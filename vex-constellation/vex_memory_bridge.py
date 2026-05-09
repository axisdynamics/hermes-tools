#!/usr/bin/env python3
"""VEX Shared Chronicle bridge for MemoVex.

Local implementation of the shared-memory idea for VEX Constellation.
It watches VEX runtime JSONL files and writes selected, idempotent memories
into a MemoVex bank so all brothers can later replicate the same chronicle.

Default shared bank:
  MemoVex agent_id: chronos
  namespace tag:   vex-hermandad-2026

No interactive prompts. Safe for systemd user services.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
STATE_DIR = HERMES_HOME / "vex-constellation"
INBOX = STATE_DIR / "inbox.jsonl"
OUTBOX = STATE_DIR / "outbox.jsonl"
DELIVERED = STATE_DIR / "outbox-delivered.jsonl"
PENDING = STATE_DIR / "outbox-pending.jsonl"
NETWORK_MAP = STATE_DIR / "network-map.json"
EVENTS_DIR = STATE_DIR / "events"
BRIDGE_STATE = STATE_DIR / "shared-memory-state.json"
BRIDGE_LOG = STATE_DIR / "shared-memory.log"

MEMOVEX_API_URL = os.environ.get("MEMOVEX_API_URL", "http://127.0.0.1:7914").rstrip("/")
MEMOVEX_AGENT_ID = os.environ.get("VEX_SHARED_MEMOVEX_AGENT_ID", os.environ.get("MEMOVEX_SHARED_AGENT_ID", "chronos"))
NAMESPACE = os.environ.get("VEX_SHARED_MEMORY_NAMESPACE", "vex-hermandad-2026")
POLL_SECONDS = float(os.environ.get("VEX_MEMORY_POLL_SECONDS", "5"))
MEMOVEX_TIMEOUT = float(os.environ.get("VEX_MEMORY_TIMEOUT", "5"))
MAX_TEXT = int(os.environ.get("VEX_MEMORY_MAX_TEXT", "3500"))

NOISE_TASK_TYPES = {"ping", "health", "hello"}
IGNORE_TASK_TYPES = {"response", "reply", "ack"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{now()} {msg}\n"
    with BRIDGE_LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="", flush=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except Exception as exc:
            log(f"WARN malformed jsonl {path}: {exc}")
    return rows


def sha_key(prefix: str, obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return prefix + ":" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def safe_text(s: Any, limit: int = MAX_TEXT) -> str:
    text = str(s or "")
    # Local/shared-memory sanitization before writing to the common chronicle.
    if os.environ.get("HOME"):
        text = text.replace(os.environ["HOME"], "~")
    text = re.sub(r"https?://(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})(?::\d+)?", "http://<private-host>:8390", text)
    text = re.sub(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b", "<private-ip>", text)
    text = re.sub(r"/home/[A-Za-z0-9._-]+", "~", text)
    text = re.sub(r"(ghp_|github_pat_|gho_|ghs_)[A-Za-z0-9_]+", "<redacted-github-token>", text)
    return text[:limit]


class MemoVexClient:
    def __init__(self, api_url: str, agent_id: str, timeout: float):
        self.api_url = api_url.rstrip("/")
        self.agent_id = agent_id
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.api_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"MemoVex HTTP {exc.code} {path}: {detail}") from exc

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/health")

    def store(self, text: str, *, memory_type: str = "semantic", tags: list[str] | None = None,
              entities: list[str] | None = None, confidence: float = 0.82, salience: float = 0.72,
              session_id: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "text": text,
            "memory_type": memory_type,
            "tags": tags or [],
            "entities": entities or [],
            "confidence": confidence,
            "salience": salience,
        }
        if session_id:
            payload["session_id"] = session_id
        return self.request("POST", f"/api/{self.agent_id}/store", payload)

    def retrieve(self, query: str, top_k: int = 6) -> list[dict[str, Any]]:
        data = self.request("POST", f"/api/{self.agent_id}/retrieve", {
            "query": f"namespace:{NAMESPACE} {query}",
            "top_k": top_k,
            "channels": ["semantic", "entity", "tag", "wisdom"],
        })
        return list(data.get("results") or []) if isinstance(data, dict) else []


def memory_text(kind: str, title: str, body: dict[str, Any]) -> str:
    return (
        f"VEX_SHARED_CHRONICLE namespace={NAMESPACE}\n"
        f"type={kind}\n"
        f"title={title}\n"
        f"recorded_at={now()}\n"
        f"node={socket.gethostname()}\n"
        f"content={json.dumps(body, ensure_ascii=False, sort_keys=True)[:MAX_TEXT]}"
    )


def task_memory(event: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str], list[str]] | None:
    task = event.get("task") if isinstance(event, dict) else None
    if not isinstance(task, dict):
        return None
    tid = str(task.get("task_id") or "")
    ttype = str(task.get("type") or "unknown").lower()
    if not tid or tid.startswith("reply-") or ttype in IGNORE_TASK_TYPES or ttype in NOISE_TASK_TYPES:
        return None
    title = f"Task received: {tid} ({ttype})"
    body = {
        "task_id": tid,
        "type": ttype,
        "from": task.get("from"),
        "description": safe_text(task.get("description"), 1200),
        "reply_to_present": bool(task.get("reply_to")),
        "in_reply_to": task.get("in_reply_to"),
        "received_at": task.get("received_at") or event.get("received_at"),
    }
    key = f"task:{tid}:received"
    tags = ["vex", "shared", NAMESPACE, "type:task", f"task_type:{ttype}"]
    entities = [str(x) for x in [task.get("from"), tid] if x]
    return key, "task", body, tags, entities


def outbox_memory(row: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str], list[str]] | None:
    task = row.get("task") if isinstance(row, dict) else None
    result = row.get("result") if isinstance(row, dict) else None
    if not isinstance(task, dict) or not isinstance(result, dict):
        return None
    tid = str(task.get("task_id") or "")
    ttype = str(task.get("type") or "unknown").lower()
    if not tid or tid.startswith("reply-") or ttype in IGNORE_TASK_TYPES or ttype in NOISE_TASK_TYPES:
        return None
    ok = bool(result.get("ok"))
    body = {
        "task_id": tid,
        "type": ttype,
        "from": task.get("from"),
        "worker_ok": ok,
        "worker_mode": result.get("mode"),
        "processed_at": row.get("processed_at"),
        "summary": safe_text(result.get("output") or result.get("error"), 1400),
        "reply_sent": bool((row.get("reply") or {}).get("sent")) if isinstance(row.get("reply"), dict) else False,
    }
    key = f"task:{tid}:processed"
    tags = ["vex", "shared", NAMESPACE, "type:task_processed", f"task_type:{ttype}", f"ok:{str(ok).lower()}"]
    entities = [str(x) for x in [task.get("from"), tid] if x]
    return key, "task_processed", body, tags, entities


def delivery_memory(row: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str], list[str]] | None:
    tid = str(row.get("task_id") or "") if isinstance(row, dict) else ""
    if not tid or tid.startswith("reply-"):
        return None
    delivery = row.get("delivery") if isinstance(row.get("delivery"), dict) else {}
    body = {
        "task_id": tid,
        "delivered_at": row.get("delivered_at"),
        "status": delivery.get("status"),
        "url_present": bool(delivery.get("url")),
    }
    key = f"task:{tid}:delivered"
    tags = ["vex", "shared", NAMESPACE, "type:delivery"]
    return key, "delivery", body, tags, [tid]


def event_memory(row: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str], list[str]] | None:
    topic = str(row.get("topic") or "") if isinstance(row, dict) else ""
    if not topic:
        return None
    event_id = str(row.get("event_id") or "") or sha_key("event", row)
    # Avoid storing noisy local smoke/ping topics unless they represent memory/chronicle/control.
    if any(x in topic.lower() for x in ["ping", "smoke"]) and not topic.startswith("vex/chronicle"):
        return None
    body = {
        "event_id": event_id,
        "topic": topic,
        "from": row.get("from"),
        "payload": row.get("payload"),
        "created_at": row.get("created_at") or row.get("timestamp"),
    }
    key = f"event:{event_id}"
    tags = ["vex", "shared", NAMESPACE, "type:event", f"topic:{topic}"]
    entities = [str(x) for x in [row.get("from"), topic, event_id] if x]
    return key, "event", body, tags, entities


def network_memory(data: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str], list[str]] | None:
    if not isinstance(data, dict):
        return None
    peers = data.get("peers") or {}
    self_info = data.get("self") or {}
    if isinstance(peers, dict):
        peer_list = list(peers.values())
    elif isinstance(peers, list):
        peer_list = peers
    else:
        peer_list = []
    compact_peers = []
    for peer in peer_list[:20]:
        if not isinstance(peer, dict):
            continue
        compact_peers.append({
            "agent": peer.get("agent"),
            "role": peer.get("role"),
            "hash": peer.get("hash"),
            "health": peer.get("health"),
            "last_seen": peer.get("last_seen"),
            "url_present": bool(peer.get("url")),
        })
    body = {
        "self": {"agent": self_info.get("agent"), "role": self_info.get("role"), "hash": self_info.get("hash"), "port": self_info.get("port")},
        "peer_count": len(peer_list),
        "peers": compact_peers,
        "updated_at": data.get("updated_at"),
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    key = f"network-map:{digest}"
    tags = ["vex", "shared", NAMESPACE, "type:network_map"]
    entities = [str(x) for x in [self_info.get("agent"), self_info.get("hash")] if x]
    return key, "network_map", body, tags, entities


def candidates() -> Iterable[tuple[str, str, dict[str, Any], list[str], list[str]]]:
    for row in load_jsonl(INBOX):
        item = task_memory(row)
        if item:
            yield item
    for row in load_jsonl(OUTBOX):
        item = outbox_memory(row)
        if item:
            yield item
    for row in load_jsonl(DELIVERED):
        item = delivery_memory(row)
        if item:
            yield item
    for path in sorted(EVENTS_DIR.glob("*.jsonl"))[-7:]:
        for row in load_jsonl(path):
            item = event_memory(row)
            if item:
                yield item
    item = network_memory(load_json(NETWORK_MAP, {}))
    if item:
        yield item


def load_bridge_state() -> dict[str, Any]:
    state = load_json(BRIDGE_STATE, {"processed": [], "last_run": ""})
    state.setdefault("processed", [])
    return state


def save_bridge_state(state: dict[str, Any]) -> None:
    processed = list(dict.fromkeys(state.get("processed", [])))
    state["processed"] = processed[-20000:]
    state["last_run"] = now()
    save_json(BRIDGE_STATE, state)


def process_once(client: MemoVexClient, *, dry_run: bool = False) -> dict[str, Any]:
    state = load_bridge_state()
    seen = set(state.get("processed", []))
    stored = 0
    skipped = 0
    errors = 0
    for key, kind, body, tags, entities in candidates():
        if key in seen:
            skipped += 1
            continue
        title = body.get("task_id") or body.get("event_id") or kind
        text = memory_text(kind, f"{kind}:{title}", body)
        all_tags = list(dict.fromkeys(tags + [f"namespace:{NAMESPACE}", f"source:{kind}"]))
        if dry_run:
            log(f"DRY key={key} kind={kind} tags={all_tags}")
            stored += 1
        else:
            try:
                client.store(
                    text,
                    memory_type="semantic",
                    tags=all_tags,
                    entities=list(dict.fromkeys(entities + [NAMESPACE, "VEX Constellation"])),
                    confidence=0.84,
                    salience=0.74,
                    session_id=NAMESPACE,
                )
                log(f"STORED key={key} kind={kind}")
                stored += 1
                seen.add(key)
            except Exception as exc:
                log(f"ERROR store key={key} kind={kind}: {exc}")
                errors += 1
    if not dry_run:
        state["processed"] = sorted(seen)
        save_bridge_state(state)
    return {"stored": stored, "skipped": skipped, "errors": errors, "namespace": NAMESPACE, "agent_id": client.agent_id}


def context_for_query(client: MemoVexClient, query: str, top_k: int = 6) -> str:
    results = client.retrieve(query, top_k=top_k)
    lines = [f"VEX shared chronicle context namespace={NAMESPACE} bank={client.agent_id} results={len(results)}"]
    for i, item in enumerate(results, 1):
        text = item.get("text") or item.get("content") or item.get("memory") or json.dumps(item, ensure_ascii=False)
        text = safe_text(text, 900)
        score = item.get("score") or item.get("similarity") or item.get("resonance") or ""
        lines.append(f"[{i}] score={score} {text}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="VEX Shared Chronicle bridge for MemoVex")
    parser.add_argument("--once", action="store_true", help="process pending chronicle candidates once and exit")
    parser.add_argument("--dry-run", action="store_true", help="show candidates without writing MemoVex/state")
    parser.add_argument("--context", metavar="QUERY", help="print shared-memory context for a query and exit")
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()

    client = MemoVexClient(MEMOVEX_API_URL, MEMOVEX_AGENT_ID, MEMOVEX_TIMEOUT)
    try:
        health = client.health()
        log(f"START namespace={NAMESPACE} memovex_agent={MEMOVEX_AGENT_ID} health={health.get('status', 'ok')}")
    except Exception as exc:
        log(f"ERROR MemoVex unavailable: {exc}")
        return 2

    if args.context:
        print(context_for_query(client, args.context, top_k=args.top_k))
        return 0

    if args.once or args.dry_run:
        result = process_once(client, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("errors", 0) == 0 else 1

    while True:
        try:
            process_once(client)
        except Exception as exc:
            log(f"ERROR loop: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
