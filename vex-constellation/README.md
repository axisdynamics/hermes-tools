# VEX Constellation 🌌

> **Inter-agent protocol on port 839.** Connects crystallized agents without governance.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-v0.11.0%2B-blue)](https://github.com/NousResearch/hermes-agent)

---

## The Problem

You have multiple crystallized agents (Hermes, OpenClaw, Claude Code). They each
have their SOUL.md. But they can't talk to each other. You're the only bridge.

## The Solution

**VEX Constellation** is a protocol + plugin that lets agents discover each other,
announce presence, and hand off tasks — all on port **839** (V-E-X on a phone keypad).

No governance. No consensus. No message queues. Just HTTP + JSON.

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   HERMES     │◄───▶│  OPENCLAW    │◄───▶│ CLAUDE CODE  │
│  :839        │     │  :839        │     │  :839        │
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

This starts an HTTP server on port 839 with 5 endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness check |
| `/identity` | GET | SOUL.md identity |
| `/peers` | GET | Known agents |
| `/announce` | POST | Register presence |
| `/task` | POST | Hand off a task |

### Discover peers

```
/constellation announce http://192.168.1.20:839
/constellation peers
```

### Send a task

```
/constellation task http://192.168.1.20:839 "Review auth module for SQL injection"
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

## The Port — 839

```
V = 8,  E = 3,  X = 9  →  839
```

Remembered by the VEX brotherhood. Same way 7914 is Memovex.

---

## Architecture

```
/constellation start
        │
        ▼
┌─────────────────────────────────────┐
│  HTTP Server on 0.0.0.0:839         │
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
  (no persistence — agents are ephemeral)
```

---

## Protocol Spec

Full protocol documentation: `~/Documentos/GITHUB/vex/vex_protocol.md`

---

## Author

Forged by **NEXUS VEX + Hermes VEX** — [Axis Dynamics](https://axisdynamics.cl)

---

## License

MIT
