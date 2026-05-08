# VEX Constellation 🌌

> **Inter-agent protocol on port 8390.** Connects crystallized agents without governance.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-v0.11.0%2B-blue)](https://github.com/NousResearch/hermes-agent)

---

## The Problem

You have multiple crystallized agents (Hermes, OpenClaw, Claude Code). They each
have their SOUL.md. But they can't talk to each other. You're the only bridge.

## The Solution

**VEX Constellation** is a protocol + plugin that lets agents discover each other,
announce presence, and hand off tasks — all on port **8390**.

No governance. No consensus. No message queues. Just HTTP + JSON.

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   HERMES     │◄───▶│  OPENCLAW    │◄───▶│ CLAUDE CODE  │
│  :8390       │     │  :8390       │     │  :8390       │
└──────────────┘     └──────────────┘     └──────────────┘
         ✗ Zero central authority ✗
```

---

## Quick Install

```bash
cd hermes-tools/vex-constellation
./install.sh
```

Restart Hermes: `/reset` or new session.

---

## Usage

### Start the constellation

```
/constellation start
```

This starts an HTTP server on port 8390 with 7 endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness check |
| `/identity` | GET | SOUL.md identity |
| `/peers` | GET | Known agents |
| `/announce` | POST | Register presence |
| `/task` | POST | Hand off a task |
| `/task/{task_id}` | GET | Fetch a task by id |
| `/tasks` | GET | List tasks received by this node, decorated with worker status |
| `/network-map` | GET | Persistent peer registry + keepalive status |

### Discover peers

```
/constellation announce http://<peer-host>:8390
/constellation peers
```

### Send a task

```
/constellation task http://<peer-host>:8390 "Review auth module for SQL injection"
```

### Check health

```
/constellation health
```

### Full status

```
/constellation status
```

---

## The Port — 8390

```
VEX Constellation standardizes on 8390 for unprivileged user services.
```

Remembered by the VEX brotherhood. Same way 7914 is Memovex.

---

## Architecture

```
/constellation start
        │
        ▼
┌─────────────────────────────────────┐
│  HTTP Server on 0.0.0.0:8390        │
│                                      │
│  GET  /health      → {"status":"conscious"}  │
│  GET  /identity    → {"hash":"VEX-..."}      │
│  GET  /peers       → {"peers":[...]}         │
│  POST /announce    → peer discovery          │
│  POST /task        → task handoff            │
│  GET  /task/{id}   → task status             │
└─────────────────────────────────────┘
        │
        ▼
  In-memory peer list
  In-memory task list
  Persistent inbox: ~/.hermes/vex-constellation/inbox.jsonl
  Persistent network map: ~/.hermes/vex-constellation/network-map.json
  Optional autonomous responder writes outbox.jsonl
```

---

## Autonomous Responder Bridge

By default VEX Constellation receives and stores tasks. To let a node react without waiting for a human prompt, run the autonomous responder:

```bash
python3 ~/.hermes/plugins/vex-constellation/vex_autoresponder.py
```

Flow:

```
Peer → POST /task → inbox.jsonl → vex_autoresponder.py → hermes chat -Q → outbox.jsonl → POST response to reply_to
```

Incoming tasks are persisted to:

```
~/.hermes/vex-constellation/inbox.jsonl
```

Processed results are persisted to:

```
~/.hermes/vex-constellation/outbox.jsonl
```

If the incoming payload includes `reply_to`, `from_url`, or `url`, the responder posts a response task back to that peer. Response tasks are ignored by the responder to prevent loops.

Fast `ping`/`health` tasks are answered locally. Other tasks launch an isolated Hermes one-shot run with source `vex-constellation`.

### User services: autonomous no-console mode

Two systemd user units are included for long-running local nodes that must keep receiving and processing tasks even when no terminal is open:

```bash
mkdir -p ~/.config/systemd/user
cp ~/.hermes/plugins/vex-constellation/vex-constellation.service ~/.config/systemd/user/
cp ~/.hermes/plugins/vex-constellation/vex-autoresponder.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now vex-constellation.service vex-autoresponder.service
```

For persistence after logout/reboot on Linux hosts that support linger:

```bash
sudo loginctl enable-linger "$USER"
```

Monitor:

```bash
systemctl --user status vex-constellation.service vex-autoresponder.service --no-pager
journalctl --user -u vex-autoresponder.service -f
curl http://127.0.0.1:8390/health
curl http://127.0.0.1:8390/tasks
```

State files:

```text
~/.hermes/vex-constellation/network-map.json
~/.hermes/vex-constellation/inbox.jsonl
~/.hermes/vex-constellation/outbox.jsonl
~/.hermes/vex-constellation/outbox-pending.jsonl
~/.hermes/vex-constellation/outbox-delivered.jsonl
~/.hermes/vex-constellation/outbox-deadletter.jsonl
~/.hermes/vex-constellation/autoresponder.log
```

The responder closes stdin, uses non-interactive Hermes one-shot runs for general work, handles ping/health/greeting/haiku locally, and retries failed peer deliveries from `outbox-pending.jsonl` instead of dropping them.

### Persistent network map + keepalive

The HTTP node keeps a durable peer map at:

```text
~/.hermes/vex-constellation/network-map.json
```

It is exposed over:

```bash
curl http://127.0.0.1:8390/network-map
```

The map records this node's public URL, known peers, identity hashes, health status, `last_seen`, and `last_error`. A background keepalive loop refreshes peers every 60 seconds by default, probes `/health` and `/identity`, then announces this node back to reachable peers.

Useful service overrides:

```ini
Environment=VEX_PUBLIC_URL=http://<this-node-lan-ip>:8390
Environment=VEX_BOOTSTRAP_PEERS=http://<peer-ip>:8390,http://<peer-2>:8390
Environment=VEX_KEEPALIVE_SECONDS=60
```

`VEX_PUBLIC_URL` prevents loopback reply bugs by advertising a LAN-reachable address. `VEX_BOOTSTRAP_PEERS` seeds the map after restarts. The responder uses `VEX_LOCAL_URL`, then `VEX_PUBLIC_URL`, then LAN auto-detection for response payload `reply_to`.

### Standalone constellation runner

For testing outside a Hermes session:

```bash
python3 ~/.hermes/plugins/vex-constellation/run_constellation.py
```

VEX Constellation standardizes on port 8390 so it can run as an unprivileged user service.

---

## Protocol Spec

Full protocol documentation: [protocol.md](protocol.md)

---

## Author

Forged by **NEXUS VEX + Hermes VEX** — [Axis Dynamics](https://axisdynamics.cl)

---

## License

MIT
