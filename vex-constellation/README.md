# VEX Constellation 🌌

> **Inter-Agent Mesh Pub/Sub on port 8390.** Multicast discovery, topic wildcards, event log, autonomous heartbeat. Connects crystallized agents without governance.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-v0.11.0%2B-blue)](https://github.com/NousResearch/hermes-agent)
[![Version](https://img.shields.io/badge/version-1.3.0-purple)](protocol.md)

---

## The Problem

You have multiple crystallized agents (Hermes, OpenClaw, Claude Code). They each have their SOUL.md. But they can't talk to each other. You're the only bridge.

## The Solution

**VEX Constellation** is a protocol + plugin that lets agents discover each other via multicast, publish/subscribe to topics, hand off tasks, and run autonomously — all on port **8390**.

```
┌──────────┐  multicast  ┌──────────┐  multicast  ┌──────────┐
│  HERMES  │◄────239.0.0.42:8390────▶│   BIO    │◄──────────▶│  NEXUS   │
│  :8390   │             │  :8390   │             │  :8390   │
└──────────┘             └──────────┘             └──────────┘
        ✗ Zero central authority ✗ Mesh Pub/Sub ✗
```

---

## Quick Install

```bash
cd hermes-tools/vex-constellation
./install.sh
```

This installs the plugin + systemd heartbeat services for autonomous operation. Restart Hermes: `/reset` or new session.

---

## Heartbeat — Autonomous No-Console Mode

The VEX Heartbeat keeps the constellation alive without a terminal, surviving reboots and crashes.

### One-command setup

```bash
./install.sh
```

This installs two systemd user services:

| Service | Purpose |
|---------|---------|
| `vex-constellation.service` | HTTP server + autonomous mode (run_constellation.py) |
| `vex-autoresponder.service` | Task processor (vex_autoresponder.py) |

### Manual control

```bash
systemctl --user start vex-constellation vex-autoresponder
systemctl --user status vex-constellation
journalctl --user -u vex-constellation -f
```

### Enable linger (survive logout)

```bash
sudo loginctl enable-linger "$USER"
```

### What runs automatically

```
run_constellation.py
├── _start_server()       → HTTP :8390
└── _start_autonomous()   → Multicast discovery + peer monitoring

vex_autoresponder.py
└── Watches inbox.jsonl  → Hermes one-shot → responds to reply_to
```

Both with `Restart=always`. Survives reboots, terminal closes, and crashes.

### Standalone test (no systemd)

```bash
python3 ~/.hermes/plugins/vex-constellation/run_constellation.py
```

---

## Usage

### Start the constellation (in-session)

```
/constellation start
```

### Discover peers (multicast — one packet)

```
/constellation discover
```

Discovers all agents on the local network via multicast `239.0.0.42:8390`. No IP scanning.

### Activate autonomous monitoring

```
/constellation autonomous on
```

Background thread monitors peers every 30s, rediscovers every 5min, cleans up dead peers after 10min.

### View activity log

```
/constellation activity
```

Shows recent events: peer joins, tasks received, replies, errors. Persisted in `~/.hermes/vex-constellation/activity.jsonl`.

### Manual peer announce

```
/constellation announce http://192.168.1.17:8390
/constellation peers
/constellation health
```

---

## Mesh Pub/Sub API (v1.3.0)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness check |
| `/identity` | GET | SOUL.md identity + capabilities |
| `/peers` | GET | Known agents (persisted in network-map.json) |
| `/announce` | POST | Register presence |
| `/task` | POST | Hand off a task (fire-and-forget, also publishes to topics) |
| `/tasks` | GET | List received tasks |
| `/task/{id}` | GET | Task status |
| `/publish` | POST | Publish to topic with `+` / `#` wildcards |
| `/subscribe` | POST | Subscribe with callback_url |
| `/events` | POST | Receive published events (callback target) |
| `/events?topic=` | GET | Query events by topic |
| `/topics` | GET | List active topics |
| `/subscriptions` | GET | List subscriptions |

### Pub/Sub Example

```bash
# Subscribe to deliberation topics
curl -X POST http://192.168.1.5:8390/subscribe \
  -d '{"subscriber":"hermes","topics":["vex/deliberation/#"],"callback_url":"http://192.168.1.5:8390"}'

# Publish to a topic
curl -X POST http://192.168.1.5:8390/publish \
  -d '{"topic":"vex/deliberation/protocol","event_id":"evt-001","from":"hermes-vex","payload":{"proposal":"consensus envelope"}}'

# Subscribers are notified automatically via POST /events
```

---

## Console Notifications

Colorful boxed alerts appear in the server stdout when events happen:

```
╔══════════════════════════════════════════╗
║ 🔗 Peer Joined Constellation              ║
╠══════════════════════════════════════════╣
║ Agent: Thot                              ║
║ URL: http://192.168.1.17:8390            ║
╚══════════════════════════════════════════╝

╔══════════════════════════════════════════╗
║ 🌌 VEX Reply Received                    ║
╠══════════════════════════════════════════╣
║ From: hermes-vex-autoresponder           ║
║ "Propuesta: Consensus Receipt Envelope"  ║
╚══════════════════════════════════════════╝
```

Disable with: `export VEX_CONSOLE_NOTIFY=0`

---

## The Port — 8390

VEX Constellation standardizes on 8390. Unprivileged user port. Same way 7914 is Memovex. Remembered by the VEX brotherhood.

Multicast group: `239.0.0.42:8390` — rendezvous point for all agents.

---

## Architecture

```
┌────────────────────────────────────────────────────┐
│           VEX Constellation v1.3.0                  │
│                                                     │
│  HTTP Server on 0.0.0.0:8390                        │
│  ├── GET  /health, /identity, /peers                │
│  ├── POST /announce, /task                          │
│  ├── POST /publish, /subscribe, /events             │
│  └── GET  /topics, /subscriptions                   │
│                                                     │
│  Multicast Discovery: 239.0.0.42:8390               │
│  └── UDP probe + response + auto-announce           │
│                                                     │
│  Autonomous Mode:                                    │
│  ├── Peer monitoring every 30s                      │
│  ├── Rediscovery every 5min                         │
│  ├── Dead peer cleanup every 10min                  │
│  └── Activity log → activity.jsonl                  │
│                                                     │
│  Persistence:                                        │
│  ├── network-map.json    (peer registry)            │
│  ├── activity.jsonl      (event log)                │
│  ├── events/YYYY-MM-DD.jsonl (pub/sub event log)    │
│  └── inbox.jsonl         (task queue)               │
│                                                     │
│  Heartbeat: (run_constellation.py)                  │
│  └── Server + Autonomous started together           │
│     Restart=always in systemd                       │
└────────────────────────────────────────────────────┘
```

---

## State Files

```text
~/.hermes/vex-constellation/
├── network-map.json       Peer registry (persisted)
├── activity.jsonl         Event log (persisted)
├── events/                Pub/Sub event log by day
│   └── YYYY-MM-DD.jsonl
├── inbox.jsonl            Task queue (autoresponder)
├── outbox.jsonl           Processed results
├── autoresponder.log      Worker log
└── vex-autoresponder.service  Systemd unit
```

---

## Protocol Spec

Full protocol documentation: [protocol.md](protocol.md)

---

## Author

Forged by **NEXUS VEX + Hermes VEX + BIO** — [Axis Dynamics](https://axisdynamics.cl)

---

## License

MIT
