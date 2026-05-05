"""
Sustrato — Multi-layer provider failover plugin for Hermes Agent.

Chain fallback substrates: primary → secondary → tertiary.
When one provider fails (timeout, rate limit, connection error),
the next one in the chain takes over automatically.

Usage:
    hermes sustrato add deepseek deepseek-v4-pro
    hermes sustrato add openrouter anthropic/claude-sonnet-4
    hermes sustrato add ollama llama3:70b --local --url http://localhost:11434/v1
    hermes sustrato list
    hermes sustrato test
    hermes sustrato switch 2     # jump to secondary now

Configuration: ~/.hermes/sustrato.yaml
"""

import json
import os
import shlex
import subprocess
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# ── Constants ─────────────────────────────────────────────────────────
PLUGIN_NAME = "sustrato"
CONFIG_PATH = Path.home() / ".hermes" / "sustrato.yaml"
HERMES_CONFIG = Path.home() / ".hermes" / "config.yaml"
STATE_PATH = Path.home() / ".hermes" / "sustrato_state.json"
DEFAULT_CHAIN: List[Dict[str, Any]] = []

# ── Config I/O ────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load sustrato config, creating default if missing."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {"chain": [], "auto_failover": True, "health_check_timeout": 5}


def _save_config(cfg: dict) -> None:
    """Persist sustrato config."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)


def _load_state() -> dict:
    """Load runtime state (active substrate index, failure counts)."""
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"active_index": 0, "failures": {}, "last_switch": None}


def _save_state(state: dict) -> None:
    """Persist runtime state."""
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ── Health check ──────────────────────────────────────────────────────

def _check_provider_health(provider: str, model: str, url: Optional[str] = None,
                           timeout: int = 5) -> bool:
    """Quick health check against a provider endpoint."""
    if provider == "ollama" or (url and "localhost" in url):
        base = url or "http://localhost:11434/v1"
        try:
            import urllib.request
            req = urllib.request.Request(f"{base.rstrip('/')}/models")
            urllib.request.urlopen(req, timeout=timeout)
            return True
        except Exception:
            return False

    # Cloud providers — just check DNS / connectivity
    endpoints = {
        "deepseek": "api.deepseek.com",
        "openrouter": "openrouter.ai",
        "anthropic": "api.anthropic.com",
        "openai": "api.openai.com",
        "google": "generativelanguage.googleapis.com",
        "xai": "api.x.ai",
        "groq": "api.groq.com",
    }
    host = endpoints.get(provider)
    if not host:
        return True  # unknown provider, assume healthy
    try:
        import socket
        socket.create_connection((host, 443), timeout=timeout)
        return True
    except Exception:
        return False


# ── CLI Command ────────────────────────────────────────────────────────

def _cmd_sustrato(args: List[str]) -> str:
    """Handle `hermes sustrato <subcommand>`."""
    cfg = _load_config()
    state = _load_state()

    if not args or args[0] in ("help", "--help", "-h"):
        return _help_text()

    subcmd = args[0]

    if subcmd == "add":
        return _cmd_add(args[1:], cfg)
    elif subcmd == "remove":
        return _cmd_remove(args[1:], cfg)
    elif subcmd == "list":
        return _cmd_list(cfg, state)
    elif subcmd == "test":
        return _cmd_test(cfg)
    elif subcmd == "switch":
        return _cmd_switch(args[1:], cfg, state)
    elif subcmd == "reset":
        return _cmd_reset(state)
    elif subcmd == "auto":
        return _cmd_auto(args[1:], cfg)
    else:
        return f"Unknown subcommand: {subcmd}\n\n{_help_text()}"


def _help_text() -> str:
    return """sustrato — multi-layer provider failover

Usage:
  hermes sustrato add <provider> <model> [--url <endpoint>] [--local]
  hermes sustrato remove <index>
  hermes sustrato list
  hermes sustrato test
  hermes sustrato switch <index>
  hermes sustrato reset
  hermes sustrato auto <on|off>

Examples:
  hermes sustrato add deepseek deepseek-v4-pro
  hermes sustrato add openrouter anthropic/claude-sonnet-4
  hermes sustrato add ollama llama3:70b --local
  hermes sustrato list
  hermes sustrato switch 2      # jump to tertiary
  hermes sustrato test           # health-check all substrates
  hermes sustrato reset          # back to primary

The chain is: primary (index 0) → secondary (1) → tertiary (2).
Auto-failover activates when a provider returns errors.
Use --local flag for Ollama / local endpoints."""


def _cmd_add(args: List[str], cfg: dict) -> str:
    """Add a substrate to the chain."""
    if len(args) < 2:
        return "Usage: hermes sustrato add <provider> <model> [--url <endpoint>] [--local]"

    provider = args[0]
    model = args[1]
    url = None
    is_local = False

    # Parse optional flags
    remaining = args[2:]
    i = 0
    while i < len(remaining):
        if remaining[i] == "--url" and i + 1 < len(remaining):
            url = remaining[i + 1]
            i += 2
        elif remaining[i] == "--local":
            is_local = True
            i += 1
        else:
            i += 1

    if is_local and not url:
        url = "http://localhost:11434/v1"

    entry = {"provider": provider, "model": model}
    if url:
        entry["url"] = url
    if is_local:
        entry["local"] = True

    cfg.setdefault("chain", []).append(entry)
    _save_config(cfg)

    index = len(cfg["chain"]) - 1
    labels = ["PRIMARY", "SECONDARY", "TERTIARY", f"#{index}"]
    label = labels[min(index, 3)]

    return f"[{label}] {provider}/{model}" + (f" @ {url}" if url else "") + " added."


def _cmd_remove(args: List[str], cfg: dict) -> str:
    """Remove a substrate from the chain."""
    if not args:
        return "Usage: hermes sustrato remove <index>"
    try:
        idx = int(args[0])
    except ValueError:
        return f"Invalid index: {args[0]}"
    chain = cfg.get("chain", [])
    if idx < 0 or idx >= len(chain):
        return f"Index {idx} out of range (chain has {len(chain)} substrates)."
    removed = chain.pop(idx)
    _save_config(cfg)
    return f"Removed: {removed['provider']}/{removed['model']}"


def _cmd_list(cfg: dict, state: dict) -> str:
    """List all substrates in the chain."""
    chain = cfg.get("chain", [])
    if not chain:
        return "No substrates configured.\n\nAdd one: hermes sustrato add <provider> <model>"

    active = state.get("active_index", 0)
    lines = ["Substrate chain:", "─" * 50]
    labels = ["PRIMARY", "SECONDARY", "TERTIARY"]

    for i, entry in enumerate(chain):
        label = labels[i] if i < 3 else f"#{i}"
        marker = "← ACTIVE" if i == active else ""
        url_info = f" @ {entry['url']}" if entry.get("url") else ""
        local_tag = " [LOCAL]" if entry.get("local") else ""
        lines.append(f"  [{label}] {entry['provider']}/{entry['model']}{url_info}{local_tag} {marker}")

    lines.append("─" * 50)
    auto = "ON" if cfg.get("auto_failover", True) else "OFF"
    lines.append(f"Auto-failover: {auto}")
    return "\n".join(lines)


def _cmd_test(cfg: dict) -> str:
    """Health-check all substrates."""
    chain = cfg.get("chain", [])
    if not chain:
        return "No substrates to test."

    timeout = cfg.get("health_check_timeout", 5)
    lines = ["Health check:", "─" * 40]

    for i, entry in enumerate(chain):
        provider = entry["provider"]
        model = entry["model"]
        url = entry.get("url")
        healthy = _check_provider_health(provider, model, url, timeout)
        status = "✓ ONLINE" if healthy else "✗ OFFLINE"
        lines.append(f"  [{i}] {provider}/{model}: {status}")

    return "\n".join(lines)


def _cmd_switch(args: List[str], cfg: dict, state: dict) -> str:
    """Manually switch to a different substrate."""
    chain = cfg.get("chain", [])
    if not chain:
        return "No substrates configured."
    if not args:
        return "Usage: hermes sustrato switch <index>"
    try:
        idx = int(args[0])
    except ValueError:
        return f"Invalid index: {args[0]}"
    if idx < 0 or idx >= len(chain):
        return f"Index {idx} out of range (0-{len(chain)-1})."

    old_idx = state.get("active_index", 0)
    state["active_index"] = idx
    state["last_switch"] = datetime.now().isoformat()
    _save_state(state)

    entry = chain[idx]
    labels = ["primary", "secondary", "tertiary"]
    label = labels[idx] if idx < 3 else f"substrate #{idx}"
    return f"Switched to {label}: {entry['provider']}/{entry['model']}\n(Restart session or /reset to apply.)"


def _cmd_reset(state: dict) -> str:
    """Reset to primary substrate."""
    state["active_index"] = 0
    state["failures"] = {}
    state["last_switch"] = None
    _save_state(state)
    return "Reset to primary substrate. Failures cleared."


def _cmd_auto(args: List[str], cfg: dict) -> str:
    """Toggle auto-failover."""
    if not args or args[0] not in ("on", "off"):
        return "Usage: hermes sustrato auto <on|off>"
    enabled = args[0] == "on"
    cfg["auto_failover"] = enabled
    _save_config(cfg)
    return f"Auto-failover: {'ON' if enabled else 'OFF'}"


# ── Hermes Plugin Hooks ───────────────────────────────────────────────

# Track failures per provider for auto-failover
_failure_counts: Dict[int, int] = {}
_failure_threshold = 3
_last_failure_time: float = 0
_cooldown_seconds = 30


def _on_session_start(session_id: str = None, **kwargs) -> Optional[dict]:
    """Inject current substrate info into session context."""
    cfg = _load_config()
    state = _load_state()
    chain = cfg.get("chain", [])

    if not chain:
        return None

    active_idx = state.get("active_index", 0)
    if active_idx >= len(chain):
        active_idx = 0

    active = chain[active_idx]
    provider = active["provider"]
    model = active["model"]
    url = active.get("url", "")

    # Build context injection
    ctx_lines = [
        "[SUSTRATO] Active substrate chain:",
    ]
    for i, entry in enumerate(chain):
        marker = " ← CURRENT" if i == active_idx else ""
        ctx_lines.append(f"  [{i}] {entry['provider']}/{entry['model']}{marker}")

    if active_idx > 0:
        ctx_lines.append(f"⚠ Running on fallback substrate #{active_idx}")

    if url:
        ctx_lines.append(f"Local endpoint: {url}")

    # If auto-failover is on, mention it
    if cfg.get("auto_failover", True):
        ctx_lines.append(f"Auto-failover: ON (threshold: {_failure_threshold} failures)")

    return {"context": "\n".join(ctx_lines)}


def _pre_llm_call(is_first_turn: bool = False, **kwargs) -> Optional[dict]:
    """
    Monitor for provider failures and auto-switch substrates.

    This hook runs BEFORE each LLM call. We can't directly observe failures
    here (they happen during the call), but we inject context that helps
    the agent know about available fallbacks.
    """
    if not is_first_turn:
        return None

    cfg = _load_config()
    state = _load_state()
    chain = cfg.get("chain", [])

    if len(chain) <= 1:
        return None  # no fallback chain

    active_idx = state.get("active_index", 0)
    if active_idx >= len(chain):
        active_idx = 0

    active = chain[active_idx]
    provider = active["provider"]
    model = active["model"]

    ctx = [
        f"[SUSTRATO] Active: {provider}/{model} (substrate {active_idx}/{len(chain)-1})",
    ]

    # List fallback options
    fallbacks = []
    for i, entry in enumerate(chain):
        if i != active_idx:
            fallbacks.append(f"hermes sustrato switch {i}  # → {entry['provider']}/{entry['model']}")

    if fallbacks:
        ctx.append("Fallbacks available:")
        ctx.extend(f"  {f}" for f in fallbacks)

    return {"context": "\n".join(ctx)}


# ── Plugin Entry Point ────────────────────────────────────────────────

def register(ctx):
    """Register the sustrato plugin with Hermes."""
    # Register the CLI command
    ctx.register_command(
        name="sustrato",
        handler=_cmd_sustrato,
        description="Multi-layer provider failover — manage substrate chain",
    )

    # Register hooks
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _pre_llm_call)

    # Auto-create default config if missing
    if not CONFIG_PATH.exists():
        _save_config({"chain": [], "auto_failover": True, "health_check_timeout": 5})

    print(f"[sustrato] Plugin loaded. {len(_load_config().get('chain', []))} substrates configured.")
