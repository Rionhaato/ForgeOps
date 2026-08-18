"""Render and post ForgeOps Boardroom task dispatches."""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen


SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_REQUEST_TIMEOUT_SECONDS = 10


def render_dispatch(spec: dict) -> str:
    """Render a structured task specification in the Boardroom format."""
    lines = [
        "[FORGEOPS TASK] [CODEX DISPATCH]",
        f"ID: {spec['id']}",
        f"Title: {spec['title']}",
        f"Owner: {spec['owner']}",
        f"Reviewer: {spec['reviewer']}",
        "",
        "Objective:",
        str(spec["objective"]),
        "",
        "Authorized:",
    ]
    lines.extend(
        f"{number}. {item}" for number, item in enumerate(spec["authorized"], start=1)
    )
    lines.extend(["", "Acceptance:"])
    lines.extend(f"\u2022 {item}" for item in spec["acceptance"])
    lines.extend(["", "Forbidden / dangerous:"])
    lines.extend(f"\u2022 {item}" for item in spec["forbidden"])
    lines.extend(["", "Cleanup:"])
    lines.extend(f"\u2022 {item}" for item in spec["cleanup"])
    return "\n".join(lines)


def post_dispatch(
    text: str,
    channel_id: str = "C0BMESXCR8A",
    token_env: str = "FORGEOPS_SLACK_BOT_TOKEN",
) -> dict:
    """Post a dispatch through Slack's ``chat.postMessage`` endpoint."""
    try:
        token = os.getenv(token_env)
        if not token:
            return {"ok": False, "ts": None, "error": "missing_token"}
        payload = json.dumps({"channel": channel_id, "text": text}).encode("utf-8")
        request = Request(
            SLACK_POST_MESSAGE_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        with urlopen(request, timeout=SLACK_REQUEST_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "ts": None, "error": "invalid_response"}
    except Exception:
        return {"ok": False, "ts": None, "error": "request_failed"}
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        return {"ok": False, "ts": None, "error": "invalid_response"}

    ok = result["ok"]
    ts = result.get("ts") if ok else None
    error = result.get("error")
    if (ts is not None and not isinstance(ts, str)) or (
        error is not None and not isinstance(error, str)
    ):
        return {"ok": False, "ts": None, "error": "invalid_response"}
    return {"ok": ok, "ts": ts, "error": error}
