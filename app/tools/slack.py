"""Slack notification via Incoming Webhook."""

from __future__ import annotations

import logging

import requests

from app.config import settings
from app.models.rca import RCAResult

logger = logging.getLogger(__name__)


def post_rca_to_slack(rca: RCAResult, dashboard_url: str = "") -> bool:
    """Post an RCA result to Slack as a Block Kit message."""
    if not settings.slack_webhook_url:
        return False

    rc = rca.root_cause
    confidence = f"{rc.confidence * 100:.0f}%" if rc else "N/A"
    category = rc.category if rc else "unknown"
    summary = rc.summary if rc else "Investigation completed"
    emoji = ":white_check_mark:" if rc and rc.confidence >= 0.8 else ":warning:"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔍 {rca.alert_name}", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Namespace:* `{rca.namespace}`"},
                {"type": "mrkdwn", "text": f"*Pod:* `{rca.pod}`"},
                {"type": "mrkdwn", "text": f"*Confidence:* {emoji} {confidence}"},
                {"type": "mrkdwn", "text": f"*Category:* {category}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Root Cause*\n{summary}"},
        },
    ]

    # Evidence
    if rc and rc.evidence:
        evidence_text = "\n".join(f"• {e}" for e in rc.evidence[:5])
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Evidence*\n{evidence_text}"},
        })

    # Fix steps
    if rca.fix_steps:
        steps_text = "\n".join(
            f"{s.order}. {s.description}" + (f"\n```{s.command}```" if s.command else "")
            for s in rca.fix_steps[:5]
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Fix Steps*\n{steps_text}"},
        })

    # Dashboard link
    if dashboard_url:
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View in Dashboard"},
                    "url": f"{dashboard_url}/app/incidents/{rca.incident_id}",
                }
            ],
        })

    payload = {"blocks": blocks, "text": f"RCA: {rca.alert_name} — {summary}"}

    try:
        resp = requests.post(settings.slack_webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("RCA posted to Slack for incident %s", rca.incident_id)
        return True
    except requests.RequestException as e:
        logger.error("Failed to post to Slack: %s", e)
        return False
