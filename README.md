# LinkHarbor for Kick

A single-file Python agent that polls the official Kick API for one channel
and pushes the live status to your [LinkHarbor](https://linkharbor.com) bio
page: a status card with Live/Offline, the viewer count, the stream title
and category and a "Watch on Kick" button, plus events for push
notifications to your followers when you go live.

It uses the public Kick API with an app token (client credentials), so it
works for any channel, not only your own. Standard library only,
Python 3.10 or newer.

Part of the [LinkHarbor integrations](https://github.com/linkharbor/integrations)
family (one repo per platform).

## Setup

```bash
git clone https://github.com/linkharbor/integration-kick
cd integration-kick

export LH_INGEST_URL="https://dock.at-harbor.com/v1/ingest/123"   # dashboard: Integrations, Custom (API)
export LH_INGEST_SECRET="…"
export KICK_CLIENT_ID="…"                                         # kick.com → Settings → Developer
export KICK_CLIENT_SECRET="…"

python3 agent.py --channel mychannel --name "My Channel" --interval 60
```

Start with `--once -v`. Run it as a service with `integration-kick.service`
(systemd; secrets go into `/etc/integration-kick.env`, `chmod 600`).

## Getting a client id and secret

Log in at [kick.com](https://kick.com), open **Settings → Developer** and
create an app. Any name and redirect URL will do; the agent only uses the
client id and client secret with the client-credentials grant, no user
login. Put them into `KICK_CLIENT_ID` / `KICK_CLIENT_SECRET`. The agent
requests an app token from `id.kick.com` and renews it before it expires.

## Events sent

| Event | When | Template variables |
|---|---|---|
| `stream.started` | the channel went live | `{title}`, `{category}`, `{value}` (viewers) |
| `stream.stopped` | the channel went offline | `{title}` |
| `title.changed` | the stream title changed while live | `{title}`, `{category}` |

On start the current state is sent without an event, so restarting the
agent does not notify followers again. The state is sent with every event
and every `--heartbeat` minutes (default 10). When Kick does not answer, the
agent logs it and tries again next interval; the card keeps its last state.

Suggested push rule (dashboard, Integrations, Push rules, Custom (API), event `stream.started`): title "🔴 {title} is live", body "{title}, {category}",
minimum pause 30 min.

## Options

```
--channel SLUG         channel slug, kick.com/<slug> (required)
--client-id ID         Kick app client id (default: env KICK_CLIENT_ID)
--client-secret SECRET Kick app client secret (default: env KICK_CLIENT_SECRET)
--name NAME            card title (default: the channel slug)
--address TEXT         text shown under the title instead of the stream title
--url URL              link behind the card's button (default: the channel page)
--cta TEXT             button label (default "Watch on Kick")
--image-url URL        banner image (default: the channel banner from Kick)
--interval SECONDS     polling interval (default 60, minimum 5)
--heartbeat MINUTES    state-only update interval (default 10, 0 = only on change)
--once                 poll once, send, exit
-v                     verbose logging
```

## License

[MIT](LICENSE). © 2026 EMI Solutions OÜ.
