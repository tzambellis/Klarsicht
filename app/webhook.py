import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

import requests as http_requests

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ValidationError

from app.agent.rca_agent import run_investigation
from app.config import settings
from app.models.alert import Alert, GrafanaWebhookPayload
from app.models.rca import RCAResult

logger = logging.getLogger(__name__)

# In-memory fallback when no database_url is configured
_memory_store: dict[str, RCAResult | None] = {}
_use_db = bool(settings.database_url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _use_db:
        from app.db import close_db, init_db
        await init_db()
    yield
    if _use_db:
        from app.db import close_db
        await close_db()


app = FastAPI(title="Klarsicht", version="0.1.0", lifespan=lifespan)


def verify_hmac_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _notify(result: RCAResult) -> None:
    """Send RCA result to all configured notification channels."""
    url = settings.dashboard_url
    if settings.teams_webhook_url:
        from app.tools.teams import post_rca_to_teams
        post_rca_to_teams(result, dashboard_url=url)
    if settings.slack_webhook_url:
        from app.tools.slack import post_rca_to_slack
        post_rca_to_slack(result, dashboard_url=url)
    if settings.discord_webhook_url:
        from app.tools.discord import post_rca_to_discord
        post_rca_to_discord(result, dashboard_url=url)


async def _run_and_store(incident_id: UUID, alert: Alert) -> None:
    """Run RCA investigation in the background and store the result."""
    try:
        result = await run_investigation(incident_id, alert)
        if _use_db:
            from app.db import save_rca_result
            await save_rca_result(incident_id, result)
        else:
            _memory_store[str(incident_id)] = result

        # Post notifications
        _notify(result)

    except Exception:
        logger.exception("Investigation failed for incident %s", incident_id)
        if _use_db:
            from app.db import mark_incident_failed
            await mark_incident_failed(incident_id)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/alert")
async def receive_alert(
    request: Request,
    x_grafana_alerting_signature: str | None = Header(default=None),
):
    body = await request.body()

    if settings.webhook_secret:
        if not x_grafana_alerting_signature:
            raise HTTPException(status_code=401, detail="Missing signature header")
        if not verify_hmac_signature(body, x_grafana_alerting_signature, settings.webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = GrafanaWebhookPayload.model_validate_json(body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    incident_ids = []
    for alert in payload.alerts:
        if alert.status != "firing":
            continue

        incident_id = uuid4()
        incident_ids.append(str(incident_id))

        logger.info(
            "Received alert %s for %s/%s — incident %s",
            alert.labels.get("alertname", "unknown"),
            alert.labels.get("namespace", "unknown"),
            alert.labels.get("pod", "unknown"),
            incident_id,
        )

        if _use_db:
            from app.db import create_incident
            await create_incident(
                incident_id,
                alert.labels.get("alertname", "unknown"),
                alert.labels.get("namespace", "unknown"),
                alert.labels.get("pod", "unknown"),
                alert.startsAt,
            )
        else:
            _memory_store[str(incident_id)] = None

        asyncio.create_task(_run_and_store(incident_id, alert))

    return {
        "status": "accepted",
        "incidents": incident_ids,
        "alerts_received": len(payload.alerts),
        "alerts_firing": len(incident_ids),
    }


@app.post("/test")
async def send_test_alert(request: Request):
    """Send a mock CrashLoopBackOff alert through the full pipeline."""
    now = datetime.now(timezone.utc).isoformat()
    mock_payload = GrafanaWebhookPayload(
        receiver="klarsicht-test",
        status="firing",
        alerts=[
            Alert(
                status="firing",
                labels={
                    "alertname": "CrashLoopBackOff",
                    "namespace": "demo",
                    "pod": "test-crashloop-" + uuid4().hex[:5],
                    "severity": "critical",
                },
                annotations={
                    "summary": "Pod is crash looping — missing DATABASE_URL environment variable",
                    "description": "Container exited with code 1: DATABASE_URL environment variable is not set. "
                    "Deploy examples/test-crashloop.yaml to reproduce this scenario.",
                },
                startsAt=datetime.now(timezone.utc),
                fingerprint="test-" + uuid4().hex[:8],
            )
        ],
        groupLabels={"alertname": "CrashLoopBackOff"},
        commonLabels={"namespace": "demo", "severity": "critical"},
        commonAnnotations={"summary": "Pod is crash looping — missing DATABASE_URL environment variable"},
    )

    incident_ids = []
    for alert in mock_payload.alerts:
        incident_id = uuid4()
        incident_ids.append(str(incident_id))

        logger.info("Test alert — incident %s", incident_id)

        if _use_db:
            from app.db import create_incident
            await create_incident(
                incident_id,
                alert.labels.get("alertname", "unknown"),
                alert.labels.get("namespace", "unknown"),
                alert.labels.get("pod", "unknown"),
                alert.startsAt,
            )
        else:
            _memory_store[str(incident_id)] = None

        asyncio.create_task(_run_and_store(incident_id, alert))

    return {
        "status": "accepted",
        "incidents": incident_ids,
        "alerts_received": 1,
        "alerts_firing": 1,
    }


@app.get("/stats")
async def stats_endpoint():
    """Return aggregated incident statistics for the overview dashboard."""
    if _use_db:
        from app.db import get_stats
        return await get_stats()

    # In-memory computation
    from collections import Counter
    from app.models.rca import RCAResult as _RCA

    total = len(_memory_store)
    completed = 0
    investigating = 0
    failed = 0
    alert_counter: Counter[str] = Counter()
    ns_counter: Counter[str] = Counter()
    cat_counter: Counter[str] = Counter()
    investigation_times: list[float] = []
    recent: list[dict] = []

    for iid, result in _memory_store.items():
        if result is not None:
            completed += 1
            alert_counter[result.alert_name] += 1
            ns_counter[result.namespace] += 1
            if result.root_cause and result.root_cause.category:
                cat_counter[result.root_cause.category] += 1
            delta = (result.investigated_at - result.started_at).total_seconds()
            if delta >= 0:
                investigation_times.append(delta)
            confidence = result.root_cause.confidence if result.root_cause else None
            recent.append({
                "incident_id": iid,
                "alert_name": result.alert_name,
                "namespace": result.namespace,
                "pod": result.pod,
                "status": "completed",
                "confidence": confidence,
                "started_at": result.started_at.isoformat(),
            })
        else:
            investigating += 1
            recent.append({
                "incident_id": iid,
                "alert_name": "unknown",
                "namespace": "unknown",
                "pod": "unknown",
                "status": "investigating",
                "confidence": None,
                "started_at": None,
            })

    avg_secs = round(sum(investigation_times) / len(investigation_times), 1) if investigation_times else 0.0
    top_alerts = [{"alert_name": name, "count": cnt} for name, cnt in alert_counter.most_common(10)]
    top_namespaces = [{"namespace": ns, "count": cnt} for ns, cnt in ns_counter.most_common(10)]
    category_breakdown = [{"category": cat, "count": cnt} for cat, cnt in cat_counter.most_common()]

    return {
        "total_incidents": total,
        "completed": completed,
        "investigating": investigating,
        "failed": failed,
        "avg_investigation_seconds": avg_secs,
        "top_alerts": top_alerts,
        "top_namespaces": top_namespaces,
        "recent_incidents": recent[:5],
        "category_breakdown": category_breakdown,
    }


class GrafanaSetupRequest(BaseModel):
    grafana_url: str
    token: str


@app.post("/grafana-setup")
async def grafana_setup(req: GrafanaSetupRequest):
    """Proxy Grafana API calls to avoid CORS issues. Creates contact point + notification policy."""
    url = req.grafana_url.rstrip("/")
    headers = {"Authorization": f"Bearer {req.token}", "Content-Type": "application/json"}
    results = []

    # 1. Create contact point
    cp_resp = http_requests.post(
        f"{url}/api/v1/provisioning/contact-points",
        headers=headers,
        json={
            "name": "klarsicht",
            "type": "webhook",
            "settings": {"url": "http://klarsicht-agent.klarsicht.svc:8000/alert", "httpMethod": "POST"},
        },
        timeout=10,
    )
    if cp_resp.status_code in (200, 201, 202):
        results.append("Contact point created.")
    elif cp_resp.status_code == 409:
        results.append("Contact point already exists — skipping.")
    else:
        raise HTTPException(status_code=cp_resp.status_code, detail=f"Contact point: {cp_resp.text}")

    # 2. Set notification policy
    np_resp = http_requests.put(
        f"{url}/api/v1/provisioning/policies",
        headers=headers,
        json={
            "receiver": "klarsicht",
            "group_by": ["grafana_folder", "alertname"],
            "group_wait": "30s",
            "group_interval": "5m",
            "repeat_interval": "4h",
        },
        timeout=10,
    )
    if np_resp.status_code in (200, 201, 202):
        results.append("Notification policy set.")
    else:
        raise HTTPException(status_code=np_resp.status_code, detail=f"Policy: {np_resp.text}")

    results.append("Done — Grafana is now connected to Klarsicht.")
    return {"status": "ok", "steps": results}


@app.get("/incidents")
async def list_incidents_endpoint():
    """List all incidents and their investigation status."""
    if _use_db:
        from app.db import list_incidents
        return await list_incidents()

    return {
        iid: {
            "status": "completed" if result is not None else "investigating",
            "result": result.model_dump(mode="json") if result else None,
        }
        for iid, result in _memory_store.items()
    }


@app.get("/incidents/{incident_id}")
async def get_incident_endpoint(incident_id: str):
    """Get a specific incident's RCA result."""
    if _use_db:
        from app.db import get_incident
        try:
            uid = UUID(incident_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid incident ID")
        data = await get_incident(uid)
        if data is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return data

    if incident_id not in _memory_store:
        raise HTTPException(status_code=404, detail="Incident not found")
    result = _memory_store[incident_id]
    return {
        "status": "completed" if result is not None else "investigating",
        "result": result.model_dump(mode="json") if result else None,
    }
