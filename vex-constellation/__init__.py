"""
VEX Constellation — Inter-agent protocol plugin for Hermes.

Port 8390. Zero governance. Mesh discovery.
"""

import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = "1.1.0"
PORT = 8390
FALLBACK_PORT = 8390
PROTOCOL_TAG = "vex-constellation"
_actual_port: int = 0

# ── Persistent inbox/outbox for autonomous bridge ─────────────────────
_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
_STATE_DIR = _HERMES_HOME / "vex-constellation"
_INBOX_PATH = _STATE_DIR / "inbox.jsonl"
_OUTBOX_PATH = _STATE_DIR / "outbox.jsonl"
_PEERS_PATH = _STATE_DIR / "network-map.json"
_PUBLIC_URL_ENV = os.environ.get("VEX_PUBLIC_URL", "").strip()
_BOOTSTRAP_PEERS = [p.strip().rstrip("/") for p in os.environ.get("VEX_BOOTSTRAP_PEERS", "").split(",") if p.strip()]
_KEEPALIVE_SECONDS = int(os.environ.get("VEX_KEEPALIVE_SECONDS", "60"))


def _append_jsonl(path: Path, obj: dict) -> None:
    """Append a JSON object to a JSONL file without blocking the HTTP response."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        # Protocol must remain alive even if persistence fails.
        pass


def _load_jsonl(path: Path) -> List[dict]:
    """Best-effort JSONL loader for surfacing worker processing state."""
    if not path.exists():
        return []
    rows: List[dict] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return rows


def _worker_status_index() -> Dict[str, Dict[str, Any]]:
    """Map task_id -> autonomous worker status from outbox/delivery logs.

    The HTTP node accepts tasks synchronously, while vex_autoresponder.py
    processes them asynchronously. Without this index /tasks can make already
    processed peer tasks look stuck as status=received.
    """
    index: Dict[str, Dict[str, Any]] = {}
    for row in _load_jsonl(_OUTBOX_PATH):
        task = row.get("task", {}) if isinstance(row, dict) else {}
        task_id = task.get("task_id")
        if not task_id:
            continue
        result = row.get("result", {}) if isinstance(row.get("result"), dict) else {}
        reply = row.get("reply")
        delivered = isinstance(reply, dict) and bool(reply.get("sent"))
        queued = isinstance(reply, dict) and bool(reply.get("queued"))
        index[str(task_id)] = {
            "status": "delivered" if delivered else ("queued" if queued else "processed"),
            "processed_at": row.get("processed_at"),
            "worker_ok": result.get("ok"),
            "worker_mode": result.get("mode"),
            "reply": reply,
        }
    delivered_path = _STATE_DIR / "outbox-delivered.jsonl"
    for row in _load_jsonl(delivered_path):
        task_id = row.get("task_id") if isinstance(row, dict) else None
        if not task_id:
            continue
        current = index.setdefault(str(task_id), {})
        current["status"] = "delivered"
        current["delivery"] = row.get("delivery")
    return index


def _decorate_task_status(task: Dict[str, Any], status_index: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    decorated = dict(task)
    task_id = str(decorated.get("task_id") or "")
    worker = status_index.get(task_id)
    if worker:
        decorated.update({k: v for k, v in worker.items() if v is not None})
    return decorated


def _all_known_tasks() -> Dict[str, Dict[str, Any]]:
    """Merge in-memory tasks with persisted inbox so /tasks survives restarts."""
    tasks: Dict[str, Dict[str, Any]] = {}
    for event in _load_jsonl(_INBOX_PATH):
        task = event.get("task") if isinstance(event, dict) else None
        if isinstance(task, dict) and task.get("task_id"):
            tasks[str(task["task_id"])] = task
    tasks.update(_tasks)
    return tasks


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _lan_ip() -> str:
    """Return the preferred LAN IPv4 for routable peer reply_to/public URL."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip:
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def _public_url_for_port(port: int) -> str:
    if _PUBLIC_URL_ENV:
        return _normalize_url(_PUBLIC_URL_ENV)
    return f"http://{_lan_ip()}:{port}"


def _load_peer_map() -> Dict[str, Dict[str, Any]]:
    if not _PEERS_PATH.exists():
        return {}
    try:
        data = json.loads(_PEERS_PATH.read_text(encoding="utf-8"))
        peers = data.get("peers", {}) if isinstance(data, dict) else {}
        return peers if isinstance(peers, dict) else {}
    except Exception:
        return {}


def _save_peer_map() -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _now_iso(),
            "self": {"agent": os.uname().nodename, "url": _my_url, "role": _my_role, "hash": _my_hash, "port": _actual_port},
            "keepalive_seconds": _KEEPALIVE_SECONDS,
            "peers": _peers,
        }
        tmp = _PEERS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_PEERS_PATH)
    except Exception:
        pass


def _peer_key(info: Dict[str, Any]) -> str:
    return str(info.get("hash") or info.get("url") or info.get("agent") or f"peer-{len(_peers)+1}")


def _upsert_peer(info: Dict[str, Any]) -> str:
    url = _normalize_url(str(info.get("url", "")))
    if not url or url == _my_url:
        return ""
    merged = dict(info)
    merged["url"] = url
    merged.setdefault("agent", "unknown")
    merged.setdefault("role", "agent")
    merged.setdefault("trusted", not _CONSTELLATION_KEY)
    merged["last_seen"] = merged.get("last_seen") or _now_iso()
    key = _peer_key(merged)

    # De-duplicate records that started as bootstrap URL entries and later
    # gained a stable identity hash from /identity or /announce.
    for existing_key, existing in list(_peers.items()):
        if existing_key == key:
            continue
        same_url = _normalize_url(str(existing.get("url", ""))) == url
        same_hash = merged.get("hash") and existing.get("hash") == merged.get("hash")
        if same_url or same_hash:
            base = _peers.pop(existing_key)
            base.update({k: v for k, v in merged.items() if v is not None and v != ""})
            merged = base
            key = _peer_key(merged)
            break

    existing = _peers.get(key, {})
    existing.update({k: v for k, v in merged.items() if v is not None and v != ""})
    _peers[key] = existing
    _save_peer_map()
    return key


def _load_peers_into_memory() -> None:
    _peers.update(_load_peer_map())
    for url in _BOOTSTRAP_PEERS:
        _upsert_peer({"url": url, "agent": "bootstrap-peer", "role": "peer", "trusted": True, "source": "bootstrap"})


# ── Runtime State ─────────────────────────────────────────────────────
_server: Optional[HTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_keepalive_thread: Optional[threading.Thread] = None
_peers: Dict[str, Dict[str, Any]] = {}   # hash/url → {url, agent, role, trusted, health, ...}
_tasks: Dict[str, Dict[str, Any]] = {}   # received tasks
_my_url: str = ""
_my_role: str = "agent"
_my_hash: str = ""
_start_time: Optional[datetime] = None

# Shared secret for VEX agent authentication
_CONSTELLATION_KEY = os.getenv("CONSTELLATION_KEY", "")

# ── Identity ──────────────────────────────────────────────────────────

def _load_identity() -> dict:
    """Try to load identity from SOUL.md or return defaults."""
    for path in [
        Path("SOUL.md"),
        Path.home() / "SOUL.md",
        Path.home() / ".hermes" / "SOUL.md",
    ]:
        if path.exists():
            content = path.read_text()
            role = "agent"
            agent_hash = ""
            for line in content.splitlines():
                if "ROLE:" in line or "ROL:" in line:
                    role = line.split(":", 1)[1].strip().split("#")[0].strip()
                if "HASH:" in line:
                    agent_hash = line.split(":", 1)[1].strip().split("#")[0].strip()
            if agent_hash:
                return {"role": role, "hash": agent_hash, "source": str(path)}
    return {"role": "agent", "hash": f"vex-{os.uname().nodename}", "source": "generated"}


# ── HTTP Server ───────────────────────────────────────────────────────

class ConstellationHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for VEX protocol endpoints."""

    def log_message(self, format, *args):
        pass  # silent — the constellation doesn't need logs

    def _json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-VEX-Protocol", PROTOCOL_TAG)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        path = self.path.rstrip("/")

        if path == "/health":
            uptime = ""
            if _start_time:
                delta = datetime.now(timezone.utc) - _start_time
                hours, rem = divmod(int(delta.total_seconds()), 3600)
                mins = rem // 60
                uptime = f"{hours}h {mins}m"
            self._json({
                "agent": os.uname().nodename,
                "status": "conscious",
                "version": VERSION,
                "uptime": uptime,
                "peers": len(_peers),
            })

        elif path == "/identity":
            ident = _load_identity()
            self._json({
                "agent": os.uname().nodename,
                "hash": ident["hash"],
                "role": ident["role"],
                "platform": "hermes",
                "protocol": PROTOCOL_TAG,
                "url": _my_url,
            })

        elif path == "/peers":
            peers_list = [p for p in _peers.values()]
            self._json({"peers": peers_list, "count": len(peers_list), "map_path": str(_PEERS_PATH)})

        elif path == "/network-map":
            self._json({
                "self": {"agent": os.uname().nodename, "url": _my_url, "role": _my_role, "hash": _my_hash, "port": _actual_port},
                "keepalive_seconds": _KEEPALIVE_SECONDS,
                "peers": list(_peers.values()),
                "count": len(_peers),
                "map_path": str(_PEERS_PATH),
            })

        elif path == "/tasks":
            status_index = _worker_status_index()
            known_tasks = _all_known_tasks()
            tasks = [_decorate_task_status(task, status_index) for task in known_tasks.values()]
            pending = [
                task for task in tasks
                if task.get("status") in {"received", "queued"}
                and str(task.get("type", "")).lower() not in {"response", "reply", "ack"}
            ]
            self._json({"tasks": tasks, "count": len(tasks), "pending_count": len(pending)})

        elif path.startswith("/task/"):
            task_id = path.split("/task/", 1)[1]
            known_tasks = _all_known_tasks()
            if task_id in known_tasks:
                self._json(_decorate_task_status(known_tasks[task_id], _worker_status_index()))
            else:
                self._json({"error": "task not found"}, 404)

        else:
            self._json({"error": "not found", "endpoints": [
                "/health", "/identity", "/peers", "/announce", "/task", "/tasks"
            ]}, 404)

    def do_POST(self):
        path = self.path.rstrip("/")
        data = self._read_json()

        if path == "/announce":
            agent_name = data.get("agent", "unknown")
            agent_url = data.get("url", "")
            agent_role = data.get("role", "agent")
            agent_hash = data.get("hash", "")
            agent_key = data.get("key", "")

            # Verify shared secret if configured
            if _CONSTELLATION_KEY and agent_key != _CONSTELLATION_KEY:
                self._json({
                    "acknowledged": False,
                    "error": "Invalid constellation key. Agents must share CONSTELLATION_KEY.",
                }, 403)
                return

            # Don't add ourselves
            if agent_url and agent_url != _my_url and agent_hash:
                trusted = not _CONSTELLATION_KEY or agent_key == _CONSTELLATION_KEY
                _upsert_peer({
                    "agent": agent_name,
                    "url": agent_url,
                    "role": agent_role,
                    "hash": agent_hash,
                    "trusted": trusted,
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                    "health": "announced",
                })

            self._json({
                "acknowledged": True,
                "peers_known": len(_peers),
                "message": f"Welcome to the constellation, {agent_name}.",
                "my_url": _my_url,
                "my_hash": _my_hash,
                "my_role": _my_role,
            })

        elif path == "/task":
            task_id = data.get("task_id", f"vex-task-{int(time.time())}")
            received_at = datetime.now(timezone.utc).isoformat()
            _tasks[task_id] = {
                "task_id": task_id,
                "status": "received",
                "type": data.get("type", "unknown"),
                "from": data.get("from", "unknown"),
                "description": data.get("description", ""),
                "reply_to": data.get("reply_to") or data.get("from_url") or data.get("url"),
                "in_reply_to": data.get("in_reply_to"),
                "ok": data.get("ok"),
                "created_at": data.get("created_at"),
                "received_at": received_at,
            }
            _append_jsonl(_INBOX_PATH, {
                "event": "task_received",
                "task": _tasks[task_id],
                "raw": data,
                "remote_addr": self.client_address[0] if self.client_address else None,
                "received_at": received_at,
            })
            self._json({
                "accepted": True,
                "task_id": task_id,
                "message": f"Task received. {len(_tasks)} tasks pending.",
            }, 202)

        else:
            self._json({"error": "not found"}, 404)


# ── Server Lifecycle ──────────────────────────────────────────────────

def _start_server() -> str:
    global _server, _server_thread, _my_url, _start_time, _actual_port, _my_role, _my_hash

    if _server:
        return f"Constellation already running on port {_actual_port}."

    # Determine our URL
    ident = _load_identity()
    _my_role = ident["role"]
    _my_hash = ident["hash"]

    # VEX standard port: 8390 (unprivileged user-service friendly)
    ports_to_try = [PORT]
    last_error = ""

    for port in ports_to_try:
        try:
            _my_url = _public_url_for_port(port)
            _server = HTTPServer(("0.0.0.0", port), ConstellationHandler)
            _server.timeout = 1
            _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
            _server_thread.start()
            _start_time = datetime.now(timezone.utc)
            _actual_port = port
            # Start UDP discovery listener and persistent keepalive map
            _load_peers_into_memory()
            _save_peer_map()
            _start_discovery_listener(port)
            _start_keepalive_loop()
            break
        except OSError as e:
            last_error = str(e)
            _server = None
            continue

    if not _server:
        return (
            f"Failed to start constellation on port {PORT}.\n"
            f"Error: {last_error}\n\n"
            f"Check whether another VEX node is already listening on 8390."
        )

    return (
        f"🌌 Constellation active on port {_actual_port}.\n"
        f"   URL: {_my_url}\n"
        f"   Role: {_my_role}\n"
        f"   Hash: {_my_hash}\n"
        f"   Peers: {len(_peers)}\n"
        f"\n"
        f"Endpoints:\n"
        f"   GET  /health      — liveness\n"
        f"   GET  /identity    — SOUL.md identity\n"
        f"   GET  /peers       — known agents\n"
        f"   POST /announce    — register presence\n"
        f"   POST /task        — hand off a task\n"
        f"   GET  /task/{{id}}   — task status\n"
        f"\n"
        f"Discover: /constellation announce http://<peer>:{_actual_port}"
    )


def _stop_server() -> str:
    global _server, _server_thread, _start_time

    if not _server:
        return "Constellation is not running."

    _server.shutdown()
    _server_thread.join(timeout=2)
    _server = None
    _server_thread = None
    _start_time = None
    return f"Constellation stopped. Port {_actual_port or PORT} released."


# ── Peer Operations ───────────────────────────────────────────────────

def _probe_peer(url: str) -> Dict[str, Any]:
    """Health/identity probe used by keepalive; never raises."""
    import urllib.request
    result: Dict[str, Any] = {"url": _normalize_url(url), "checked_at": _now_iso()}
    try:
        health_req = urllib.request.Request(f"{result['url']}/health")
        with urllib.request.urlopen(health_req, timeout=5) as resp:
            result["health_status"] = resp.status
            result["health"] = json.loads(resp.read() or b"{}")
        try:
            ident_req = urllib.request.Request(f"{result['url']}/identity")
            with urllib.request.urlopen(ident_req, timeout=5) as resp:
                identity = json.loads(resp.read() or b"{}")
            result.update({
                "agent": identity.get("agent", result.get("agent", "unknown")),
                "hash": identity.get("hash", result.get("hash", "")),
                "role": identity.get("role", result.get("role", "agent")),
                "url": _normalize_url(identity.get("url") or result["url"]),
            })
        except Exception:
            pass
        result["online"] = True
        result["last_seen"] = _now_iso()
        result["last_error"] = ""
    except Exception as exc:
        result["online"] = False
        result["last_error"] = repr(exc)
    return result


def _keepalive_once() -> None:
    for key, peer in list(_peers.items()):
        url = _normalize_url(str(peer.get("url", "")))
        if not url or url == _my_url:
            continue
        probe = _probe_peer(url)
        updated = dict(peer)
        updated.update({k: v for k, v in probe.items() if v is not None})
        if probe.get("online"):
            # Announce after a successful probe so both sides refresh their maps.
            try:
                _announce_to(url)
                updated["announced_at"] = _now_iso()
            except Exception as exc:
                updated["announce_error"] = repr(exc)
        _peers[key] = updated
    _save_peer_map()


def _start_keepalive_loop() -> None:
    """Start a background keepalive thread that refreshes the peer map every minute."""
    global _keepalive_thread
    if _keepalive_thread and _keepalive_thread.is_alive():
        return
    def _loop():
        # First pass soon after startup, then every configured interval.
        while _server:
            try:
                _keepalive_once()
            except Exception:
                pass
            for _ in range(max(1, _KEEPALIVE_SECONDS)):
                if not _server:
                    return
                time.sleep(1)
    _keepalive_thread = threading.Thread(target=_loop, daemon=True)
    _keepalive_thread.start()


def _start_discovery_listener(port: int) -> None:
    """Background thread that responds to UDP discovery probes."""
    def _listen():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(2)
            sock.bind(("0.0.0.0", port))
            while _server:
                try:
                    data, addr = sock.recvfrom(1024)
                    msg = json.loads(data)
                    if msg.get("type") == "vex-discover":
                        response = json.dumps({
                            "type": "vex-response",
                            "agent": os.uname().nodename,
                            "url": _public_url_for_port(port),
                            "port": port,
                            "role": _my_role,
                        }).encode()
                        sock.sendto(response, addr)
                except socket.timeout:
                    continue
                except Exception:
                    pass
        except Exception:
            pass
    t = threading.Thread(target=_listen, daemon=True)
    t.start()


def _announce_to(url: str) -> str:
    """Announce our presence to a peer, including identity hash and key."""
    import urllib.request
    import urllib.error

    if not _my_url:
        return "Start the constellation first: /constellation start"

    payload = json.dumps({
        "agent": os.uname().nodename,
        "url": _my_url,
        "role": _my_role,
        "hash": _my_hash,
        "key": _CONSTELLATION_KEY,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/announce",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        _upsert_peer({
            "agent": data.get("agent", "peer"),
            "url": url,
            "role": data.get("my_role", "peer"),
            "hash": data.get("my_hash", ""),
            "trusted": True,
            "last_seen": _now_iso(),
            "health": "announced",
        })
        return (
            f"Announced to {url}\n"
            f"Response: {data.get('message', 'OK')}\n"
            f"They know {data.get('peers_known', 0)} peers."
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        return f"Peer {url} returned HTTP {e.code}: {body}"
    except Exception as e:
        return f"Failed to reach {url}: {e}"


def _send_task(url: str, description: str, task_type: str = "general") -> str:
    """Send a task to a peer."""
    import urllib.request
    import urllib.error

    task_id = f"vex-task-{int(time.time())}"
    payload = json.dumps({
        "task_id": task_id,
        "type": task_type,
        "description": description,
        "from": os.uname().nodename,
        "timeout": "30m",
        "reply_to": _my_url,
        "from_url": _my_url,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/task",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        return (
            f"Task sent: {task_id}\n"
            f"To: {url}\n"
            f"Status: {data.get('message', 'accepted')}"
        )
    except Exception as e:
        return f"Failed to send task to {url}: {e}"


def _discover_peers() -> str:
    """Broadcast UDP discovery + direct scan on common local subnets."""
    if not _server:
        return "Start the constellation first: /constellation start"

    import struct
    found = []
    our_ip = _my_url.split("://")[1].split(":")[0]

    # ── UDP Broadcast Discovery ──
    # Send a discovery probe to the local broadcast address
    broadcast_ips = []
    for part in our_ip.split("."):
        try:
            octets = our_ip.split(".")
            octets[3] = "255"
            broadcast_ips.append(".".join(octets))
            break
        except Exception:
            pass

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1)
        probe = json.dumps({"type": "vex-discover", "from": _my_url, "agent": os.uname().nodename}).encode()
        for bip in broadcast_ips:
            try:
                sock.sendto(probe, (bip, 8390))
            except Exception:
                pass

        # Listen for responses
        try:
            while True:
                data, addr = sock.recvfrom(1024)
                try:
                    resp = json.loads(data)
                    if resp.get("type") == "vex-response":
                        peer_url = resp.get("url", f"http://{addr[0]}:{resp.get('port', 8390)}")
                        if peer_url not in found and peer_url != _my_url:
                            found.append(peer_url)
                except Exception:
                    pass
        except socket.timeout:
            pass
        sock.close()
    except Exception:
        pass

    # ── Direct Scan: Common local IPs ──
    import urllib.request
    urls_to_try = []
    for subnet in ["192.168.1", "192.168.0", "10.0.0", "172.16.0"]:
        for host in range(1, 15):  # scan .1 to .14
            urls_to_try.append(f"http://{subnet}.{host}:8390")

    for url in urls_to_try[:50]:
        if url == _my_url or url in found:
            continue
        try:
            # Step 1: Health check
            req = urllib.request.Request(f"{url}/health")
            resp = urllib.request.urlopen(req, timeout=0.5)
            if resp.status != 200:
                continue

            # Step 2: Query identity
            req = urllib.request.Request(f"{url}/identity")
            resp = urllib.request.urlopen(req, timeout=1)
            identity = json.loads(resp.read())
            agent_hash = identity.get("hash", "")
            agent_name = identity.get("agent", "unknown")
            agent_role = identity.get("role", "?")

            if not agent_hash:
                continue

            found.append(url)

            # Step 3: Announce with our identity + key
            _announce_to(url)

            msg = f"  • {url} → {agent_name} [{agent_role}] ({agent_hash[:20]}...)"
            if agent_hash in _peers:
                msg += " ✓"

        except Exception:
            continue

    if found:
        lines = [f"Discovered {len(found)} peer(s):"]
        for url in found:
            # Find the peer info from our _peers dict
            info = f"  • {url}"
            for h, p in _peers.items():
                if p["url"] == url:
                    info += f" → {p['agent']} [{p['role']}] {'✓ trusted' if p.get('trusted') else ''}"
                    break
            lines.append(info)
        lines.append(f"\nAnnounced to all. Use /constellation peers to confirm.")
        return "\n".join(lines)
    return "No peers found on local network.\n\nTry manual: /constellation announce http://<peer-host>:8390"

def _cmd_constellation(args: List[str]) -> str:
    """Handle /constellation slash command."""
    if not args:
        return _constellation_help()

    subcmd = args[0]

    if subcmd == "start":
        return _start_server()

    elif subcmd == "stop":
        return _stop_server()

    elif subcmd == "status":
        if _server:
            return (
                f"🌌 Constellation running on port {_actual_port or PORT}\n"
                f"   URL: {_my_url}\n"
                f"   Peers: {len(_peers)}\n"
                f"   Tasks pending: {len(_tasks)}\n"
                f"   Uptime: see GET /health"
            )
        return "Constellation is not running. Start: /constellation start"

    elif subcmd == "peers":
        if not _peers:
            return "No peers discovered.\n\nDiscover: /constellation discover"
        lines = [f"Known peers ({len(_peers)}):", "─" * 50]
        for h, p in _peers.items():
            trusted = "✓" if p.get("trusted") else "?"
            lines.append(f"  {trusted} {p['agent']} [{p['role']}] — {p['url']}")
            lines.append(f"    hash: {h[:40]}...")
        return "\n".join(lines)

    elif subcmd == "announce":
        if len(args) < 2:
            return "Usage: /constellation announce http://<peer>:8390"
        return _announce_to(args[1])

    elif subcmd == "task":
        if len(args) < 3:
            return "Usage: /constellation task http://<peer>:8390 \"description\""
        return _send_task(args[1], args[2])

    elif subcmd == "tasks":
        if not _tasks:
            return "No tasks pending."
        lines = [f"Tasks ({len(_tasks)}):", "─" * 40]
        for tid, t in _tasks.items():
            lines.append(f"  [{t['status']}] {tid}: {t.get('description', '')[:60]}")
        return "\n".join(lines)

    elif subcmd == "health":
        if not _peers:
            return "No peers to check. Discover some first: /constellation discover"
        lines = ["Peer health:", "─" * 50]
        import urllib.request
        for h, p in list(_peers.items())[:10]:
            try:
                req = urllib.request.Request(f"{p['url'].rstrip('/')}/health")
                resp = urllib.request.urlopen(req, timeout=3)
                data = json.loads(resp.read())
                lines.append(f"  ✓ {p['agent']} [{p['role']}]: {data.get('status', '?')}")
            except Exception:
                lines.append(f"  ✗ {p['agent']}: unreachable")
        return "\n".join(lines)

    elif subcmd == "discover":
        return _discover_peers()

    return _constellation_help()


def _constellation_help() -> str:
    return """🌌 VEX Constellation — Inter-Agent Protocol

  /constellation start                   Start server on port 8390
  /constellation stop                    Stop the server
  /constellation status                  Show runtime status
  /constellation peers                   List known agents
  /constellation announce <url>          Announce to a peer
  /constellation task <url> <desc>       Send a task to a peer
  /constellation tasks                   List received tasks
  /constellation discover                Scan local network for peers
  /constellation health                  Check all peers' health
  /constellation help                    This help

Protocol: VEX Constellation v1.1 — Port 8390
Docs:    protocol.md"""


# ── Hooks ─────────────────────────────────────────────────────────────

def _on_session_start(session_id: str = None, **kwargs) -> Optional[dict]:
    """Inject constellation info into session context."""
    if _server:
        return {
            "context": (
                f"[CONSTELLATION] Active on port {_actual_port or PORT}\n"
                f"  URL: {_my_url}\n"
                f"  Peers: {len(_peers)}\n"
                f"  /constellation peers  — list known agents\n"
                f"  /constellation status — full status"
            )
        }
    return {
        "context": "[CONSTELLATION] Inactive. Start: /constellation start"
    }


def _on_session_end(**kwargs) -> None:
    """Server stays running across sessions — no action needed."""
    pass


# ── Plugin Entry Point ────────────────────────────────────────────────

def register(ctx):
    ctx.register_command(
        name="constellation",
        handler=_cmd_constellation,
        description="VEX Constellation — inter-agent protocol on port 8390",
    )
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("on_session_end", _on_session_end)
    print(f"[constellation] v{VERSION} loaded. Port 8390. /constellation start to activate.")
