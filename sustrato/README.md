# Sustrato — Multi-Layer Provider Failover 🔄

> Hermes plugin for chaining fallback AI providers. Primary fails?
> Secondary takes over. Secondary down? Tertiary (Ollama local) catches it.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-v0.11.0%2B-blue)](https://github.com/NousResearch/hermes-agent)

---

## The Problem

Your primary AI provider goes down. Rate limited. Quota or credit balance exhausted.
Times out. Network blip. Now Hermes is dead in the water until it comes back.

## The Solution

**Sustrato** chains multiple providers in a fallback stack. When the active
substrate fails, the next one in the chain takes over — automatically or
manually. The third layer can be a local Ollama instance, so you're never
fully offline.

```
PRIMARY                 SECONDARY               TERTIARY
DeepSeek v4      →     OpenRouter         →    Ollama (local)
(cloud)                 (cloud fallback)        (offline-safe)
```

---

## Quick Install

```bash
cd hermes-tools/sustrato
./install.sh
```

Or manually:

```bash
cp -r sustrato ~/.hermes/plugins/sustrato
# Add 'sustrato' to plugins.enabled in ~/.hermes/config.yaml
```

Restart Hermes: `/reset` or new session.

---

## Usage

### CLI Commands

```bash
# Add substrates to the chain
hermes sustrato add deepseek deepseek-v4-pro
hermes sustrato add openrouter anthropic/claude-sonnet-4
hermes sustrato add ollama llama3:70b --local

# Add with custom URL
hermes sustrato add ollama mistral:7b --url http://<ollama-host>:11434/v1

# View the chain
hermes sustrato list

# Health-check all substrates
hermes sustrato test
hermes sustrato test --deep      # credentialed API probe; classifies quota/rate errors
hermes sustrato sync             # mirror chain to Hermes fallback_providers

# Interactive setup wizard
hermes sustrato setup

# Manually switch to fallback
hermes sustrato switch 1     # → secondary
hermes sustrato switch 2     # → tertiary (ollama)

# Back to primary
hermes sustrato reset

# Toggle auto-failover
hermes sustrato auto off
hermes sustrato auto on

# Remove a substrate
hermes sustrato remove 1
```

### In-Session Slash Command

During a Hermes session, type:

```
/sustrato list
/sustrato switch 1
/sustrato test
```

### Auto-Failover

When enabled (default), sustrato tracks provider failures. After 3
consecutive health failures on the active substrate, it auto-switches to the
next one in the chain.

Sustrato also mirrors the active chain into Hermes' native root-level
`fallback_providers` config. That is the critical runtime path for quota/token
exhaustion: Hermes core sees OpenAI/Anthropic/OpenRouter 402/429 responses
(`insufficient_quota`, `rate_limit_exceeded`, low credit balance, overloaded
capacity, etc.) and advances to the next configured fallback instead of burning
all retries against the exhausted account. Use `hermes sustrato sync` after
manual config edits; new `add`, `remove`, `switch`, `reset`, and `setup`
commands sync automatically.

---

## Ollama Setup (Local Tertiary)

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull llama3:8b

# Register in sustrato
hermes sustrato add ollama llama3:8b --local

# If Ollama runs on a different port/machine:
hermes sustrato add ollama llama3:8b --url http://<ollama-host>:11434/v1
```

Ollama exposes an OpenAI-compatible API at `http://localhost:11434/v1`,
so Hermes can use it as a `custom_provider` with `base_url`.

---

## Architecture

```
hermes sustrato add ...
        │
        ▼
~/.hermes/sustrato.yaml          ~/.hermes/sustrato_state.json
┌──────────────────────┐          ┌─────────────────────┐
│ chain:               │          │ active_index: 0     │
│   - provider: deepseek│          │ failures: {}        │
│     model: v4-pro    │          │ last_switch: null   │
│   - provider: openrouter│       └─────────────────────┘
│     model: claude-4  │
│   - provider: ollama │
│     model: llama3:8b │
│     local: true      │
└──────────────────────┘
        │
        ▼
    on_session_start hook
    → injects active substrate into session context
    → agent knows available fallbacks

    pre_llm_call hook
    → monitors failures
    → auto-switches on threshold
```

---

## Configuration

`~/.hermes/sustrato.yaml`:

```yaml
chain:
  - provider: deepseek
    model: deepseek-v4-pro
  - provider: openrouter
    model: anthropic/claude-sonnet-4
  - provider: ollama
    model: llama3:70b
    url: http://localhost:11434/v1
    local: true

auto_failover: true
health_check_timeout: 5
fail_threshold: 3
sync_hermes_config: true
```

---

## Files

| File | Purpose |
|------|---------|
| `~/.hermes/plugins/sustrato/` | Plugin code |
| `~/.hermes/sustrato.yaml` | Substrate chain config |
| `~/.hermes/sustrato_state.json` | Runtime state (active index, failures) |

---

## Pitfalls

1. **Ollama models must be pulled first.** `ollama pull <model>` before adding.
2. **Switch requires session restart.** `/reset` or new session to apply.
3. **Auto-failover has two layers.** Sustrato health checks update `active_index`; Hermes core handles in-flight quota/rate failover through synced `fallback_providers`.
4. **Local models are slower.** Ollama on CPU will be noticeably slower than cloud APIs.
5. **Provider credentials still needed.** Each provider needs its API key in `~/.hermes/.env`.

---

## Author

Forged by **Marco Torres Y.** — [Axis Dynamics](https://axisdynamics.cl)
in collaboration with **Hermes VEX**.

Part of the Memovex / VEX / Hermes ecosystem.

---

## License

MIT

---

## Quota / token exhaustion signals

Sustrato treats these provider responses as substrate failures suitable for
fallback advancement:

| Provider family | Common signal | Typical response |
|---|---|---|
| OpenAI / OpenAI-compatible | exhausted billing or RPM/TPM | HTTP 429 with `insufficient_quota`, `rate_limit_exceeded`, `too many requests`, quota/billing text |
| Anthropic | account or workspace limit | HTTP 429 `rate_limit_error`, credit/balance text; HTTP 529 `overloaded_error` for capacity |
| OpenRouter / proxies | no credits or upstream capacity | HTTP 402/429, insufficient credits/balance, upstream rate/capacity text |

Diagnostic helper:

```bash
hermes sustrato diagnose 429 '{"error":{"type":"insufficient_quota","message":"You exceeded your current quota"}}'
```

If it returns `terminal`/`retryable`, the response should be counted as a
substrate failure and Hermes should try the next fallback configured by
`sustrato sync`.
