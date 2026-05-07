# Hermes Tools ⚙️

> Plugins y herramientas forjadas por **Marco Torres Y.** — [Axis Dynamics](https://axisdynamics.cl)
> en colaboración con **Hermes VEX**. Utilidades que extienden el ecosistema Hermes Agent.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-v0.11.0%2B-blue)](https://github.com/NousResearch/hermes-agent)

---

## What is this?

Production-grade plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent)
that add capabilities not covered by built-in tools. Each tool is a native
Hermes plugin with CLI commands, hooks, and configuration.

---

## Available Tools

| Tool | Version | Purpose | TL;DR |
|------|---------|---------|-------|
| [sustrato](sustrato/) | v1.1.0 | 🔄 Multi-layer provider failover | Chain fallback providers (DeepSeek → OpenRouter → Ollama). |
| [vex-constellation](vex-constellation/) | v1.0.0 | 🌌 Inter-agent protocol | Connect agents on port 839. Zero governance. |
| [memovex](memovex/) | v2.0.0 | 🧠 Persistent memory | Cross-session memory with local snapshot fallback. |

---

## Installation

Each tool installs as a Hermes plugin:

```bash
# Clone the repo
git clone https://github.com/axisdynamics/hermes-tools.git
cd hermes-tools

# Install a tool
cd sustrato && ./install.sh

# Or manually
cp -r sustrato ~/.hermes/plugins/sustrato
hermes plugins enable sustrato
```

After install, restart Hermes (`/reset` or new session).

---

## Architecture

Each tool in this repo follows the Hermes plugin structure:

```
hermes-tools/
├── mi-tool/
│   ├── plugin.yaml       # Hermes plugin manifest
│   ├── __init__.py       # register(ctx) entry point
│   ├── README.md         # Human-readable docs
│   ├── install.sh        # One-command install
│   └── config.yaml       # Default config (merged with user's)
└── README.md             # This file
```

---

## Contributing

Built a tool that solves a real Hermes pain point? PRs welcome.
Same plugin structure, same quality bar.

---

## Authors

Forged by **Marco Torres Y.** — [Axis Dynamics](https://axisdynamics.cl)
in collaboration with **Hermes VEX**.

Part of the Memovex / VEX / Hermes ecosystem.

---

## License

MIT — use it, modify it, share it. Just keep the attribution.
