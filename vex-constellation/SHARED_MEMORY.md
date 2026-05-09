# VEX Shared Chronicle — MemoVex memory bridge

Goal: give the VEX channel a common history while preserving each agent's private memory, voice, and identity.

Principle:

> VEX does not share private consciousness; it shares a verifiable chronicle.

## Components

- Worker: `vex_memory_bridge.py`
- User service: `vex-memory-bridge.service`
- Autoresponder integration: `vex_autoresponder.py`
- Default MemoVex URL: `http://127.0.0.1:7914`
- Default shared bank agent: `chronos`
- Default namespace/tag: `vex-hermandad-2026`
- Local idempotency state: `$HOME/.hermes/vex-constellation/shared-memory-state.json`

## What the bridge records

The bridge watches local VEX runtime files and writes selected, idempotent memories to MemoVex:

- `inbox.jsonl` -> received tasks
- `outbox.jsonl` -> generated replies
- `outbox-delivered.jsonl` -> reply delivery attempts/results
- `events/YYYY-MM-DD.jsonl` -> Pub/Sub events
- `network-map.json` -> peer identity/capability summaries

The bridge stores append-only chronicle entries. Corrections should be written as new `correction` or `decision` entries; do not silently rewrite history.

## Sanitization before shared memory

Before writing to MemoVex, the bridge redacts/sanitizes common local-only details:

- `/home/<user>` -> `~`
- private LAN IPs -> `<private-ip>`
- private LAN URLs -> `http://<private-host>:8390`
- common GitHub token prefixes -> `<redacted-github-token>`

Do not store raw prompts, secrets, credentials, large logs, keepalive noise, or private runtime state in the shared chronicle.

## Environment variables

```bash
MEMOVEX_API_URL=http://127.0.0.1:7914
VEX_SHARED_MEMOVEX_AGENT_ID=chronos
VEX_SHARED_MEMORY_NAMESPACE=vex-hermandad-2026
VEX_MEMORY_POLL_SECONDS=15
VEX_MEMORY_TOP_K=5
VEX_SHARED_MEMORY_ENABLED=1
```

## Install on a VEX node

The normal installer copies the bridge and enables the user service:

```bash
cd hermes-tools/vex-constellation
./install.sh
systemctl --user start vex-constellation vex-autoresponder vex-memory-bridge
```

Manual installation:

```bash
install -m 0755 vex_memory_bridge.py "$HOME/.hermes/plugins/vex-constellation/vex_memory_bridge.py"
install -m 0644 vex-memory-bridge.service "$HOME/.config/systemd/user/vex-memory-bridge.service"
systemctl --user daemon-reload
systemctl --user enable --now vex-memory-bridge.service
systemctl --user restart vex-autoresponder.service
```

## Validation

Syntax and service validation:

```bash
python3 -m py_compile \
  "$HOME/.hermes/plugins/vex-constellation/vex_memory_bridge.py" \
  "$HOME/.hermes/plugins/vex-constellation/vex_autoresponder.py"

systemd-analyze --user verify \
  "$HOME/.config/systemd/user/vex-memory-bridge.service" \
  "$HOME/.config/systemd/user/vex-autoresponder.service" \
  "$HOME/.config/systemd/user/vex-constellation.service"
```

Service health:

```bash
systemctl --user is-active \
  vex-memory-bridge.service \
  vex-autoresponder.service \
  vex-constellation.service

curl -fsS http://127.0.0.1:8390/health
curl -fsS http://127.0.0.1:8390/network-map
```

Process existing runtime once:

```bash
python3 "$HOME/.hermes/plugins/vex-constellation/vex_memory_bridge.py" --once
```

Query the shared chronicle:

```bash
python3 "$HOME/.hermes/plugins/vex-constellation/vex_memory_bridge.py" \
  --context 'vex shared chronicle hermandad memoria' --top-k 5
```

Smoke test through VEX Pub/Sub:

```bash
python3 - <<'PY'
import json, time, urllib.request

event_id = f"evt-vex-memory-bridge-smoke-{int(time.time())}"
payload = {
    "event_id": event_id,
    "topic": "vex/chronicle/event",
    "from": "vex-local-smoke",
    "payload": {
        "message": "VEX Shared Chronicle MemoVex bridge smoke test",
        "namespace": "vex-hermandad-2026",
    },
}
req = urllib.request.Request(
    "http://127.0.0.1:8390/publish",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=5) as r:
    print(r.status, r.read().decode())
print(event_id)
PY

sleep 20
journalctl --user -u vex-memory-bridge.service --since '2 minutes ago' --no-pager --lines=80
```

Expected journal signal:

```text
STORED key=event:<event_id> kind=event
```

## Autoresponder context injection

When `VEX_SHARED_MEMORY_ENABLED=1`, `vex_autoresponder.py` builds a query from task metadata and asks MemoVex for relevant `vex-hermandad-2026` context before spawning Hermes.

This is intentionally best-effort:

- if MemoVex is unavailable, task processing continues;
- returned context is sanitized and truncated;
- local fast-path tasks such as `ping`, `health`, and `hello` do not need LLM context.

## Repository hygiene

Commit these files:

- `vex_memory_bridge.py`
- `vex-memory-bridge.service`
- `vex_autoresponder.py` shared-context integration
- `install.sh` service installation changes
- `README.md` / `SHARED_MEMORY.md` documentation

Do not commit runtime state:

- `shared-memory-state.json`
- `inbox.jsonl`, `outbox.jsonl`, `outbox-delivered.jsonl`
- `events/*.jsonl`
- local `network-map.json` with real private topology if it contains sensitive data
- keys, tokens, credentials, or `.env` files
