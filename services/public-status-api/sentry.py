"""Zero-dependency Sentry envelope client.

Port of marcobellingeri.dev/engine/lib/sentry.mjs, same fail-open contract:
without a DSN it is a no-op, and a failed delivery never raises past this module
— an error report must not add damage to a request that is already failing.
"""

import json
import os
import re
import sys
import time
import uuid

import httpx

_DSN_RE = re.compile(r"^https://([a-f0-9]+)@([^/]+)/(\d+)$")


_dsn_malformato_segnalato = False


def _endpoint(dsn: str | None) -> str | None:
    """DSN -> envelope endpoint, or None if there is nothing usable.

    No DSN is a deliberate no-op. A DSN that is *set but unparseable* is not: it
    means someone configured error reporting and believes it works. Fail-open is
    the right contract for a failed delivery, not for a configuration mistake —
    so that case says so on stderr, once, instead of disappearing.
    """
    global _dsn_malformato_segnalato
    if not dsn:
        return None
    match = _DSN_RE.match(dsn)
    if not match:
        if not _dsn_malformato_segnalato:
            _dsn_malformato_segnalato = True
            # Never print the DSN itself: it is an ingest key, and logs travel.
            print(
                "sentry: SENTRY_DSN is set but does not parse as https://<key>@<host>/<project>"
                " — error reporting is OFF",
                file=sys.stderr,
            )
        return None
    return f"https://{match.group(2)}/api/{match.group(3)}/envelope/"


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
    # Which deploy an error came from, with no step anyone has to remember.
    # RAILWAY_GIT_COMMIT_SHA is injected by the platform on every deployment
    # (Railway, "Runtime variables"), so it cannot drift from what is actually
    # running the way a hand-bumped version string does — and a release that has
    # to be updated by hand is a release that will be wrong. Sentry accepts a
    # commit SHA as a release name and needs no API call to create it first.
    #
    # Absent means omitted, not empty: locally and under `docker compose` the
    # variable does not exist, and an empty release would file those errors under
    # a version that does not exist rather than under none.
    release = os.environ.get("RAILWAY_GIT_COMMIT_SHA")
    if release:
        event["release"] = release
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
