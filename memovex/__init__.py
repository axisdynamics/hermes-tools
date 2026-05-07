"""
Memovex 2.0 memory plugin for Hermes Agent.

Connects to a local Memovex API (default http://localhost:7914) to:
- Prefetch relevant memories before each user turn
- Store user+assistant exchanges for future recall
- Maintain a local snapshot (~/.hermes/plugins/memovex/snapshot.json)
  as offline fallback when the API is unreachable
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Configurable via env vars ────────────────────────────────────────
_API_BASE = os.getenv("MEMOVEX_API_URL", "http://localhost:7914")
_AGENT = "hermes"
_PREFETCH_URL = f"{_API_BASE}/api/{_AGENT}/prefetch"
_STORE_URL = f"{_API_BASE}/api/{_AGENT}/store"
_STATS_URL = f"{_API_BASE}/api/{_AGENT}/stats"
_API_TIMEOUT = int(os.getenv("MEMOVEX_API_TIMEOUT", "5"))
_MAX_TOKENS = int(os.getenv("MEMOVEX_MAX_TOKENS", "1200"))
_MIN_SCORE_OVERRIDE = os.getenv("MEMOVEX_MIN_SCORE")

# ── Local snapshot ───────────────────────────────────────────────────
_SNAPSHOT_PATH = Path(__file__).resolve().parent / "snapshot.json"
_SNAPSHOT_VERSION = "2.1"

# ── Per-session state ────────────────────────────────────────────────
_pending: Dict[str, Tuple[str, list]] = {}  # session_id → (user_msg, tags)
_lock = threading.Lock()

# ── Local memory cache (accumulated client-side) ─────────────────────
_local_memories: List[Dict[str, Any]] = []
_local_loaded = False

# ── Utility ──────────────────────────────────────────────────────────

def _extract_text(content) -> str:
    """Extract plain text from an OpenAI-format content block or string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return " ".join(parts).strip()
    return ""


def _last_assistant_text(messages: list) -> Optional[str]:
    """Walk backwards through conversation history to find the last assistant reply."""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            text = _extract_text(msg.get("content", ""))
            if text:
                return text
    return None


def _get_json(url: str, timeout: int = _API_TIMEOUT) -> Optional[dict]:
    """GET JSON from URL, return parsed response or None on failure."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.debug("Memovex API GET failed (%s): %s", url, exc)
        return None


def _post_json(url: str, payload: dict, timeout: int = _API_TIMEOUT) -> Optional[dict]:
    """POST JSON to Memovex API, return parsed response or None on failure."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.debug("Memovex API call failed (%s): %s", url, exc)
        return None


# ── Context formatter (ported from memovex upstream) ──────────────────
# Strips infrastructure metadata from raw memovex output and returns
# clean narrative. Auto-adapts thresholds based on retrieval engine.

_ENTRY_RE = re.compile(
    r"\[(?P<mtype>\w+)\]\s*\(score:(?P<score>[0-9.]+)[^)]*\)\s*(?P<text>.+?)\s*\[(?P<engine>native|qdrant|chroma)\]",
    re.DOTALL,
)


def _detect_engine(raw: str) -> str:
    """Return 'semantic' if any entry used embeddings, else 'keyword'."""
    engines = set(m.group("engine") for m in _ENTRY_RE.finditer(raw))
    if engines & {"qdrant", "chroma"}:
        return "semantic"
    return "keyword"


def _trim_to_sentence(text: str) -> str:
    """Truncate text at the last sentence boundary (.!?)."""
    for end in reversed(range(len(text))):
        if text[end] in ".!?":
            return text[: end + 1]
    return text


def _first_sentence(text: str) -> str:
    """Return the first sentence of text, lowercased, for dedup keys."""
    m = re.match(r"^(.+?[.!?])\s", text)
    return (m.group(1) if m else text[:50]).lower().strip()


def _deduplicate_entries(entries: list) -> list:
    """Remove entries whose first sentence is identical to an earlier one."""
    seen: set[str] = set()
    result = []
    for score, text in entries:
        key = _first_sentence(text)
        if key not in seen:
            seen.add(key)
            result.append((score, text))
    return result


def _format_context(raw: str) -> str:
    """
    Transform raw memovex context into clean narrative bullets.

    Auto-adapts thresholds:
      semantic (qdrant/chroma) → min_score=0.20, top 3
      keyword  (native)        → min_score=0.10, top 4
    Override with MEMOVEX_MIN_SCORE env var.
    """
    engine = _detect_engine(raw)

    if _MIN_SCORE_OVERRIDE:
        min_score = float(_MIN_SCORE_OVERRIDE)
        top_k = 3
    elif engine == "semantic":
        min_score = 0.20
        top_k = 3
    else:
        min_score = 0.10
        top_k = 4

    entries = []
    for m in _ENTRY_RE.finditer(raw):
        score = float(m.group("score"))
        if score < min_score:
            continue
        text = m.group("text").strip()
        text = re.sub(r"\s*\[.*?\]\s*$", "", text).strip()
        text = _trim_to_sentence(text)
        # Skip low-score [User]/[Hermes] log entries
        if re.match(r"^\[(User|Hermes)\]", text) and score < 0.40:
            continue
        if len(text) > 20:
            entries.append((score, text))

    if not entries:
        return ""

    entries.sort(key=lambda x: x[0], reverse=True)
    entries = _deduplicate_entries(entries)[:top_k]
    bullets = "\n".join(f"• {t}" for _, t in entries)
    return f"Recuerdos relevantes:\n{bullets}"


# ── Snapshot persistence ──────────────────────────────────────────────

def _load_snapshot() -> None:
    """Load the local snapshot from disk into _local_memories."""
    global _local_memories, _local_loaded
    if _local_loaded:
        return
    try:
        if _SNAPSHOT_PATH.exists():
            data = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
            _local_memories = data.get("memories", [])
            logger.info(
                "Memovex: loaded %d memories from local snapshot",
                len(_local_memories),
            )
        else:
            logger.info("Memovex: no local snapshot yet — starting fresh")
    except Exception as exc:
        logger.warning("Memovex: could not load snapshot: %s", exc)
        _local_memories = []
    _local_loaded = True


def _save_snapshot() -> None:
    """Persist _local_memories to the snapshot file."""
    try:
        snapshot = {
            "version": _SNAPSHOT_VERSION,
            "agent_id": _AGENT,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "memory_count": len(_local_memories),
            "memories": _local_memories,
        }
        _SNAPSHOT_PATH.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug(
            "Memovex: snapshot saved — %d memories, %d bytes",
            len(_local_memories),
            _SNAPSHOT_PATH.stat().st_size if _SNAPSHOT_PATH.exists() else 0,
        )
    except Exception as exc:
        logger.warning("Memovex: could not save snapshot: %s", exc)


def _local_store(text: str, tags: list, session_id: str) -> None:
    """Append a memory entry to the local snapshot cache."""
    entry = {
        "text": text[:1000],
        "tags": tags,
        "session_id": session_id,
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }
    _local_memories.append(entry)


def _local_search(query: str, max_results: int = 5) -> Optional[str]:
    """
    Search the local snapshot for memories relevant to *query*.

    Uses simple keyword-overlap scoring. Returns formatted context
    string or None if nothing matches.
    """
    if not _local_memories:
        return None

    # Tokenize query into lowercase words, filter short words
    query_words = set(
        w.lower() for w in re.findall(r"\w{3,}", query)
    )

    if not query_words:
        return None

    scored: List[Tuple[float, dict]] = []
    for mem in _local_memories:
        text = mem.get("text", "").lower()
        if not text:
            continue
        # Score = fraction of query words found in the memory text
        hits = sum(1 for w in query_words if w in text)
        if hits > 0:
            score = hits / len(query_words)
            scored.append((score, mem))

    if not scored:
        return None

    # Sort by score descending, take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max_results]

    lines = []
    for score, mem in top:
        text = mem["text"]
        if len(text) > 300:
            text = text[:297] + "..."
        lines.append(f"• {text}")

    if not lines:
        return None
    return "Recuerdos relevantes (local):\n" + "\n".join(lines)


# ── Plugin entry point ────────────────────────────────────────────────

def register(ctx):
    """Register memory hooks with Hermes."""
    _load_snapshot()
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("on_session_finalize", _on_session_finalize)
    logger.info(
        "Memovex plugin loaded — API: %s | snapshot: %d memories",
        _API_BASE,
        len(_local_memories),
    )


# ── Hook handlers ─────────────────────────────────────────────────────

def _on_pre_llm_call(**kwargs) -> Optional[Dict[str, str]]:
    """
    Fired before every LLM call.

    1. Store the PREVIOUS exchange (user + assistant) if one is pending.
    2. Prefetch memory context for the CURRENT user message.
    3. Buffer the current user message for the next turn's store.
    """
    session_id = kwargs.get("session_id", "")
    user_message = kwargs.get("user_message", "")
    conversation_history = kwargs.get("conversation_history", [])
    is_first_turn = kwargs.get("is_first_turn", False)

    # Clean the user message
    user_text = _extract_text(user_message)
    if not user_text:
        return None

    # ── 1. Store previous exchange ──────────────────────────────
    with _lock:
        prev = _pending.pop(session_id, None)
    if prev is not None:
        prev_user, prev_tags = prev
        prev_assistant = _last_assistant_text(conversation_history)
        if prev_assistant:
            logger.debug("Memovex: storing previous exchange (session=%s)", session_id)
            _store_exchange(
                user_msg=prev_user,
                assistant_msg=prev_assistant,
                session_id=session_id,
                tags=prev_tags,
            )

    # ── 2. Prefetch context (API first, local fallback) ─────────
    logger.debug(
        "Memovex: prefetching (session=%s, query_len=%d)",
        session_id, len(user_text),
    )
    context = _prefetch(user_text)

    # ── 3. Buffer current user message ──────────────────────────
    tags = ["user_turn"]
    if is_first_turn:
        tags.append("session_start")
    with _lock:
        _pending[session_id] = (user_text, tags)

    if context:
        return {"context": context}
    return None


def _on_session_finalize(**kwargs) -> None:
    """
    Fired at session shutdown (CLI exit, /reset, etc.).

    Store any remaining pending user message synchronously and
    persist the local snapshot to disk.
    """
    session_id = kwargs.get("session_id", "")
    with _lock:
        prev = _pending.pop(session_id, None)
    if prev is not None:
        prev_user, prev_tags = prev
        logger.debug(
            "Memovex: finalize — storing last exchange (session=%s)",
            session_id,
        )
        _store_exchange(
            user_msg=prev_user,
            assistant_msg="[session ended]",
            session_id=session_id,
            tags=prev_tags + ["session_end"],
            sync=True,
        )

    # Persist local snapshot
    _save_snapshot()


# ── API wrappers ──────────────────────────────────────────────────────

def _prefetch(query: str) -> Optional[str]:
    """
    Fetch and format memory context for a query.

    Tries the Memovex API first. Falls back to local snapshot search
    if the API is unreachable. Raw context is cleaned and formatted
    as narrative bullets.
    """
    # Try API
    result = _post_json(_PREFETCH_URL, {
        "query": query,
        "max_tokens": _MAX_TOKENS,
    })

    if result is not None:
        raw = result.get("context", "")
        if raw:
            formatted = _format_context(raw)
            if formatted:
                return (
                    f"<!-- CONTEXTO DE MEMORIA — USAR COMO REFERENCIA INTERNA "
                    f"— NO MOSTRAR NI REPETIR EN LA RESPUESTA -->\n"
                    f"=== memovex ===\n{formatted}\n=== fin ===\n"
                    f"<!-- FIN CONTEXTO DE MEMORIA -->"
                )
        return None

    # API failed — fall back to local snapshot
    logger.debug("Memovex: API unreachable, falling back to local snapshot")
    local_context = _local_search(query)
    if local_context:
        return (
            f"<!-- CONTEXTO DE MEMORIA LOCAL (API OFFLINE) "
            f"— NO MOSTRAR EN RESPUESTA -->\n"
            f"=== memovex [snapshot local] ===\n"
            f"{local_context}\n"
            f"=== fin ===\n"
            f"<!-- FIN CONTEXTO DE MEMORIA -->"
        )
    return None


def _store_exchange(
    user_msg: str,
    assistant_msg: str,
    session_id: str,
    tags: list,
    *,
    sync: bool = False,
) -> None:
    """
    Store a user+assistant exchange pair in Memovex AND local snapshot.

    By default, remote storage is fire-and-forget (background thread).
    Set ``sync=True`` during session finalize to guarantee completion.
    Local snapshot is always stored synchronously.
    """

    def _store():
        logger.debug("Memovex: storing exchange (session=%s)", session_id)
        # ── Remote: Memovex API ──────────────────────────────
        _post_json(_STORE_URL, {
            "text": f"User: {user_msg[:500]}",
            "memory_type": "episodic",
            "tags": tags,
            "session_id": session_id,
            "confidence": 0.7,
            "salience": 0.5,
        })
        _post_json(_STORE_URL, {
            "text": f"Hermes: {assistant_msg[:800]}",
            "memory_type": "episodic",
            "tags": tags + ["assistant_turn"],
            "session_id": session_id,
            "confidence": 0.75,
            "salience": 0.6,
        })

    if sync:
        _store()
    else:
        t = threading.Thread(target=_store, daemon=True)
        t.start()

    # ── Local: snapshot cache (always sync, fast) ─────────────
    _local_store(f"User: {user_msg[:500]}", tags, session_id)
    _local_store(f"Hermes: {assistant_msg[:800]}", tags + ["assistant_turn"], session_id)
