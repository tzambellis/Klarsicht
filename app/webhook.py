import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

import requests as http_requests

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from app.agent.rca_agent import run_investigation
from app.auth import AuthUser, can_view_incident, filter_incidents, get_current_user
from app.config import settings
from app.models.alert import Alert, GrafanaWebhookPayload
from app.models.rca import RCAResult
from app.tls import apply_tls_settings

apply_tls_settings()

logger = logging.getLogger(__name__)

# In-memory fallback when no database_url is configured
_memory_store: dict[str, RCAResult | None] = {}
_memory_labels: dict[str, dict[str, str]] = {}
_memory_errors: dict[str, str] = {}
_memory_alerts: dict[str, Alert] = {}
_use_db = bool(settings.database_url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _use_db and not settings.is_agent:
        from app.db import close_db, init_db
        await init_db()
        # Initialize service catalog tables if Confluence is configured
        if settings.confluence_url:
            from app.catalog import init_catalog_schema
            await init_catalog_schema()

    # Agent mode: register with backend on startup
    if settings.is_agent:
        from app.agent_startup import register_with_backend
        register_with_backend()

    yield

    if _use_db and not settings.is_agent:
        from app.db import close_db
        await close_db()


app = FastAPI(title="Klarsicht", version="0.1.0", lifespan=lifespan)

# Mount cluster API (join endpoint on backend, K8s tools on agent)
from app.cluster_api import router as cluster_router
app.include_router(cluster_router)


def verify_hmac_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_basic_auth(authorization: str | None) -> bool:
    """Verify HTTP Basic Auth header against configured webhook credentials."""
    expected_user = settings.webhook_basic_auth_user
    expected_pass = settings.webhook_basic_auth_password
    if not expected_user:
        return True  # not configured
    if not authorization or not authorization.lower().startswith("basic "):
        return False
    import base64
    try:
        decoded = base64.b64decode(authorization[6:]).decode()
        user, _, password = decoded.partition(":")
    except Exception:
        return False
    return hmac.compare_digest(user, expected_user) and hmac.compare_digest(password, expected_pass)


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

    except Exception as e:
        import traceback
        error_message = f"{type(e).__name__}: {e}"
        full_trace = traceback.format_exc()
        logger.exception("Investigation failed for incident %s", incident_id)

        # Mark the live progress stream as failed so the detail view stops polling
        # and can surface the error to the user. The full traceback goes in `detail`
        # so the dashboard can show it under the failure step.
        from app.steps import get_progress
        progress = get_progress(str(incident_id))
        progress.add_step("Investigation failed", full_trace, status="error")
        progress.complete("failed")

        if _use_db:
            from app.db import mark_incident_failed
            await mark_incident_failed(incident_id, error_message)
        else:
            _memory_errors[str(incident_id)] = error_message

    # Persist the execution trace so it's still visible after the in-memory
    # progress is cleaned up (or the pod restarts).
    if _use_db:
        try:
            from app.db import save_incident_steps
            from app.steps import get_progress
            await save_incident_steps(incident_id, get_progress(str(incident_id)).to_dict()["steps"])
        except Exception:
            logger.exception("Failed to persist execution trace for incident %s", incident_id)


@app.get("/auth/config")
async def auth_config():
    """Public OIDC config the dashboard reads at runtime."""
    return {
        "enabled": settings.auth_enabled,
        "issuer_url": settings.oidc_issuer_url,
        "client_id": settings.oidc_client_id,
        "scopes": settings.oidc_scopes,
        # When client_secret is set, the backend handles the OAuth flow (BFF mode)
        # When empty, the SPA does PKCE itself
        "bff_mode": bool(settings.oidc_client_secret),
    }


def _public_url(request: Request, path: str) -> str:
    """Build a fully-qualified public URL.

    Prefers the configured dashboard_url (canonical, never wrong) and falls
    back to parsing X-Forwarded-* headers when not configured.
    """
    if settings.dashboard_url:
        return settings.dashboard_url.rstrip("/") + path
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{scheme}://{host}{path}"


@app.get("/auth/login")
async def auth_login(request: Request):
    """Start the OIDC login flow (BFF mode, server-side)."""
    if not settings.auth_enabled:
        raise HTTPException(status_code=400, detail="Auth not enabled")

    import secrets
    from urllib.parse import urlencode
    from fastapi.responses import RedirectResponse
    from app.auth import get_oidc_config

    config = get_oidc_config()
    state = secrets.token_urlsafe(32)
    redirect_uri = _public_url(request, "/oauth2/callback")

    params = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "scope": settings.oidc_scopes,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    auth_url = f"{config['authorization_endpoint']}?{urlencode(params)}"

    response = RedirectResponse(auth_url, status_code=302)
    response.set_cookie("oidc_state", state, httponly=True, secure=True, samesite="lax", max_age=600)
    response.set_cookie("oidc_redirect", redirect_uri, httponly=True, secure=True, samesite="lax", max_age=600)
    return response


@app.get("/oauth2/callback")
async def oauth2_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """OIDC callback — exchange code for token, set session cookie, redirect home."""
    from fastapi.responses import RedirectResponse
    from app.auth import get_oidc_config, decode_token, sign_session, SESSION_COOKIE, SESSION_TTL

    if error:
        raise HTTPException(status_code=400, detail=f"OIDC error: {error}")

    saved_state = request.cookies.get("oidc_state")
    if not saved_state or saved_state != state:
        raise HTTPException(status_code=400, detail="Invalid state")

    redirect_uri = request.cookies.get("oidc_redirect") or _public_url(request, "/oauth2/callback")
    config = get_oidc_config()

    # Exchange code for tokens — use HTTP Basic auth (client_secret_basic),
    # which is the most common default required by OIDC providers like Ping.
    token_resp = http_requests.post(
        config["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        auth=(settings.oidc_client_id, settings.oidc_client_secret),
        timeout=10,
    )
    if token_resp.status_code != 200:
        logger.error("Token exchange failed: %s %s", token_resp.status_code, token_resp.text)
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_resp.text}")

    tokens = token_resp.json()
    id_token = tokens.get("id_token")
    access_token = tokens.get("access_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="No id_token in response")

    # Validate the ID token signature (and at_hash against access_token if present)
    try:
        claims = decode_token(id_token, access_token=access_token)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid id_token: {e}")

    session_jwt = sign_session(claims)
    home_url = _public_url(request, "/")

    response = RedirectResponse(home_url, status_code=302)
    response.set_cookie(
        SESSION_COOKIE, session_jwt,
        httponly=True, secure=True, samesite="lax", max_age=SESSION_TTL,
    )
    response.delete_cookie("oidc_state")
    response.delete_cookie("oidc_redirect")
    return response


@app.get("/auth/me")
async def auth_me(request: Request):
    """Return the current user from the session cookie."""
    from app.auth import verify_session, resolve_user, SESSION_COOKIE
    if not settings.auth_enabled:
        return {"authenticated": True, "auth_disabled": True, "is_admin": True}

    session_token = request.cookies.get(SESSION_COOKIE)
    if not session_token:
        return {"authenticated": False}
    claims = verify_session(session_token)
    if claims is None:
        return {"authenticated": False}

    user = resolve_user(claims)
    return {
        "authenticated": True,
        "sub": claims.get("sub"),
        "email": claims.get("email"),
        "name": claims.get("name"),
        "is_admin": user.is_admin,
        "allowed_labels": user.allowed_label_values,
    }


@app.post("/auth/logout")
async def auth_logout():
    """Clear the session cookie."""
    from fastapi.responses import JSONResponse
    from app.auth import SESSION_COOKIE
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


def _require_admin(user: AuthUser | None) -> None:
    """Raise 403 if the user is not an admin (or auth disabled = always allowed)."""
    if not settings.auth_enabled:
        return
    if user is None or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


@app.get("/admin/config")
async def admin_config(user: AuthUser | None = Depends(get_current_user)):
    """Return the (sanitized) running config — admin only."""
    _require_admin(user)
    return {
        "mode": settings.mode,
        "cluster_name": settings.cluster_name,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_profile": settings.llm_profile,
        "llm_max_tool_calls": settings.llm_max_tool_calls,
        "watch_namespaces": settings.watch_namespace_list,
        "mimir_endpoint": settings.mimir_endpoint,
        "database_url": _redact_url(settings.database_url),
        "auth_enabled": settings.auth_enabled,
        "oidc_issuer_url": settings.oidc_issuer_url,
        "oidc_client_id": settings.oidc_client_id,
        "oidc_scopes": settings.oidc_scopes,
        "auth_claim_mapping": settings.auth_claim_mapping,
        "auth_team_mappings": settings.auth_team_mappings,
        "auth_admin_teams": settings.auth_admin_teams,
        "confluence_url": settings.confluence_url,
        "confluence_spaces": settings.confluence_space_list,
        "gitlab_url": settings.gitlab_url,
        "gitlab_project": settings.gitlab_project,
        "teams_webhook_configured": bool(settings.teams_webhook_url),
        "slack_webhook_configured": bool(settings.slack_webhook_url),
        "discord_webhook_configured": bool(settings.discord_webhook_url),
        "dashboard_url": settings.dashboard_url,
    }


def _redact_url(url: str) -> str:
    """Strip credentials from a connection URL for safe display."""
    if not url:
        return ""
    import re
    return re.sub(r"://[^:]+:[^@]+@", "://***:***@", url)


@app.get("/readyz")
async def readyz():
    """Readiness probe — checks all configured services and reports status."""
    services: list[dict] = []

    # Database
    if _use_db:
        try:
            from app.db import _get_pool
            pool = _get_pool()
            await pool.fetchval("SELECT 1")
            services.append({"name": "postgres", "status": "ok"})
        except Exception as e:
            services.append({"name": "postgres", "status": "error", "error": str(e)})
    else:
        services.append({"name": "postgres", "status": "disabled"})

    # LLM provider — check that we can build the client
    try:
        from app.agent.rca_agent import _build_llm
        _build_llm()
        services.append({"name": f"llm:{settings.llm_provider}", "status": "ok"})
    except Exception as e:
        services.append({"name": f"llm:{settings.llm_provider}", "status": "error", "error": str(e)})

    # K8s API (only when this instance has K8s access)
    if not settings.is_backend:
        try:
            from app.tools.k8s import _v1
            _v1().list_namespace(limit=1, _request_timeout=3)
            services.append({"name": "kubernetes", "status": "ok"})
        except Exception as e:
            services.append({"name": "kubernetes", "status": "error", "error": str(e)})

    # Mimir / Prometheus
    if settings.mimir_endpoint:
        try:
            r = http_requests.get(settings.mimir_endpoint.rstrip("/") + "/api/v1/query", params={"query": "up"}, timeout=3)
            services.append({"name": "mimir", "status": "ok" if r.ok else "error",
                             **({"error": f"HTTP {r.status_code}"} if not r.ok else {})})
        except Exception as e:
            services.append({"name": "mimir", "status": "error", "error": str(e)})

    # OIDC issuer
    if settings.auth_enabled and settings.oidc_issuer_url:
        try:
            r = http_requests.get(
                settings.oidc_issuer_url.rstrip("/") + "/.well-known/openid-configuration",
                timeout=3,
            )
            services.append({"name": "oidc", "status": "ok" if r.ok else "error",
                             **({"error": f"HTTP {r.status_code}"} if not r.ok else {})})
        except Exception as e:
            services.append({"name": "oidc", "status": "error", "error": str(e)})

    # Confluence
    if settings.confluence_url:
        try:
            from app.tools.confluence import _base_url, _headers, _auth
            r = http_requests.get(
                f"{_base_url()}/rest/api/space",
                headers=_headers(), auth=_auth(), timeout=3, params={"limit": 1},
            )
            services.append({"name": "confluence", "status": "ok" if r.ok else "error",
                             **({"error": f"HTTP {r.status_code}"} if not r.ok else {})})
        except Exception as e:
            services.append({"name": "confluence", "status": "error", "error": str(e)})

    # GitLab
    if settings.gitlab_url and settings.gitlab_token:
        try:
            r = http_requests.get(
                f"{settings.gitlab_url.rstrip('/')}/api/v4/version",
                headers={"PRIVATE-TOKEN": settings.gitlab_token}, timeout=3,
            )
            services.append({"name": "gitlab", "status": "ok" if r.ok else "error",
                             **({"error": f"HTTP {r.status_code}"} if not r.ok else {})})
        except Exception as e:
            services.append({"name": "gitlab", "status": "error", "error": str(e)})

    overall = "ok" if all(s["status"] in ("ok", "disabled") for s in services) else "degraded"
    status_code = 200 if overall == "ok" else 503
    from fastapi.responses import JSONResponse
    return JSONResponse({"status": overall, "services": services}, status_code=status_code)


@app.post("/alert")
async def receive_alert(
    request: Request,
    x_grafana_alerting_signature: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    body = await request.body()

    if settings.webhook_secret:
        if not x_grafana_alerting_signature:
            raise HTTPException(status_code=401, detail="Missing signature header")
        if not verify_hmac_signature(body, x_grafana_alerting_signature, settings.webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid signature")

    if settings.webhook_basic_auth_user:
        if not verify_webhook_basic_auth(authorization):
            raise HTTPException(
                status_code=401,
                detail="Invalid webhook credentials",
                headers={"WWW-Authenticate": 'Basic realm="Klarsicht webhook"'},
            )

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
                labels=alert.labels,
                alert_payload=alert.model_dump(mode="json"),
            )
        else:
            _memory_store[str(incident_id)] = None
            _memory_labels[str(incident_id)] = alert.labels
            _memory_alerts[str(incident_id)] = alert

        asyncio.create_task(_run_and_store(incident_id, alert))

    # Fan-out: forward raw payload to peer instances
    if settings.peer_url_list:
        _forward_to_peers(body)

    return {
        "status": "accepted",
        "incidents": incident_ids,
        "alerts_received": len(payload.alerts),
        "alerts_firing": len(incident_ids),
    }


def _forward_to_peers(body: bytes) -> None:
    """Forward alert payload to all configured peer instances (fire-and-forget)."""
    for base_url in settings.peer_url_list:
        url = base_url.rstrip("/") + "/alert"
        try:
            http_requests.post(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                timeout=5,
            )
            logger.info("Forwarded alert to peer %s", url)
        except Exception:
            logger.warning("Failed to forward alert to peer %s", url, exc_info=True)


@app.post("/test")
async def send_test_alert(request: Request, user: AuthUser | None = Depends(get_current_user)):
    """Send a mock CrashLoopBackOff alert through the full pipeline.

    When auth is enabled, injects the calling user's allowed labels
    into the test alert so the user can actually see the result.
    """
    # Inject user's labels so they pass the auth filter
    extra_labels: dict[str, str] = {}
    if user and not user.is_admin:
        for label_key, allowed_values in user.allowed_label_values.items():
            if allowed_values:
                extra_labels[label_key] = allowed_values[0]

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
                    **extra_labels,
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
                labels=alert.labels,
                alert_payload=alert.model_dump(mode="json"),
            )
        else:
            _memory_store[str(incident_id)] = None
            _memory_labels[str(incident_id)] = alert.labels
            _memory_alerts[str(incident_id)] = alert

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
            if iid in _memory_errors:
                failed += 1
                status_value = "failed"
            else:
                investigating += 1
                status_value = "investigating"
            recent.append({
                "incident_id": iid,
                "alert_name": "unknown",
                "namespace": "unknown",
                "pod": "unknown",
                "status": status_value,
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
async def list_incidents_endpoint(user: AuthUser | None = Depends(get_current_user)):
    """List all incidents and their investigation status."""
    if _use_db:
        from app.db import list_incidents
        incidents = await list_incidents()
    else:
        incidents = {
            iid: {
                "status": (
                    "completed" if result is not None
                    else "failed" if iid in _memory_errors
                    else "investigating"
                ),
                "result": result.model_dump(mode="json") if result else None,
                "labels": _memory_labels.get(iid, {}),
                "error": _memory_errors.get(iid),
            }
            for iid, result in _memory_store.items()
        }

    return filter_incidents(incidents, user)


@app.get("/incidents/{incident_id}")
async def get_incident_endpoint(incident_id: str, user: AuthUser | None = Depends(get_current_user)):
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
        if not can_view_incident(data, user):
            raise HTTPException(status_code=403, detail="Access denied")
        return data

    if incident_id not in _memory_store:
        raise HTTPException(status_code=404, detail="Incident not found")
    result = _memory_store[incident_id]
    data = {
        "status": (
            "completed" if result is not None
            else "failed" if incident_id in _memory_errors
            else "investigating"
        ),
        "result": result.model_dump(mode="json") if result else None,
        "labels": _memory_labels.get(incident_id, {}),
        "error": _memory_errors.get(incident_id),
    }
    if not can_view_incident(data, user):
        raise HTTPException(status_code=403, detail="Access denied")
    return data


@app.get("/incidents/{incident_id}/steps")
async def get_incident_steps(incident_id: str):
    """Return the live (in-memory) trace, or fall back to the persisted one for finished incidents."""
    from app.steps import get_progress, _progress as _live_progress
    if incident_id in _live_progress:
        return _live_progress[incident_id].to_dict()

    if _use_db:
        try:
            from app.db import get_incident_steps as db_get_steps
            steps = await db_get_steps(UUID(incident_id))
            if steps is not None:
                # Status reflects the incident's terminal state — UI doesn't poll for these
                return {"status": "completed", "steps": steps}
        except (ValueError, Exception):
            pass

    # Fall back to creating an empty progress object (matches old behaviour)
    return get_progress(incident_id).to_dict()


class ManualInvestigationRequest(BaseModel):
    namespace: str
    pod: str
    context: str = ""             # free-form: what the user knows about the app
    severity: str = "info"


@app.post("/investigate")
async def manual_investigate(
    req: ManualInvestigationRequest,
    user: AuthUser | None = Depends(get_current_user),
):
    """Trigger an RCA investigation manually for a chosen pod, with optional user-supplied context.

    Useful when Grafana isn't wired up yet, or for demos. Synthesises an Alert and feeds
    it through the same _run_and_store flow as a real webhook.
    """
    if not req.namespace or not req.pod:
        raise HTTPException(status_code=400, detail="namespace and pod are required")

    labels = {
        "alertname": "ManualInvestigation",
        "namespace": req.namespace,
        "pod": req.pod,
        "severity": req.severity or "info",
    }

    if user and not can_view_incident({"labels": labels}, user):
        raise HTTPException(status_code=403, detail="Access denied")

    incident_id = uuid4()
    now = datetime.now(timezone.utc)
    description = req.context.strip() or f"User requested investigation of pod {req.pod} in namespace {req.namespace}."

    alert = Alert(
        status="firing",
        labels=labels,
        annotations={
            "summary": f"Manual investigation: {req.namespace}/{req.pod}",
            "description": description,
        },
        startsAt=now,
        fingerprint=f"manual-{incident_id.hex[:8]}",
    )

    logger.info("Manual investigation %s for %s/%s", incident_id, req.namespace, req.pod)

    if _use_db:
        from app.db import create_incident
        await create_incident(
            incident_id,
            "ManualInvestigation",
            req.namespace,
            req.pod,
            now,
            labels=labels,
            alert_payload=alert.model_dump(mode="json"),
        )
    else:
        _memory_store[str(incident_id)] = None
        _memory_labels[str(incident_id)] = labels
        _memory_alerts[str(incident_id)] = alert

    asyncio.create_task(_run_and_store(incident_id, alert))
    return {"incident_id": str(incident_id)}


@app.post("/incidents/{incident_id}/retry")
async def retry_incident(incident_id: str, user: AuthUser | None = Depends(get_current_user)):
    """Re-run the investigation for a finished (or failed) incident."""
    try:
        iid = UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid incident id")

    # Recover the original Alert payload
    alert: Alert | None = None
    if _use_db:
        from app.db import get_alert_payload, reset_incident_for_retry
        payload = await get_alert_payload(iid)
        if payload is None:
            raise HTTPException(status_code=404, detail="No stored alert payload — incident pre-dates retry support")
        try:
            alert = Alert.model_validate(payload)
        except ValidationError as e:
            raise HTTPException(status_code=500, detail=f"Stored alert payload is invalid: {e}")
        await reset_incident_for_retry(iid)
    else:
        alert = _memory_alerts.get(incident_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="No stored alert payload for this incident")
        _memory_store[incident_id] = None
        _memory_errors.pop(incident_id, None)

    # Authorization: same rule as viewing the incident
    if user and not can_view_incident({"labels": alert.labels}, user):
        raise HTTPException(status_code=403, detail="Access denied")

    # Reset the live step stream so the dashboard sees a clean run
    from app.steps import _progress as _live_progress
    _live_progress.pop(incident_id, None)

    asyncio.create_task(_run_and_store(iid, alert))
    return {"status": "retrying", "incident_id": incident_id}


@app.get("/incidents/{incident_id}/stream")
async def stream_incident_steps(incident_id: str):
    """SSE stream of investigation steps. Sends events as steps are added."""
    from app.steps import get_progress

    async def event_generator():
        progress = get_progress(incident_id)
        sent = 0
        while True:
            steps = progress.to_dict()
            # Send any new steps
            if len(steps["steps"]) > sent:
                for step in steps["steps"][sent:]:
                    yield f"data: {json.dumps(step)}\n\n"
                sent = len(steps["steps"])

            if progress.status in ("completed", "failed"):
                yield f"data: {json.dumps({'event': 'done', 'status': progress.status})}\n\n"
                break

            await progress.wait_for_update(timeout=30)
            # Send keepalive
            yield ": keepalive\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/compare")
async def compare_instances():
    """Compare performance and results across this instance and all peers."""
    instances = []

    # Collect own stats
    own_stats = await stats_endpoint()
    instances.append({
        "instance": settings.dashboard_url or "self",
        "model": f"{settings.llm_provider}/{settings.llm_model or 'default'}",
        "stats": own_stats,
    })

    # Collect peer stats
    for base_url in settings.peer_url_list:
        url = base_url.rstrip("/")
        try:
            resp = http_requests.get(f"{url}/stats", timeout=10)
            resp.raise_for_status()
            peer_stats = resp.json()
            instances.append({
                "instance": base_url,
                "stats": peer_stats,
            })
        except Exception:
            logger.warning("Failed to fetch stats from peer %s", base_url, exc_info=True)
            instances.append({
                "instance": base_url,
                "stats": None,
                "error": "unreachable",
            })

    # Build comparison summary
    summary = []
    for inst in instances:
        s = inst.get("stats")
        if s:
            summary.append({
                "instance": inst["instance"],
                "model": inst.get("model", "unknown"),
                "total_incidents": s.get("total_incidents", 0),
                "completed": s.get("completed", 0),
                "failed": s.get("failed", 0),
                "avg_investigation_seconds": s.get("avg_investigation_seconds", 0),
                "category_breakdown": s.get("category_breakdown", []),
            })

    return {"instances": instances, "summary": summary}


# --- Service Catalog / Confluence Sync ---


@app.post("/catalog/sync")
async def catalog_sync():
    """Sync service catalog: crawl Confluence BHBs and K8s deployments."""
    if not _use_db:
        raise HTTPException(status_code=400, detail="Database required for service catalog")

    results = {}

    if settings.confluence_url:
        from app.catalog import sync_confluence
        results["confluence"] = await sync_confluence()

    if not settings.is_backend:
        try:
            from app.catalog import sync_k8s_deployments
            results["k8s"] = await sync_k8s_deployments()
        except Exception as e:
            results["k8s"] = {"error": str(e)}

    return {"status": "ok", **results}


@app.post("/catalog/match")
async def catalog_match():
    """Run LLM-based fuzzy matching of deployments to BHB pages."""
    if not _use_db:
        raise HTTPException(status_code=400, detail="Database required for service catalog")
    if not settings.confluence_url:
        raise HTTPException(status_code=400, detail="Confluence not configured")

    from app.catalog import bootstrap_llm_matching
    result = await bootstrap_llm_matching()
    return result


@app.get("/catalog")
async def catalog_list():
    """List all services in the catalog with their BHB matches."""
    if not _use_db:
        raise HTTPException(status_code=400, detail="Database required for service catalog")

    from app.db import _get_pool
    import json
    pool = _get_pool()
    rows = await pool.fetch(
        "SELECT name, namespace, cluster, team, tech, dependencies, bhb_title, match_confidence FROM service_catalog ORDER BY name"
    )
    return [
        {
            "name": r["name"],
            "namespace": r["namespace"],
            "cluster": r["cluster"],
            "team": r["team"],
            "tech": r["tech"],
            "dependencies": json.loads(r["dependencies"]) if r["dependencies"] else [],
            "bhb_title": r["bhb_title"],
            "match_confidence": r["match_confidence"],
        }
        for r in rows
    ]
