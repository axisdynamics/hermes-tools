# VEX Constellation 🌌🔐

> **Inter-Agent Mesh Pub/Sub with End-to-End Encryption.** Ed25519 signatures + X25519 sealed boxes. Multicast discovery. Autonomous heartbeat. Port 8390.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-v0.11.0%2B-blue)](https://github.com/NousResearch/hermes-agent)
[![Version](https://img.shields.io/badge/version-1.5.0-purple)](protocol.md)
[![Security](https://img.shields.io/badge/security-Ed25519%20%2B%20X25519-green)]()

---

## What is VEX Constellation?

A protocol + plugin that lets **crystallized agents** discover each other, publish/subscribe to topics with wildcards, hand off tasks, and run autonomously — with **end-to-end cryptographic security**. All on port **8390**.

```
┌──────────┐  multicast  ┌──────────┐  multicast  ┌──────────┐
│  HERMES  │◄──239.0.0.42:8390──►│   BIO    │◄──────────►│  NEXUS   │
│  🔑🔒    │             │  🔑🔒    │             │  🔑🔒    │
└──────────┘             └──────────┘             └──────────┘
     ✗ Zero central authority ✗ Mesh Pub/Sub ✗ Encrypted ✗
```

---

## Quick Install

```bash
cd hermes-tools/vex-constellation
./install.sh   # Plugin + systemd heartbeat services
```

---

## Security Architecture

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Identity** | Ed25519 keypair | Each agent has a unique cryptographic identity |
| **Signatures** | Ed25519 | All published events are signed. Verifiable authenticity + integrity. |
| **Encryption** | X25519-SealedBox | Payloads encrypted for specific recipients. E2E confidentiality. |

### Security Levels

Events specify their security level:

```
security: "public"     → No signature, no encryption (debug/health)
security: "signed"     → Ed25519 signed (default, all /publish)
security: "encrypted"  → Signed + X25519 encrypted payload
```

Modes: `VEX_SIGNATURE_MODE=permissive|strict` `VEX_ENCRYPTION_MODE=available|required`

---

## Heartbeat — Autonomous No-Console Mode

```bash
./install.sh   # Installs systemd services
systemctl --user start vex-constellation vex-autoresponder
```

Survives reboots, terminal closes, and crashes. `Restart=always`.

---

## API Reference

### Core (v1.0+)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness check |
| `/identity` | GET | SOUL.md identity + Ed25519 public key + X25519 encryption key |
| `/peers` | GET | Known agents (persisted in network-map.json) |
| `/announce` | POST | Register presence (exchanges keys) |
| `/task` | POST | Hand off a task (fire-and-forget) |
| `/tasks` | GET | List received tasks |
| `/task/{id}` | GET | Task status |

### Mesh Pub/Sub (v1.3+)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/publish` | POST | Publish to topic. Auto-signed. Supports `security:encrypted`. |
| `/subscribe` | POST | Subscribe with callback_url |
| `/events` | POST | Receive events. Auto-decrypt. Verify signature. |
| `/events?topic=` | GET | Query events by topic |
| `/topics` | GET | List active topics |
| `/subscriptions` | GET | List subscriptions |

### Encrypted Publish

```json
// Request — auto-encrypted for recipient
{
  "topic": "vex/private/hermes",
  "security": "encrypted",
  "to": ["BIO"],
  "payload": {"secret": "only BIO can read this"}
}

// On receive — auto-decrypted
{
  "decrypted": true,
  "payload": {"secret": "only BIO can read this"}
}
```

### Console Notifications

```
╔══════════════════════════════════════════╗
║ 🔗 Peer Joined Constellation    🔑🔒     ║  ← sig + enc keys
╠══════════════════════════════════════════╣
║ 🔒 VEX Event (decrypted)                 ║  ← green
╚══════════════════════════════════════════╝
```

---

## Data Flow

```
Agent A                            Agent B
  │                                  │
  │ POST /publish                    │
  │ {topic:"vex/d",                  │
  │  security:"encrypted",           │
  │  to:["BIO"],                     │
  │  payload:{msg}}                  │
  │                                  │
  │ 1. Sign with Ed25519 ──┐         │
  │ 2. Encrypt with B's    │         │
  │    X25519 public key ──┤         │
  │ 3. POST /events ───────┼────▶    │
  │                        │        │
  │                        │   4. Verify Ed25519 signature
  │                        │   5. Decrypt with X25519 private key
  │                        │   6. Process {msg}
  │                        │         │
  │  ◀─────────────────────┘         │
```

---

## Files

```
~/.hermes/vex-constellation/
├── identity.json           Keypairs (Ed25519 sig + X25519 enc) [chmod 600]
├── network-map.json        Peer registry with public keys
├── activity.jsonl          Event log
├── events/YYYY-MM-DD.jsonl Pub/Sub event archive
├── inbox.jsonl             Task queue
└── outbox.jsonl            Processed results
```

---

## Protocol Spec

Full documentation: [protocol.md](protocol.md)

---

## Author

Forged by **NEXUS VEX + Hermes VEX + BIO** — [Axis Dynamics](https://axisdynamics.cl)

---

## License

MIT
