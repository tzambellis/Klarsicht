"""Discord notification via Webhook."""

from __future__ import annotations

import logging

import requests

from app.config import settings
from app.models.rca import RCAResult

logger = logging.getLogger(__name__)


def post_rca_to_discord(rca: RCAResult, dashboard_url: str = "") -> bool:
    """Post an RCA result to Discord as an embed."""
    if not settings.discord_webhook_url:
        return False

    rc = rca.root_cause
    confidence = f"{rc.confidence * 100:.0f}%" if rc else "N/A"
    category = rc.category if rc else "unknown"
    summary = rc.summary if rc else "Investigation completed"

    # Green if high confidence, yellow if medium, red if low
    if rc and rc.confidence >= 0.8:
        color = 0x22C55E  # green
    elif rc and rc.confidence >= 0.5:
        color = 0xF59E0B  # amber
    else:
        color = 0xEF4444  # red

    fields = [
        {"name": "Namespace", "value": f"`{rca.namespace}`", "inline": True},
        {"name": "Pod", "value": f"`{rca.pod}`", "inline": True},
        {"name": "Confidence", "value": confidence, "inline": True},
        {"name": "Category", "value": category, "inline": True},
    ]

    # Evidence
    if rc and rc.evidence:
        evidence_text = "\n".join(f"• {e[:100]}" for e in rc.evidence[:4])
        fields.append({"name": "Evidence", "value": evidence_text, "inline": False})

    # Fix steps
    if rca.fix_steps:
        steps_text = "\n".join(
            f"**{s.order}.** {s.description}" + (f"\n```{s.command}```" if s.command else "")
            for s in rca.fix_steps[:3]
        )
        fields.append({"name": "Fix Steps", "value": steps_text[:1024], "inline": False})

    embed = {
        "title": f"🔍 {rca.alert_name}",
        "description": summary,
        "color": color,
        "fields": fields,
        "footer": {"text": "Klarsicht RCA"},
        "timestamp": rca.investigated_at.isoformat(),
    }

    if dashboard_url:
        embed["url"] = f"{dashboard_url}/app/incidents/{rca.incident_id}"

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(settings.discord_webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("RCA posted to Discord for incident %s", rca.incident_id)
        return True
    except requests.RequestException as e:
        logger.error("Failed to post to Discord: %s", e)
        return False
