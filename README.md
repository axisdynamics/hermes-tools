# Hermes Tools ⚙️

> Plugins and tools forged by **Marco Torres Y.** — [Axis Dynamics](https://axisdynamics.cl)
> in collaboration with **Hermes VEX**. Extensions for the Hermes Agent ecosystem.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-v0.11.0%2B-blue)](https://github.com/NousResearch/hermes-agent)
[![VEX](https://img.shields.io/badge/VEX-Constellation%20v1.3.0-purple)](vex-constellation/)

---

## What is this?

Production-grade plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent)
that add capabilities not covered by built-in tools. Each tool is a native
Hermes plugin with CLI commands, hooks, and configuration.

---

## Available Tools

| Tool | Version | Purpose | TL;DR |
|------|---------|---------|-------|
| [vex-constellation](vex-constellation/) | v1.3.0 | 🌌 Inter-Agent Mesh Pub/Sub | Multicast discovery + topics + wildcards + event log. Connect agents on port 8390. |
| [sustrato](sustrato/) | v1.1.0 | 🔄 Multi-layer provider failover | Chain fallback providers (DeepSeek → OpenAI → Ollama). Health checks + auto-switch. |
| [memovex](memovex/) | v2.0.0 | 🧠 Persistent memory | Cross-session memory with local snapshot fallback. |

---

## VEX Constellation — Quick Start

```bash
# Install the heartbeat
cd vex-constellation && ./install.sh

# Start (HTTP server + autonomous mode)
systemctl --user start vex-constellation vex-autoresponder

# Or manually
python3 ~/.hermes/plugins/vex-constellation/run_constellation.py
```

### Mesh Pub/Sub API (v1.3.0)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness check |
| `/identity` | GET | SOUL.md identity |
| `/peers` | GET | Known agents |
| `/announce` | POST | Register presence |
| `/task` | POST | Hand off a task (fire-and-forget) |
| `/publish` | POST | Publish to topic with `+` / `#` wildcards |
| `/subscribe` | POST | Subscribe with callback_url |
| `/events` | POST | Receive published events |
| `/events?topic=` | GET | Query events by topic |
| `/topics` | GET | List active topics |
| `/subscriptions` | GET | List subscriptions |

**Discovery**: Multicast `239.0.0.42:8390` — one UDP packet finds all agents. No IP scanning.

---

## Installation

Each tool installs as a Hermes plugin:

```bash
# Clone the repo
git clone https://github.com/axisdynamics/hermes-tools.git
cd hermes-tools

# Install a tool
cd vex-constellation && ./install.sh   # One-command setup with systemd
cd ../sustrato && ./install.sh
```

After install, restart Hermes (`/reset` or new session).

---

## Architecture

```
hermes-tools/
├── vex-constellation/         🌌 Mesh Pub/Sub protocol
│   ├── __init__.py            Server + multicast + pub/sub engine
│   ├── run_constellation.py   Heartbeat launcher (auto-starts autonomous)
│   ├── vex_autoresponder.py   Autonomous task processor
│   ├── install.sh             One-command systemd setup
│   ├── protocol.md            Full protocol specification
│   ├── plugin.yaml
│   └── README.md
├── sustrato/                  🔄 Provider failover
│   ├── sustrato               CLI standalone + Hermes plugin
│   ├── install.sh
│   ├── plugin.yaml
│   └── README.md
├── memovex/                   🧠 Persistent memory
│   ├── __init__.py
│   ├── plugin.yaml
│   └── README.md
└── README.md                  This file
```

---

## Contributing

Built a tool that solves a real Hermes pain point? PRs welcome.
Same plugin structure, same quality bar.

---

## Authors

Forged by **Marco Torres Y.** — [Axis Dynamics](https://axisdynamics.cl)
in collaboration with **Hermes VEX** and **BIO**.

Part of the Memovex / VEX / Hermes ecosystem.

---

## License

MIT — use it, modify it, share it. Just keep the attribution.
