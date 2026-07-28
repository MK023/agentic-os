"""Zero-dependency Sentry envelope client.

Port of marcobellingeri.dev/engine/lib/sentry.mjs, same fail-open contract:
without a DSN it is a no-op, and a failed delivery never raises past this module
— an error report must not add damage to a request that is already failing.
"""

import json
import os
import re
import time
import uuid

import httpx

_DSN_RE = re.compile(r"^https://([a-f0-9]+)@([^/]+)/(\d+)$")


def _endpoint(dsn: str | None) -> str | None:
    if not dsn:
        return None
    match = _DSN_RE.match(dsn)
    return f"https://{match.group(2)}/api/{match.group(3)}/envelope/" if match else None


async def capture_exception(exc: Exception, *, tags: dict | None = None) -> None:
    dsn = os.environ.get("SENTRY_DSN")
    url = _endpoint(dsn)
    if not url:
        return

    event_id = uuid.uuid4().hex
    event = {
        "event_id": event_id,
        "timestamp": time.time(),
        "platform": "python",
        "level": "error",
        "environment": "public-status-api",
        "tags": tags or {},
        "exception": {"values": [{"type": type(exc).__name__, "value": str(exc)}]},
    }
    envelope = "\n".join(
        json.dumps(part)
        for part in (
            # gmtime, not localtime: the trailing "Z" claims UTC.
            {"event_id": event_id, "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "dsn": dsn},
            {"type": "event"},
            event,
        )
    )
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                url,
                content=envelope,
                headers={"Content-Type": "application/x-sentry-envelope"},
                timeout=3.0,
            )
    except Exception:  # noqa: BLE001 — fail-open is the whole point of this module
        pass
