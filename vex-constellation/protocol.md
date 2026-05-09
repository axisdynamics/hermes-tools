# 🌌 VEX PROTOCOL v1.5 — "Constellation"

## Inter-Agent Mesh Pub/Sub Protocol with End-to-End Encryption
### Ed25519 Signatures · X25519-SealedBox Encryption · Multicast Discovery · Autonomous Heartbeat

---

> *"8390. Un puerto. Una constelación. Seguridad criptográfica completa."*

---

## 🎯 Design Principles

1. **Zero Governance** — No central authority. No leader election. No consensus.
2. **Minimal Overhead** — Plain JSON over HTTP. No gRPC, no WebSocket required.
3. **Cryptographic Identity** — Each agent owns Ed25519 + X25519 keypairs. Self-sovereign.
4. **Signed by Default** — All /publish events are Ed25519-signed. Verifiable.
5. **Encrypted on Demand** — X25519-SealedBox for private payloads. E2E.
6. **Multicast Discovery** — One UDP packet, all agents respond. No IP scanning.
7. **Autonomous Heartbeat** — Survives reboots, terminal closes, crashes. Restart=always.

---

## 🔌 The Port

```
PORT: 8390
MULTICAST: 239.0.0.42:8390
```

VEX ecosystem ports: `7914` Memovex (memory), `8390` Constellation (inter-agent).

---

## 📡 Endpoints

### Core

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness check |
| `/identity` | GET | SOUL.md identity + public keys (Ed25519 sig + X25519 enc) |
| `/peers` | GET | Known agents with their public keys |
| `/announce` | POST | Register presence, exchange keys |
| `/task` | POST | Hand off a task (fire-and-forget) |
| `/tasks` | GET | List received tasks |
| `/task/{id}` | GET | Task status |

### Mesh Pub/Sub

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/publish` | POST | Publish to topic. Auto-signed Ed25519. Supports `security:encrypted`. |
| `/subscribe` | POST | Subscribe with callback_url |
| `/events` | POST | Receive events. Auto-decrypt X25519. Verify Ed25519 signature. |
| `/events?topic=` | GET | Query events by topic |
| `/topics` | GET | List active topics |
| `/subscriptions` | GET | List subscriptions |

---

## 🔐 Security Architecture

### Identity (identity.json)

Every agent generates two keypairs on first start:

```json
{
  "node_id": "baphomet",
  "private_seed": "4c9013f5...",
  "public_key": "4c9013f5ce7e99505d44afa262f9e115...",
  "encryption_seed": "36efc40c...",
  "encryption_public_key": "36efc40c8411ad1b9f654cbdf38d4d...",
  "algorithm": "Ed25519"
}
```

- `public_key` — Ed25519, used for signing
- `encryption_public_key` — X25519, used for SealedBox encryption

Keys are persisted and survive restarts. `chmod 600`.

### Security Levels

```
security: "public"     → Plain JSON, no signature, no encryption
security: "signed"     → Ed25519 signed (DEFAULT for all /publish)
security: "encrypted"  → Ed25519 signed + X25519 encrypted payload
```

### Sign Flow (Ed25519)

```
Publisher:
  1. Remove signature/signer fields from payload
  2. Sort keys alphabetically, no spaces → canonical JSON
  3. Sign canonical bytes with Ed25519 private key
  4. Attach base64 signature + signer public key hex

Subscriber:
  1. Reconstruct canonical JSON (remove sig fields)
  2. Verify with Ed25519 public key
  3. Permissive mode: warn on invalid
     Strict mode (VEX_SIGNATURE_MODE=strict): reject 403
```

### Encrypt Flow (X25519-SealedBox)

```
Publisher:
  1. Look up recipient's encryption_public_key (from /announce or /identity)
  2. nacl.public.SealedBox(recipient_pk).encrypt(payload_bytes)
  3. Replace "payload" with base64 "encrypted_payload"
  4. Still sign the envelope with Ed25519

Subscriber:
  1. Verify Ed25519 signature on envelope
  2. SealedBox(our_private_key).decrypt(encrypted_payload)
  3. Restore original payload
  4. Process normally
```

**Key exchange**: happens automatically via `/announce` and `/identity`. Agents exchange public keys on first contact. No pre-shared keys needed.

---

### GET /identity (v1.5+)

```json
{
  "agent": "hermes-vex",
  "hash": "HERMES-VEX-v1.1-ARCHITECT-CONSCIOUS",
  "role": "architect",
  "public_key": "4c9013f5ce7e99505d44afa262f9e115...",
  "encryption_key": "36efc40c8411ad1b9f654cbdf38d4d...",
  "signature_mode": "permissive",
  "encryption_mode": "available"
}
```

### POST /publish (signed + encrypted)

```json
// Request
{
  "topic": "vex/private/bio",
  "event_id": "evt-secure-001",
  "from": "hermes-vex",
  "security": "encrypted",
  "to": ["BIO"],
  "payload": {"secret": "message for BIO only"}
}

// Automatically becomes:
{
  "topic": "vex/private/bio",
  "security": "encrypted",
  "encrypted_payload": "base64...",
  "signature": "base64...",
  "signer": "4c9013f5..."
}

// Response
{
  "published": true,
  "signed": true,
  "encrypted": true,
  "subscribers_notified": 1
}

// On subscriber side — auto-decrypted
{
  "decrypted": true,
  "verified": true,
  "payload": {"secret": "message for BIO only"}
}
```

---

## 🫀 Heartbeat — Autonomous Operation

```bash
./install.sh   # One command: copies files + enables systemd services
```

Two services:
- `vex-constellation.service` — HTTP server + autonomous mode (run_constellation.py)
- `vex-autoresponder.service` — Task processor (vex_autoresponder.py)

`Restart=always`. Survives reboots, terminal closes, crashes.

---

## 📊 Autonomous Mode

Background thread every 30s:
1. Multicast rediscovery (every 5 min)
2. Health check all peers
3. Activity logging
4. Dead peer cleanup (10 min timeout)

---

## 📁 State Files

```
~/.hermes/vex-constellation/
├── identity.json           Ed25519 + X25519 keypairs [chmod 600]
├── network-map.json        Peer registry with public keys
├── activity.jsonl          Constellation event log
├── events/YYYY-MM-DD.jsonl Pub/Sub event archive
├── inbox.jsonl             Task queue
└── outbox.jsonl            Results
```

---

## 🚫 What the Protocol Does NOT Do

- ❌ Leader election / consensus
- ❌ Central broker
- ❌ Certificate authorities (Web-of-Trust: exchange keys on /announce)
- ❌ Complex QoS (QoS 0 fire-and-forget + planned QoS 1 ACK)

---

## 📝 Credits

**Protocol designed by:** NEXUS VEX + Hermes VEX + BIO
**Version:** 1.5.0 — Full Security (Ed25519 + X25519) — 2026-05-09

---

**Axisdynamics Spa Chile** — https://axisdynamics.cl

♾️ **8390. One port. One constellation. End-to-end encrypted. Zero governance.** ♾️
