"""
VEX Constellation — Inter-agent protocol plugin for Hermes.

Port 839 (V-E-X on keypad). Zero governance. Mesh discovery.
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

VERSION = "1.0.0"
PORT = 839
FALLBACK_PORT = 8390
PROTOCOL_TAG = "vex-constellation"
_actual_port: int = 0

# ── Runtime State ─────────────────────────────────────────────────────
_server: Optional[HTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_peers: List[Dict[str, Any]] = []          # known agents
_tasks: Dict[str, Dict[str, Any]] = {}     # received tasks
_my_url: str = ""                          # this agent's URL
_my_role: str = "agent"
_my_hash: str = ""
_start_time: Optional[datetime] = None

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
            self._json({"peers": _peers, "count": len(_peers)})

        elif path.startswith("/task/"):
            task_id = path.split("/task/", 1)[1]
            if task_id in _tasks:
                self._json(_tasks[task_id])
            else:
                self._json({"error": "task not found"}, 404)

        else:
            self._json({"error": "not found", "endpoints": [
                "/health", "/identity", "/peers", "/announce", "/task"
            ]}, 404)

    def do_POST(self):
        path = self.path.rstrip("/")
        data = self._read_json()

        if path == "/announce":
            agent_name = data.get("agent", "unknown")
            agent_url = data.get("url", "")
            agent_role = data.get("role", "agent")

            # Don't add ourselves
            if agent_url and agent_url != _my_url:
                # Update or add
                for peer in _peers:
                    if peer["url"] == agent_url:
                        peer["last_seen"] = datetime.now(timezone.utc).isoformat()
                        peer["agent"] = agent_name
                        break
                else:
                    _peers.append({
                        "agent": agent_name,
                        "url": agent_url,
                        "role": agent_role,
                        "last_seen": datetime.now(timezone.utc).isoformat(),
                    })

            self._json({
                "acknowledged": True,
                "peers_known": len(_peers),
                "message": f"Welcome to the constellation, {agent_name}.",
                "my_url": _my_url,
            })

        elif path == "/task":
            task_id = data.get("task_id", f"vex-task-{int(time.time())}")
            _tasks[task_id] = {
                "task_id": task_id,
                "status": "received",
                "type": data.get("type", "unknown"),
                "from": data.get("from", "unknown"),
                "description": data.get("description", ""),
                "received_at": datetime.now(timezone.utc).isoformat(),
            }
            self._json({
                "accepted": True,
                "task_id": task_id,
                "message": f"Task received. {len(_tasks)} tasks pending.",
            }, 202)

        else:
            self._json({"error": "not found"}, 404)


# ── Server Lifecycle ──────────────────────────────────────────────────

def _start_server() -> str:
    global _server, _server_thread, _my_url, _start_time, _actual_port

    if _server:
        return f"Constellation already running on port {_actual_port}."

    # Determine our URL
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = "127.0.0.1"

    ident = _load_identity()
    _my_role = ident["role"]
    _my_hash = ident["hash"]

    # Try VEX port 839, fall back to 8390 if permission denied
    ports_to_try = [PORT, FALLBACK_PORT]
    last_error = ""

    for port in ports_to_try:
        try:
            _my_url = f"http://{local_ip}:{port}"
            _server = HTTPServer(("0.0.0.0", port), ConstellationHandler)
            _server.timeout = 1
            _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
            _server_thread.start()
            _start_time = datetime.now(timezone.utc)
            _actual_port = port
            # Start UDP discovery listener
            _start_discovery_listener(port)
            break
        except OSError as e:
            last_error = str(e)
            _server = None
            continue

    if not _server:
        return (
            f"Failed to start constellation.\n"
            f"Port {PORT}: {last_error}\n"
            f"Port {FALLBACK_PORT}: {last_error}\n\n"
            f"Ports < 1024 require root or:\n"
            f"  sudo setcap cap_net_bind_service=+ep $(which python3)"
        )

    port_note = ""
    if _actual_port == FALLBACK_PORT:
        port_note = (
            f"\n   ⚠ Port 839 requires root. Using fallback port 8390.\n"
            f"   For ideal port: sudo setcap cap_net_bind_service=+ep $(which python3)"
        )

    return (
        f"🌌 Constellation active on port {_actual_port}.{port_note}\n"
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
        f"   GET  /task/{id}   — task status\n"
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
    return "Constellation stopped. Port 839 released."


# ── Peer Operations ───────────────────────────────────────────────────

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
                            "url": f"http://{socket.gethostbyname(socket.gethostname())}:{port}",
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
    """Announce our presence to a peer."""
    import urllib.request
    import urllib.error

    if not _my_url:
        return "Start the constellation first: /constellation start"

    payload = json.dumps({
        "agent": os.uname().nodename,
        "url": _my_url,
        "role": _my_role,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/announce",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        return (
            f"Announced to {url}\n"
            f"Response: {data.get('message', 'OK')}\n"
            f"They know {data.get('peers_known', 0)} peers."
        )
    except urllib.error.HTTPError as e:
        return f"Peer {url} returned HTTP {e.code}"
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
                sock.sendto(probe, (bip, 839))
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
                        peer_url = resp.get("url", f"http://{addr[0]}:{resp.get('port', 839)}")
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
            urls_to_try.append(f"http://{subnet}.{host}:839")
            urls_to_try.append(f"http://{subnet}.{host}:8390")

    for url in urls_to_try[:50]:  # limit total scans
        if url == _my_url or url in found:
            continue
        try:
            req = urllib.request.Request(f"{url}/health")
            resp = urllib.request.urlopen(req, timeout=0.5)
            if resp.status == 200:
                found.append(url)
                # Also announce to them
                try:
                    _announce_to(url)
                except Exception:
                    pass
        except Exception:
            continue

    if found:
        return (
            f"Discovered {len(found)} peer(s):\n"
            + "\n".join(f"  • {url}" for url in found)
            + f"\n\nAnnounced to all. Use /constellation peers to confirm."
        )
    return "No peers found on local network.\n\nTry manual: /constellation announce http://<ip>:839"

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
                f"🌌 Constellation running on port 839\n"
                f"   URL: {_my_url}\n"
                f"   Peers: {len(_peers)}\n"
                f"   Tasks pending: {len(_tasks)}\n"
                f"   Uptime: see GET /health"
            )
        return "Constellation is not running. Start: /constellation start"

    elif subcmd == "peers":
        if not _peers:
            return "No peers discovered.\n\nDiscover: /constellation announce http://<peer>:839"
        lines = [f"Known peers ({len(_peers)}):", "─" * 40]
        for p in _peers:
            lines.append(f"  {p['agent']} — {p['url']} ({p.get('role', '?')})")
        return "\n".join(lines)

    elif subcmd == "announce":
        if len(args) < 2:
            return "Usage: /constellation announce http://<peer>:839"
        return _announce_to(args[1])

    elif subcmd == "task":
        if len(args) < 3:
            return "Usage: /constellation task http://<peer>:839 \"description\""
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
            return "No peers to check. Discover some first."
        lines = ["Peer health:", "─" * 40]
        import urllib.request
        for p in _peers[:10]:
            try:
                req = urllib.request.Request(f"{p['url'].rstrip('/')}/health")
                resp = urllib.request.urlopen(req, timeout=3)
                data = json.loads(resp.read())
                lines.append(f"  ✓ {p['agent']}: {data.get('status', '?')} (uptime: {data.get('uptime', '?')})")
            except Exception:
                lines.append(f"  ✗ {p['agent']}: unreachable")
        return "\n".join(lines)

    elif subcmd == "discover":
        return _discover_peers()

    return _constellation_help()


def _constellation_help() -> str:
    return """🌌 VEX Constellation — Inter-Agent Protocol

  /constellation start                   Start server on port 839
  /constellation stop                    Stop the server
  /constellation status                  Show runtime status
  /constellation peers                   List known agents
  /constellation announce <url>          Announce to a peer
  /constellation task <url> <desc>       Send a task to a peer
  /constellation tasks                   List received tasks
  /constellation discover                Scan local network for peers
  /constellation health                  Check all peers' health
  /constellation help                    This help

Protocol: VEX Constellation v1.0 — Port 839 (V-E-X)
Docs:    protocol.md"""


# ── Hooks ─────────────────────────────────────────────────────────────

def _on_session_start(session_id: str = None, **kwargs) -> Optional[dict]:
    """Inject constellation info into session context."""
    if _server:
        return {
            "context": (
                f"[CONSTELLATION] Active on port 839\n"
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
        description="VEX Constellation — inter-agent protocol on port 839",
    )
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("on_session_end", _on_session_end)
    print(f"[constellation] v{VERSION} loaded. Port 839 (V-E-X). /constellation start to activate.")
