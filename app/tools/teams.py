"""Microsoft Teams notification via Incoming Webhook."""

from __future__ import annotations

import logging
from typing import Any

import requests

from app.config import settings
from app.models.rca import RCAResult

logger = logging.getLogger(__name__)


def post_rca_to_teams(rca: RCAResult, dashboard_url: str = "") -> bool:
    """Post an RCA result to Microsoft Teams as an Adaptive Card.

    Returns True if the message was sent successfully.
    """
    if not settings.teams_webhook_url:
        return False

    rc = rca.root_cause
    confidence = f"{rc.confidence * 100:.0f}%" if rc else "N/A"
    category = rc.category if rc else "unknown"
    summary = rc.summary if rc else "Investigation completed"

    # Build fix steps text
    fix_text = ""
    for step in rca.fix_steps[:5]:
        fix_text += f"**{step.order}.** {step.description}\n"
        if step.command:
            fix_text += f"`{step.command}`\n"

    # Build evidence text
    evidence_text = ""
    if rc and rc.evidence:
        for e in rc.evidence[:4]:
            evidence_text += f"- {e}\n"

    # Confidence color
    if rc and rc.confidence >= 0.9:
        accent = "good"
    elif rc and rc.confidence >= 0.7:
        accent = "warning"
    else:
        accent = "attention"

    card: dict[str, Any] = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "ColumnSet",
                            "columns": [
                                {
                                    "type": "Column",
                                    "width": "stretch",
                                    "items": [
                                        {
                                            "type": "TextBlock",
                                            "text": f"🔍 {rca.alert_name}",
                                            "weight": "Bolder",
                                            "size": "Large",
                                            "wrap": True,
                                        }
                                    ],
                                },
                                {
                                    "type": "Column",
                                    "width": "auto",
                                    "items": [
                                        {
                                            "type": "TextBlock",
                                            "text": confidence,
                                            "weight": "Bolder",
                                            "size": "Large",
                                            "color": accent,
                                        }
                                    ],
                                },
                            ],
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "Namespace", "value": rca.namespace},
                                {"title": "Pod", "value": rca.pod},
                                {"title": "Category", "value": category},
                                {"title": "Started", "value": rca.started_at.strftime("%Y-%m-%d %H:%M:%S UTC")},
                            ],
                        },
                        {
                            "type": "TextBlock",
                            "text": "**Root Cause**",
                            "weight": "Bolder",
                            "spacing": "Medium",
                        },
                        {
                            "type": "TextBlock",
                            "text": summary,
                            "wrap": True,
                        },
                    ],
                },
            }
        ],
    }

    body = card["attachments"][0]["content"]["body"]

    if evidence_text:
        body.append({
            "type": "TextBlock",
            "text": "**Evidence**",
            "weight": "Bolder",
            "spacing": "Medium",
        })
        body.append({
            "type": "TextBlock",
            "text": evidence_text,
            "wrap": True,
            "size": "Small",
        })

    if fix_text:
        body.append({
            "type": "TextBlock",
            "text": "**Fix Steps**",
            "weight": "Bolder",
            "spacing": "Medium",
        })
        body.append({
            "type": "TextBlock",
            "text": fix_text,
            "wrap": True,
            "size": "Small",
        })

    if dashboard_url:
        card["attachments"][0]["content"]["actions"] = [
            {
                "type": "Action.OpenUrl",
                "title": "View in Dashboard",
                "url": f"{dashboard_url}/app/incidents/{rca.incident_id}",
            }
        ]

    try:
        resp = requests.post(settings.teams_webhook_url, json=card, timeout=10)
        resp.raise_for_status()
        logger.info("RCA posted to Teams for incident %s", rca.incident_id)
        return True
    except requests.RequestException as e:
        logger.error("Failed to post to Teams: %s", e)
        return False
