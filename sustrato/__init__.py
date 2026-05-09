"""
Sustrato — Multi-layer provider failover plugin for Hermes Agent v1.1.0.
"""
import json, os, socket, sys, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

PLUGIN_NAME = "sustrato"

# ── Paths (respect HERMES_HOME) ──────────────────────────────────────
def _hermes_dir() -> Path:
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"])
    return Path.home() / ".hermes"

CONFIG_PATH = _hermes_dir() / "sustrato.yaml"
STATE_PATH  = _hermes_dir() / "sustrato_state.json"

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f: return yaml.safe_load(f) or {}
    return {"chain": [], "auto_failover": True, "health_check_timeout": 5, "fail_threshold": 3}

def _save_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)

def _load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH) as f: return json.load(f)
    return {"active_index": 0, "failures": {}, "last_switch": None, "last_success": None}

def _save_state(state: dict):
    with open(STATE_PATH, "w") as f: json.dump(state, f, indent=2)

# ── Health check ──────────────────────────────────────────────────────
_OLLAMA_ENDPOINTS = ["/api/tags", "/models", "/"]

def _check_provider_health(entry: dict, timeout: int = 5) -> bool:
    provider = entry.get("provider", "")
    url = entry.get("url", "")
    if provider == "ollama" or "localhost" in url or "192.168" in url or "10." in url or "172." in url:
        base = (url or "http://localhost:11434/v1").rstrip("/")
        for ep in _OLLAMA_ENDPOINTS:
            try:
                req = urllib.request.Request(f"{base}{ep}")
                resp = urllib.request.urlopen(req, timeout=timeout)
                if resp.status == 200: return True
            except Exception: continue
        return False
    endpoints = {
        "deepseek": ("api.deepseek.com", 443), "openrouter": ("openrouter.ai", 443),
        "anthropic": ("api.anthropic.com", 443), "openai": ("api.openai.com", 443),
        "openai-codex": ("api.openai.com", 443), "google": ("generativelanguage.googleapis.com", 443),
        "xai": ("api.x.ai", 443), "groq": ("api.groq.com", 443),
    }
    target = endpoints.get(provider)
    if not target: return True
    try:
        socket.create_connection(target, timeout=timeout)
        return True
    except Exception: return False

# ── Auto-failover ─────────────────────────────────────────────────────
def _auto_failover() -> Optional[str]:
    """Check active substrate health. If down, try failover. Returns message or None."""
    cfg = _load_config(); state = _load_state()
    chain = cfg.get("chain", [])
    if len(chain) <= 1 or not cfg.get("auto_failover", True):
        return None

    active = state.get("active_index", 0)
    if active >= len(chain):
        active = 0; state["active_index"] = 0; _save_state(state)

    entry = chain[active]
    healthy = _check_provider_health(entry, cfg.get("health_check_timeout", 5))

    if healthy:
        # Reset failures on success
        state["last_success"] = datetime.now().isoformat()
        key = str(active)
        if key in state.get("failures", {}):
            state["failures"][key] = 0
        _save_state(state)
        return None

    # Record failure
    failures = state.setdefault("failures", {})
    key = str(active)
    failures[key] = failures.get(key, 0) + 1
    threshold = cfg.get("fail_threshold", 3)
    _save_state(state)

    # If threshold reached, switch to next
    if failures[key] >= threshold and active + 1 < len(chain):
        next_idx = active + 1
        state["active_index"] = next_idx
        state["failures"] = {}
        state["last_switch"] = datetime.now().isoformat()
        _save_state(state)
        next_entry = chain[next_idx]
        return (f"⚠ Substrate [{active}] {entry['provider']}/{entry['model']} failed "
                f"{threshold}x. Auto-switched to [{next_idx}] {next_entry['provider']}/{next_entry['model']}.")
    return (f"⚠ Substrate [{active}] {entry['provider']}/{entry['model']} appears OFFLINE "
            f"({failures[key]}/{threshold} failures).")

# ── Hermes Plugin Hooks ───────────────────────────────────────────────
def _on_session_start(session_id: str = None, **kwargs) -> Optional[dict]:
    """Check substrate health and inject active chain into session context."""
    cfg = _load_config(); state = _load_state()
    chain = cfg.get("chain", [])
    if not chain: return None

    # Run auto-failover check
    failover_msg = _auto_failover()
    state = _load_state()  # re-read after possible switch
    active_idx = state.get("active_index", 0)
    if active_idx >= len(chain): active_idx = 0

    active = chain[active_idx]
    lines = ["[SUSTRATO] Active substrate chain:"]
    for i, entry in enumerate(chain):
        marker = " ← CURRENT" if i == active_idx else ""
        url_str = f" @ {entry.get('url','')}" if entry.get('url') else ""
        lines.append(f"  [{i}] {entry['provider']}/{entry['model']}{url_str}{marker}")

    if failover_msg:
        lines.append(f"\n⚠ {failover_msg}")

    if active_idx > 0:
        lines.append(f"⚠ Running on fallback substrate #{active_idx}")

    if cfg.get("auto_failover", True):
        threshold = cfg.get("fail_threshold", 3)
        f_total = sum(state.get("failures", {}).values())
        lines.append(f"Auto-failover: ON (threshold: {threshold}, current failures: {f_total})")

    return {"context": "\n".join(lines)}

def _pre_llm_call(is_first_turn: bool = False, **kwargs) -> Optional[dict]:
    """Inject fallback commands on first turn."""
    if not is_first_turn: return None
    cfg = _load_config(); state = _load_state()
    chain = cfg.get("chain", [])
    if len(chain) <= 1: return None

    active_idx = state.get("active_index", 0)
    if active_idx >= len(chain): active_idx = 0
    active = chain[active_idx]

    ctx = [f"[SUSTRATO] Active: {active['provider']}/{active['model']} (substrate {active_idx}/{len(chain)-1})"]
    fallbacks = []
    for i, entry in enumerate(chain):
        if i != active_idx:
            fallbacks.append(f"  /sustrato switch {i}  # → {entry['provider']}/{entry['model']}")
    if fallbacks:
        ctx.append("Fallbacks available:")
        ctx.extend(fallbacks)
    ctx.append(f"  /sustrato failures     # view failure counters")
    return {"context": "\n".join(ctx)}

# ── CLI Command (slash commands) ──────────────────────────────────────
def _cmd_sustrato(args: List[str]) -> str:
    """Handle /sustrato slash command."""
    cfg = _load_config(); state = _load_state()
    if not args or args[0] in ("help","--help","-h"):
        return ("/sustrato list|test|switch|reset|failures\n"
                "/sustrato add <provider> <model> [--url URL] [--local]\n"
                "/sustrato remove <index>")

    subcmd = args[0]
    chain = cfg.get("chain", [])
    active_idx = state.get("active_index", 0)

    if subcmd == "list":
        if not chain: return "No substrates configured."
        lines = ["Substrate chain:"]
        labels = ["PRIMARY","SECONDARY","TERTIARY"]
        for i, entry in enumerate(chain):
            marker = " ← ACTIVE" if i == active_idx else ""
            lbl = labels[i] if i < 3 else f"#{i}"
            lines.append(f"  [{lbl}] {entry['provider']}/{entry['model']}{marker}")
        return "\n".join(lines)

    elif subcmd == "test":
        if not chain: return "No substrates."
        timeout = cfg.get("health_check_timeout", 5)
        lines = ["Health check:"]
        for i, entry in enumerate(chain):
            healthy = _check_provider_health(entry, timeout)
            lines.append(f"  [{i}] {entry['provider']}/{entry['model']}: {'✓' if healthy else '✗'}")
        return "\n".join(lines)

    elif subcmd == "switch":
        if len(args) < 2: return "Usage: /sustrato switch <index>"
        try: idx = int(args[1])
        except ValueError: return f"Invalid index: {args[1]}"
        if idx < 0 or idx >= len(chain): return f"Index {idx} out of range (0-{len(chain)-1})"
        state["active_index"] = idx; state["failures"] = {}
        state["last_switch"] = datetime.now().isoformat()
        _save_state(state)
        return (f"Switched to [{idx}] {chain[idx]['provider']}/{chain[idx]['model']}\n"
                f"Restart or /reset to apply.")

    elif subcmd == "reset":
        _save_state({"active_index":0, "failures":{}, "last_switch":None, "last_success":None})
        return "Reset to primary. Failures cleared."

    elif subcmd == "failures":
        threshold = cfg.get("fail_threshold", 3)
        failures = state.get("failures", {})
        lines = ["Failure tracking:"]
        for i, entry in enumerate(chain):
            key = str(i); count = failures.get(key, 0)
            bar = "█" * count + "░" * (threshold - count)
            marker = " ← ACTIVE" if i == active_idx else ""
            lines.append(f"  [{i}] {entry['provider']}/{entry['model']}: [{bar}] {count}/{threshold}{marker}")
        return "\n".join(lines)

    elif subcmd == "add":
        return "Use the CLI for adding substrates: sustrato add <provider> <model>"

    elif subcmd == "failover":
        """Force failover: record failure and switch if threshold reached."""
        cfg = _load_config(); state = _load_state()
        chain = cfg.get("chain", [])
        active_idx = state.get("active_index", 0)
        threshold = cfg.get("fail_threshold", 3)
        key = str(active_idx)
        failures = state.setdefault("failures", {})
        failures[key] = failures.get(key, 0) + 1
        if failures[key] >= threshold and active_idx + 1 < len(chain):
            next_idx = active_idx + 1
            state["active_index"] = next_idx
            state["failures"] = {}
            state["last_switch"] = datetime.now().isoformat()
            _save_state(state)
            return (f"⚠ Failover: [{active_idx}]→[{next_idx}] ({chain[next_idx]['provider']}/{chain[next_idx]['model']})\n"
                    f"Restart or /reset to apply.")
        _save_state(state)
        return (f"Failure recorded: {failures[key]}/{threshold} on [{active_idx}] {chain[active_idx]['provider']}\n"
                f"Need {threshold - failures[key]} more failures to switch.\n"
                f"Manual switch: /sustrato switch {active_idx + 1}")

    return f"Unknown: {subcmd}"

# ── Plugin Entry Point ────────────────────────────────────────────────
def register(ctx):
    ctx.register_command(name="sustrato", handler=_cmd_sustrato,
                         description="Multi-layer provider failover — manage substrates")
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    if not CONFIG_PATH.exists():
        _save_config({"chain":[], "auto_failover":True, "health_check_timeout":5, "fail_threshold":3})
    print(f"[sustrato] Plugin v1.1.0 loaded. {len(_load_config().get('chain',[]))} substrates configured.")
