#!/usr/bin/env python3
"""LinkHarbor agent for Kick channels.

Polls the official Kick API for one channel (OAuth client credentials — an
app from kick.com → Settings → Developer) and pushes the state to a
LinkHarbor "Custom (API)" integration: a status card on your bio page with
Live/Offline, the viewer count, the stream title and category, a "Watch on
Kick" button, and events for push notifications when a stream starts.

    export LH_INGEST_URL="https://dock.at-harbor.com/v1/ingest/123"
    export LH_INGEST_SECRET="…"
    export KICK_CLIENT_ID="…"
    export KICK_CLIENT_SECRET="…"
    python3 agent.py --channel xqc

No third-party packages needed (Python 3.10+, standard library only).

Events sent (each can drive a push rule in the dashboard):
    stream.started   the channel went live (data.title, data.category)
    stream.stopped   the channel went offline
    title.changed    the stream title changed while live (data.title)

On start the current state is sent without an event, so restarting the
agent does not notify followers again.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

log = logging.getLogger("linkharbor")

UA = "LinkHarbor-Kick/1.0"
TOKEN_URL = "https://id.kick.com/oauth/token"
CHANNELS_URL = "https://api.kick.com/public/v1/channels"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channel", required=True, help="channel slug (kick.com/<slug>)")
    p.add_argument("--client-id", default=os.environ.get("KICK_CLIENT_ID", ""), help="Kick app client id (env KICK_CLIENT_ID)")
    p.add_argument("--client-secret", default=os.environ.get("KICK_CLIENT_SECRET", ""), help="Kick app client secret (env KICK_CLIENT_SECRET)")
    p.add_argument("--name", default="", help="card title (default: the channel slug)")
    p.add_argument("--address", default="", help="text shown under the title instead of the stream title (optional)")
    p.add_argument("--url", default="", help="link behind the card's button (default: the channel page)")
    p.add_argument("--cta", default="Watch on Kick", help="button label")
    p.add_argument("--image-url", default="", help="banner image for the large layout (default: the channel banner)")
    p.add_argument("--interval", type=int, default=60, help="seconds between polls (default 60)")
    p.add_argument("--heartbeat", type=int, default=10, help="minutes between state-only updates (0 = only on change)")
    p.add_argument("--ingest-url", default=os.environ.get("LH_INGEST_URL", ""))
    p.add_argument("--ingest-secret", default=os.environ.get("LH_INGEST_SECRET", ""))
    p.add_argument("--once", action="store_true", help="poll once, send, exit")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    if not a.client_id or not a.client_secret:
        p.error("set KICK_CLIENT_ID and KICK_CLIENT_SECRET (or --client-id / --client-secret)")
    if not a.ingest_url or not a.ingest_secret:
        p.error("set LH_INGEST_URL and LH_INGEST_SECRET (or --ingest-url / --ingest-secret)")
    a.channel = a.channel.strip().lstrip("@").lower()
    return a


class Ingest:
    def __init__(self, url: str, secret: str) -> None:
        self.url, self.secret = url, secret

    def send(self, event: str | None, data: dict | None, state: dict) -> bool:
        body: dict = {"state": state}
        if event:
            body["event"] = event
            body["event_id"] = f"{event}-{uuid.uuid4()}"
        if data:
            body["data"] = data
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.secret}", "Content-Type": "application/json",
                     "User-Agent": UA},
        )
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=10) as res:
                    log.debug("← %s %s", res.status, res.read(200).decode(errors="replace"))
                    return True
            except urllib.error.HTTPError as e:
                text = e.read(300).decode(errors="replace")
                if e.code == 401:
                    log.error("401 from LinkHarbor — wrong secret or integration disconnected")
                    return False
                if e.code < 500:
                    log.error("%s from LinkHarbor: %s", e.code, text)
                    return False
                log.warning("%s from LinkHarbor (attempt %d)", e.code, attempt)
            except Exception as e:  # network
                log.warning("request failed (attempt %d): %s", attempt, e)
            time.sleep(2)
        return False


class KickToken:
    """App access token (client credentials), refreshed a minute before it expires."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id, self.client_secret = client_id, client_secret
        self.token = ""
        self.expires_at = 0.0

    def get(self) -> str:
        if self.token and time.time() < self.expires_at - 60:
            return self.token
        form = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()
        req = urllib.request.Request(
            TOKEN_URL, data=form, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": UA},
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            body = json.loads(res.read(64_000).decode(errors="replace"))
        self.token = str(body.get("access_token") or "")
        if not self.token:
            raise RuntimeError("token response without access_token")
        self.expires_at = time.time() + int(body.get("expires_in") or 3600)
        log.debug("new Kick token, valid %ss", int(body.get("expires_in") or 3600))
        return self.token


def fetch_channel(token: KickToken, slug: str) -> dict | None:
    """Returns the channel's live status, or None when Kick did not answer."""
    try:
        bearer = token.get()
    except urllib.error.HTTPError as e:
        log.error("Kick token request failed: %s %s", e.code, e.read(300).decode(errors="replace"))
        return None
    except Exception as e:
        log.warning("Kick token request failed: %s", e)
        return None
    req = urllib.request.Request(
        CHANNELS_URL + "?" + urllib.parse.urlencode({"slug": slug}),
        headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            data = json.loads(res.read(512_000).decode(errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            token.token = ""  # force a new token next poll
        log.warning("Kick API: %s %s", e.code, e.read(300).decode(errors="replace"))
        return None
    except Exception as e:
        log.warning("Kick API did not answer: %s", e)
        return None
    rows = data.get("data") if isinstance(data, dict) else None
    row = rows[0] if isinstance(rows, list) and rows else None
    if not isinstance(row, dict):
        log.warning("Kick API: no channel named %r", slug)
        return None
    stream = row.get("stream") if isinstance(row.get("stream"), dict) else {}
    category = row.get("category") if isinstance(row.get("category"), dict) else {}
    return {
        "slug": str(row.get("slug") or slug),
        "live": bool(stream.get("is_live", False)),
        "viewers": int(stream.get("viewer_count") or 0),
        "started_at": str(stream.get("start_time") or ""),
        "title": str(row.get("stream_title") or "").strip(),
        "category": str(category.get("name") or "").strip(),
        "banner": str(row.get("banner_picture") or ""),
    }


def build_state(a: argparse.Namespace, ch: dict) -> dict:
    text = ch["title"]
    if ch["category"]:
        text = f"{text} · {ch['category']}" if text else ch["category"]
    s: dict = {
        "type": "status",
        "title": (a.name or ch["slug"])[:80],
        "state": "online" if ch["live"] else "offline",
        "label": "Live" if ch["live"] else "Offline",
        "text": (a.address or text)[:200],
        "url": a.url or f"https://kick.com/{ch['slug']}",
        "cta": a.cta,
    }
    if ch["live"]:
        s["value"] = str(ch["viewers"])
    image = a.image_url or ch["banner"]
    if image.startswith("http"):
        s["image_url"] = image
    return s


def main() -> int:
    a = parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ingest = Ingest(a.ingest_url, a.ingest_secret)
    token = KickToken(a.client_id, a.client_secret)

    live: bool | None = None
    last_title = ""
    last_sent = 0.0
    heartbeat_s = a.heartbeat * 60

    while True:
        ch = fetch_channel(token, a.channel)
        if ch is None:
            log.warning("no answer from Kick, retrying in %ss", a.interval)
            if a.once:
                return 1
            time.sleep(max(5, a.interval))
            continue
        state = build_state(a, ch)

        events: list[tuple[str, dict | None]] = []
        if live is None:
            pass  # first poll: state only
        elif ch["live"] != live:
            if ch["live"]:
                events.append(("stream.started", {"title": ch["title"], "category": ch["category"]}))
            else:
                events.append(("stream.stopped", None))
        elif ch["live"] and ch["title"] != last_title:
            events.append(("title.changed", {"title": ch["title"], "category": ch["category"]}))

        if events:
            for ev, data in events:
                log.info("%s %s", ev, (data or {}).get("title", ""))
                ingest.send(ev, data, state)
            last_sent = time.time()
        elif live is None or (heartbeat_s and time.time() - last_sent >= heartbeat_s):
            log.info("%s: %s, %s viewers", ch["slug"], "live" if ch["live"] else "offline", ch["viewers"])
            ingest.send(None, None, state)
            last_sent = time.time()

        live = ch["live"]
        last_title = ch["title"]
        if a.once:
            return 0
        time.sleep(max(5, a.interval))


if __name__ == "__main__":
    sys.exit(main())
