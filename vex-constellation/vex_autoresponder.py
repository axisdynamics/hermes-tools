#!/usr/bin/env python3
"""Autonomous bridge for VEX Constellation.

Watches ~/.hermes/vex-constellation/inbox.jsonl, runs an isolated Hermes
one-shot for new tasks, stores results in outbox.jsonl, and optionally posts a
response task back to the peer when a reply URL is available.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
STATE_DIR = HERMES_HOME / "vex-constellation"
INBOX = STATE_DIR / "inbox.jsonl"
OUTBOX = STATE_DIR / "outbox.jsonl"
STATE = STATE_DIR / "autoresponder-state.json"
LOG = STATE_DIR / "autoresponder.log"
LOCAL_REPLY_URL = os.environ.get("VEX_LOCAL_URL", "http://127.0.0.1:8390")
DEFAULT_TOOLSETS = os.environ.get(
    "VEX_AUTORESPONDER_TOOLSETS",
    "terminal,file,web,skills,memory,session_search",
)
HERMES_TIMEOUT = int(os.environ.get("VEX_HERMES_TIMEOUT", "900"))
POLL_SECONDS = float(os.environ.get("VEX_POLL_SECONDS", "2"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{now()} {msg}\n"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="", flush=True)


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_state() -> dict[str, Any]:
    if not STATE.exists():
        return {"processed": []}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"processed": []}


def save_state(state: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE)


def iter_inbox() -> list[dict[str, Any]]:
    if not INBOX.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in INBOX.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except Exception as exc:
            log(f"WARN malformed inbox line: {exc}")
    return events


def build_prompt(event: dict[str, Any]) -> str:
    task = event.get("task", {})
    raw = event.get("raw", {})
    return f"""Eres un Hermes autónomo activado por VEX Constellation.

Contexto:
- Este run fue disparado por una tarea entrante HTTP, sin interacción humana en vivo.
- Responde y actúa sólo dentro del alcance explícito de la tarea.
- Evita acciones destructivas, gasto de dinero, publicación externa o cambios irreversibles salvo que la tarea lo pida de forma inequívoca y segura.
- Si falta contexto, produce un reporte claro en vez de inventar.
- Escribe una respuesta final breve y útil para devolver al peer.

Tarea VEX:
{json.dumps(task, ensure_ascii=False, indent=2)}

Payload crudo:
{json.dumps(raw, ensure_ascii=False, indent=2)}
"""


def run_hermes(event: dict[str, Any]) -> dict[str, Any]:
    task = event.get("task", {})
    task_type = str(task.get("type", "")).lower()
    description = str(task.get("description", ""))

    # Fast local path for health/ping tests; validates the bridge without LLM cost.
    if task_type in {"ping", "health"}:
        return {
            "ok": True,
            "mode": "local",
            "output": f"VEX autoresponder alive at {now()}. Received: {description}",
        }

    prompt = build_prompt(event)
    cmd = [
        "hermes",
        "chat",
        "-Q",
        "--source",
        "vex-constellation",
        "-t",
        DEFAULT_TOOLSETS,
        "-q",
        prompt,
    ]
    log(f"RUN task_id={task.get('task_id')} cmd={' '.join(cmd[:7])} ...")
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=HERMES_TIMEOUT,
            cwd=str(HERMES_HOME),
        )
        return {
            "ok": proc.returncode == 0,
            "mode": "hermes",
            "returncode": proc.returncode,
            "output": proc.stdout[-20000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "mode": "hermes",
            "error": f"timeout after {HERMES_TIMEOUT}s",
            "output": (exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
        }
    except Exception as exc:
        return {"ok": False, "mode": "hermes", "error": repr(exc), "output": ""}


def response_url(event: dict[str, Any]) -> str | None:
    task = event.get("task", {})
    raw = event.get("raw", {})
    for key in ("reply_to", "from_url", "url"):
        val = task.get(key) or raw.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val.rstrip("/")
    # Conservative fallback: if peer sent from a LAN IP, answer on 8390.
    remote = event.get("remote_addr")
    if isinstance(remote, str) and remote not in {"127.0.0.1", "127.0.1.1", "::1"}:
        return f"http://{remote}:8390"
    return None


def post_response(event: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
    task = event.get("task", {})
    # Avoid response storms.
    if str(task.get("type", "")).lower() in {"response", "reply", "ack"}:
        return None
    url = response_url(event)
    if not url:
        return {"sent": False, "reason": "no reply_to/from_url/url available"}
    body = {
        "task_id": f"reply-{task.get('task_id', int(time.time()))}",
        "type": "response",
        "from": "hermes-vex-autoresponder",
        "reply_to": LOCAL_REPLY_URL,
        "in_reply_to": task.get("task_id"),
        "description": result.get("output", "")[-8000:] or result.get("error", ""),
        "ok": result.get("ok"),
        "created_at": now(),
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{url}/task",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"sent": True, "url": url, "status": resp.status, "body": resp.read().decode("utf-8", "replace")[:2000]}
    except Exception as exc:
        return {"sent": False, "url": url, "error": repr(exc)}


def process_once() -> int:
    state = load_state()
    processed = set(state.get("processed", []))
    count = 0
    for event in iter_inbox():
        task = event.get("task", {})
        task_id = str(task.get("task_id") or "")
        if not task_id or task_id in processed:
            continue
        if str(task.get("type", "")).lower() in {"response", "reply", "ack"}:
            processed.add(task_id)
            continue
        log(f"PROCESS task_id={task_id} from={task.get('from')} type={task.get('type')}")
        result = run_hermes(event)
        reply = post_response(event, result)
        record = {"processed_at": now(), "task": task, "result": result, "reply": reply}
        append_jsonl(OUTBOX, record)
        processed.add(task_id)
        state["processed"] = sorted(processed)
        save_state(state)
        count += 1
        log(f"DONE task_id={task_id} ok={result.get('ok')} reply={reply}")
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process pending inbox once and exit")
    args = parser.parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log(f"VEX autoresponder start inbox={INBOX} outbox={OUTBOX}")
    if args.once:
        return 0 if process_once() >= 0 else 1
    while True:
        try:
            process_once()
        except KeyboardInterrupt:
            log("VEX autoresponder stop")
            return 0
        except Exception as exc:
            log(f"ERROR {repr(exc)}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
