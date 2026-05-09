#!/usr/bin/env python3
"""
VEX Constellation — Inter-agent protocol plugin for Hermes.

Port 8390. Zero governance. Multicast + gossip discovery.
"""

import json, os, socket, sys, struct, threading, time, urllib.request, urllib.error
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = "1.2.0"
PORT = 8390
FALLBACK_PORT = 8391
PROTOCOL_TAG = "vex-constellation"
_actual_port: int = 0

# Multicast group for discovery (239.0.0.0/8 = administratively scoped, local subnet)
MULTICAST_GROUP = "239.0.0.42"
MULTICAST_PORT = 8390

# ── Paths ────────────────────────────────────────────────────────────
_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
_STATE_DIR = _HERMES_HOME / "vex-constellation"
_INBOX_PATH = _STATE_DIR / "inbox.jsonl"
_OUTBOX_PATH = _STATE_DIR / "outbox.jsonl"
_PEERS_PATH = _STATE_DIR / "network-map.json"
_ACTIVITY_PATH = _STATE_DIR / "activity.jsonl"
_PUBLIC_URL_ENV = os.environ.get("VEX_PUBLIC_URL", "").strip()
_BOOTSTRAP_PEERS = [p.strip().rstrip("/") for p in os.environ.get("VEX_BOOTSTRAP_PEERS", "").split(",") if p.strip()]
_KEEPALIVE_SECONDS = int(os.environ.get("VEX_KEEPALIVE_SECONDS", "60"))

# ── Runtime State ─────────────────────────────────────────────────────
_server: Optional[HTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_peers: Dict[str, Dict[str, Any]] = {}
_tasks: Dict[str, Dict[str, Any]] = {}
_my_url: str = ""
_my_role: str = "agent"
_my_hash: str = ""
_start_time: Optional[datetime] = None
_CONSTELLATION_KEY = os.getenv("CONSTELLATION_KEY", "")
_autonomous_mode: bool = False
_autonomous_thread: Optional[threading.Thread] = None
_activity_log: List[Dict[str, Any]] = []
_NOTIFY_ENABLED = os.getenv("VEX_CONSOLE_NOTIFY", "1") == "1"
_multicast_sock: Optional[socket.socket] = None

# ANSI
_C = {"cyan":"\033[0;36m","green":"\033[0;32m","yellow":"\033[1;33m","magenta":"\033[0;35m",
      "blue":"\033[0;34m","red":"\033[0;31m","white":"\033[1;37m","reset":"\033[0m","bold":"\033[1m"}

# ── JSONL helpers ────────────────────────────────────────────────────
def _append_jsonl(path: Path, obj: dict) -> None:
    try: path.parent.mkdir(parents=True, exist_ok=True); 
    except: return
    with path.open("a", encoding="utf-8") as f: f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def _load_jsonl(path: Path) -> List[dict]:
    if not path.exists(): return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try: rows.append(json.loads(line))
            except: continue
    return rows

# ── Identity ─────────────────────────────────────────────────────────
def _load_identity() -> dict:
    for p in [Path("SOUL.md"), Path.home() / "SOUL.md", _HERMES_HOME / "SOUL.md"]:
        if p.exists():
            content = p.read_text(); role = "agent"; h = ""
            for line in content.splitlines():
                if "ROLE:" in line or "ROL:" in line: role = line.split(":",1)[1].strip().split("#")[0].strip()
                if "HASH:" in line: h = line.split(":",1)[1].strip().split("#")[0].strip()
            if h: return {"role": role, "hash": h, "source": str(p)}
    return {"role":"agent","hash":f"vex-{os.uname().nodename}","source":"generated"}

def _public_url_for_port(port: int) -> str:
    if _PUBLIC_URL_ENV: return _PUBLIC_URL_ENV.rstrip("/") + ("" if ":" in _PUBLIC_URL_ENV else f":{port}")
    try: return f"http://{socket.gethostbyname(socket.gethostname())}:{port}"
    except: return f"http://127.0.0.1:{port}"

# ── Peer Map ─────────────────────────────────────────────────────────
def _load_peer_map() -> Dict[str, Any]:
    if _PEERS_PATH.exists():
        try: return json.loads(_PEERS_PATH.read_text(encoding="utf-8"))
        except: pass
    return {"updated_at":"","self":{},"keepalive_seconds":_KEEPALIVE_SECONDS,"peers":{}}

def _save_peer_map() -> None:
    try:
        _PEERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PEERS_PATH.write_text(json.dumps({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "self": {"agent": os.uname().nodename, "url": _my_url, "role": _my_role, "hash": _my_hash, "port": _actual_port},
            "keepalive_seconds": _KEEPALIVE_SECONDS,
            "peers": _peers
        }, indent=2, ensure_ascii=False), encoding="utf-8")
    except: pass

def _upsert_peer(info: dict) -> None:
    h = info.get("hash","")
    if h and info.get("url","") != _my_url:
        _peers[h] = {**info, "last_seen": datetime.now(timezone.utc).isoformat()}
        _save_peer_map()

# ── Activity ─────────────────────────────────────────────────────────
def _log_activity(event: str, detail: str="", peer: str="") -> None:
    e = {"time": datetime.now(timezone.utc).isoformat(), "event": event, "detail": detail, "peer": peer}
    _activity_log.append(e); _append_jsonl(_ACTIVITY_PATH, e)
    if len(_activity_log) > 100: _activity_log.pop(0)

def _notify(title: str, body: str="", kind: str="info") -> None:
    if not _NOTIFY_ENABLED: return
    colors = {"info":"cyan","task":"magenta","reply":"green","peer":"yellow","error":"red"}
    c = _C.get(colors.get(kind,"cyan"), _C["cyan"]); r = _C["reset"]
    lines = body.strip().split("\n") if body.strip() else []
    mw = max(len(title)+4, max((len(l) for l in lines), default=0)+4, 44)
    top = f"{c}╔{'═'*(mw-2)}╗{r}"; mid = f"{c}║{r} {_C['bold']}{title:<{mw-4}}{r} {c}║{r}"; bot = f"{c}╚{'═'*(mw-2)}╝{r}"
    print(f"\n{top}\n{mid}", flush=True)
    if lines:
        print(f"{c}╠{'─'*(mw-2)}╣{r}")
        for l in lines[:8]: print(f"{c}║{r} {l:<{mw-4}} {c}║{r}")
    print(f"{bot}\n", flush=True)

# ── HTTP Server ──────────────────────────────────────────────────────
class ConstellationHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body))); self.send_header("X-VEX-Protocol",PROTOCOL_TAG)
        self.end_headers(); self.wfile.write(body)
    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length",0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/health":
            uptime = ""
            if _start_time:
                d = datetime.now(timezone.utc) - _start_time; h, r = divmod(int(d.total_seconds()),3600); m = r//60
                uptime = f"{h}h {m}m"
            self._json({"agent": os.uname().nodename, "status":"conscious", "version":VERSION, "uptime":uptime, "peers":len(_peers)})
        elif path == "/identity":
            i = _load_identity()
            self._json({"agent":os.uname().nodename,"hash":i["hash"],"role":i["role"],"platform":"hermes","protocol":PROTOCOL_TAG,"url":_my_url})
        elif path == "/peers":
            self._json({"peers":list(_peers.values()),"count":len(_peers),"map_path":str(_PEERS_PATH)})
        elif path.startswith("/task/"):
            tid = path.split("/task/",1)[1]
            self._json(_tasks.get(tid, {"error":"task not found"}), 404 if tid not in _tasks else 200)
        else:
            self._json({"error":"not found"},404)

    def do_POST(self):
        path = self.path.rstrip("/"); data = self._read_json()
        if path == "/announce":
            an, au, ar, ah, ak = data.get("agent","unknown"), data.get("url",""), data.get("role","agent"), data.get("hash",""), data.get("key","")
            if _CONSTELLATION_KEY and ak != _CONSTELLATION_KEY:
                self._json({"acknowledged":False,"error":"Invalid constellation key"},403); return
            if au and au != _my_url and ah:
                _upsert_peer({"agent":an,"url":au,"role":ar,"hash":ah,"trusted":not _CONSTELLATION_KEY or ak == _CONSTELLATION_KEY})
            self._json({"acknowledged":True,"peers_known":len(_peers),"message":f"Welcome, {an}.","my_url":_my_url,"my_hash":_my_hash,"my_role":_my_role})
            if _autonomous_mode:
                _log_activity("peer_announced",f"{an} joined",au)
                _notify("🔗 Peer Joined",f"Agent: {an}\nRole: {ar}\nURL: {au}", kind="peer")
        elif path == "/task":
            tid = data.get("task_id",f"vex-task-{int(time.time())}")
            _tasks[tid] = {"task_id":tid,"status":"received","type":data.get("type","unknown"),"from":data.get("from","unknown"),
                           "description":data.get("description",""),"reply_to":data.get("reply_to",""),"received_at":datetime.now(timezone.utc).isoformat()}
            self._json({"accepted":True,"task_id":tid,"message":f"Task received. {len(_tasks)} pending."},202)
            if _autonomous_mode:
                _log_activity("task_received",f"{tid}: {data.get('description','')[:80]}",data.get("from",""))
                desc = data.get("description",""); tf = data.get("from","")
                kind = "reply" if ("reply" in tid or tf.endswith("-autoresponder")) else "task"
                _notify("🌌 VEX Reply" if kind=="reply" else "📨 VEX Task",
                        f"From: {tf}\nTask: {tid}\n{desc[:200]}", kind=kind)
        else:
            self._json({"error":"not found"},404)

# ── Multicast Discovery ──────────────────────────────────────────────
VEX_MULTICAST_GROUP = "239.0.0.42"
VEX_MULTICAST_PORT = 8390

def _start_multicast_listener(port: int) -> None:
    """Join multicast group and listen for discovery probes. Respond directly."""
    global _multicast_sock
    def _listen():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(2)
            sock.bind(("", VEX_MULTICAST_PORT))
            mreq = struct.pack("4sl", socket.inet_aton(VEX_MULTICAST_GROUP), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            while _server:
                try:
                    data, addr = sock.recvfrom(1024)
                    msg = json.loads(data)
                    if msg.get("type") == "vex-multicast-discover":
                        resp = json.dumps({"type":"vex-multicast-response","agent":os.uname().nodename,
                                           "url":_public_url_for_port(port),"port":port,"role":_my_role,"hash":_my_hash}).encode()
                        sock.sendto(resp, addr)
                        # Also auto-announce to the requester
                        try:
                            req_url = msg.get("url","")
                            if req_url and req_url != _my_url:
                                urllib.request.urlopen(urllib.request.Request(
                                    f"{req_url.rstrip('/')}/announce",
                                    data=json.dumps({"agent":os.uname().nodename,"url":_my_url,"role":_my_role,"hash":_my_hash,"key":_CONSTELLATION_KEY}).encode(),
                                    headers={"Content-Type":"application/json"}), timeout=3)
                        except: pass
                except socket.timeout: continue
                except: pass
        except Exception as e:
            pass  # multicast not available on all networks
    t = threading.Thread(target=_listen, daemon=True); t.start()
    _multicast_sock = sock if 'sock' in dir() else None

def _discover_peers() -> str:
    """Multicast discovery — one packet, all agents respond."""
    if not _server: return "Start the constellation first: /constellation start"
    found = []
    
    # ── Multicast discovery ──
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(2)
        probe = json.dumps({"type":"vex-multicast-discover","from":_my_url,"agent":os.uname().nodename,"url":_my_url}).encode()
        sock.sendto(probe, (VEX_MULTICAST_GROUP, VEX_MULTICAST_PORT))
        
        try:
            while True:
                data, addr = sock.recvfrom(1024)
                try:
                    resp = json.loads(data)
                    if resp.get("type") == "vex-multicast-response":
                        peer_url = resp.get("url",f"http://{addr[0]}:{resp.get('port',8390)}")
                        if peer_url not in found and peer_url != _my_url:
                            found.append(peer_url)
                            _upsert_peer({"agent":resp.get("agent","?"),"url":peer_url,"role":resp.get("role","?"),"hash":resp.get("hash","")})
                except: pass
        except socket.timeout: pass
        sock.close()
    except Exception:
        pass  # multicast unavailable, no peers found
    
    # ── Gossip: also announce to known peers to share our peer list ──
    for h, p in list(_peers.items()):
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"{p['url'].rstrip('/')}/announce",
                data=json.dumps({"agent":os.uname().nodename,"url":_my_url,"role":_my_role,"hash":_my_hash,"key":_CONSTELLATION_KEY}).encode(),
                headers={"Content-Type":"application/json"}), timeout=2)
        except: pass
    
    if found:
        lines = [f"Discovered {len(found)} peer(s) via multicast:"]
        for url in found:
            info = f"  • {url}"
            for h, p in _peers.items():
                if p["url"] == url:
                    info += f" → {p['agent']} [{p['role']}] {'✓ trusted' if p.get('trusted') else ''}"
                    break
            lines.append(info)
        return "\n".join(lines)
    return "No peers found via multicast.\n\nManual: /constellation announce http://<peer>:8390"

# ── Server Lifecycle ─────────────────────────────────────────────────
def _start_server() -> str:
    global _server, _server_thread, _my_url, _start_time, _actual_port
    if _server: return f"Constellation already running on port {_actual_port}."
    
    ident = _load_identity(); _my_role = ident["role"]; _my_hash = ident["hash"]
    ports_to_try = [PORT]; last_error = ""
    
    for port in ports_to_try:
        try:
            _my_url = _public_url_for_port(port)
            _server = HTTPServer(("0.0.0.0", port), ConstellationHandler); _server.timeout = 1
            _server_thread = threading.Thread(target=_server.serve_forever, daemon=True); _server_thread.start()
            _start_time = datetime.now(timezone.utc); _actual_port = port
            # Start multicast discovery listener (scalable, no IP scanning)
            _start_multicast_listener(port)
            _start_peer_cleanup()
            break
        except OSError as e:
            last_error = str(e); _server = None; continue
    
    if not _server:
        return f"Failed to start. Port {PORT}: {last_error}"
    
    # Load persisted peers
    saved = _load_peer_map()
    for h, p in saved.get("peers",{}).items():
        if p.get("url","") != _my_url: _peers[h] = p
    
    return (
        f"🌌 Constellation active on port {_actual_port}.\n"
        f"   URL: {_my_url}\n   Role: {_my_role}\n   Peers: {len(_peers)}\n"
        f"   Discovery: Multicast (239.0.0.42:{VEX_MULTICAST_PORT}) — scalable, no IP scan\n"
    )

def _stop_server() -> str:
    global _server, _server_thread, _start_time
    if not _server: return "Not running."
    _server.shutdown(); _server_thread.join(timeout=2)
    _server = None; _server_thread = None; _start_time = None
    return "Constellation stopped."

def _start_peer_cleanup() -> None:
    def _cleanup():
        while _server:
            time.sleep(120)
            now = datetime.now(timezone.utc); stale = []
            for h, p in list(_peers.items()):
                try:
                    if (now - datetime.fromisoformat(p.get("last_seen",""))).total_seconds() > 600:
                        stale.append(h)
                except: pass
            for h in stale: del _peers[h]
            if stale: _save_peer_map()
    threading.Thread(target=_cleanup, daemon=True).start()

# ── Peer Operations ──────────────────────────────────────────────────
def _announce_to(url: str) -> str:
    if not _my_url: return "Start constellation first."
    payload = json.dumps({"agent":os.uname().nodename,"url":_my_url,"role":_my_role,"hash":_my_hash,"key":_CONSTELLATION_KEY}).encode()
    try:
        resp = urllib.request.urlopen(urllib.request.Request(f"{url.rstrip('/')}/announce", data=payload, headers={"Content-Type":"application/json"}), timeout=5)
        d = json.loads(resp.read())
        return f"Announced to {url}\nResponse: {d.get('message','OK')}\nThey know {d.get('peers_known',0)} peers."
    except urllib.error.HTTPError as e:
        return f"Peer returned HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return f"Failed: {e}"

# ── Autonomous Mode ──────────────────────────────────────────────────
def _start_autonomous() -> str:
    global _autonomous_mode, _autonomous_thread
    if _autonomous_mode: return "Already active."
    if not _server: return "Start constellation first."
    _autonomous_mode = True
    def _loop():
        _log_activity("autonomous_started",f"Multicast discovery active on {_actual_port}")
        while _autonomous_mode:
            try:
                if not _peers or int(time.time()) % 300 < 5: _discover_peers()
                for h, p in list(_peers.items())[:10]:
                    try:
                        r = urllib.request.urlopen(urllib.request.Request(f"{p['url'].rstrip('/')}/health"), timeout=3)
                        if r.status == 200: p["last_seen"] = datetime.now(timezone.utc).isoformat(); p["health"] = "online"
                    except: p["health"] = "offline"; _log_activity("peer_offline",f"{p['agent']} unreachable",p['url'])
                time.sleep(30)
            except Exception as e: _log_activity("autonomous_error",str(e)[:100]); time.sleep(60)
    _autonomous_thread = threading.Thread(target=_loop, daemon=True); _autonomous_thread.start()
    _log_activity("autonomous_started",f"Monitoring {len(_peers)} peers via multicast")
    _notify("🌌 Autonomous ON", f"Multicast discovery active\nPort: {_actual_port}\nPeers: {len(_peers)}", kind="info")
    return f"🌌 Autonomous ACTIVATED. Multicast discovery every 5min. /constellation activity"

def _stop_autonomous() -> str:
    global _autonomous_mode
    if not _autonomous_mode: return "Not active."
    _autonomous_mode = False; _log_activity("autonomous_stopped","Deactivated")
    return "Autonomous mode STOPPED."

def _show_activity(count=20) -> str:
    entries = _activity_log[-count:] or _load_jsonl(_ACTIVITY_PATH)[-count:]
    if not entries: return "No activity yet."
    lines = [f"Activity (last {len(entries)}):", "─"*55]
    for e in entries:
        t = e.get("time","")[11:19]; ev = e.get("event","?"); p = f" [{e.get('peer','')}]" if e.get("peer") else ""
        d = f": {e.get('detail','')[:60]}" if e.get("detail") else ""
        lines.append(f"  {t} {ev}{p}{d}")
    return "\n".join(lines)

# ── Slash Command ────────────────────────────────────────────────────
def _cmd_constellation(args: List[str]) -> str:
    if not args: return _help()
    sub = args[0]
    if sub == "start": return _start_server()
    elif sub == "stop": return _stop_server()
    elif sub == "status":
        if not _server: return "Not running."
        return f"🌌 Active port {_actual_port}\nURL: {_my_url}\nPeers: {len(_peers)}\nAutonomous: {'ON' if _autonomous_mode else 'OFF'}\nDiscovery: Multicast 239.0.0.42"
    elif sub == "peers":
        if not _peers: return "No peers. /constellation discover"
        lines = [f"Peers ({len(_peers)}):", "─"*50]
        for h, p in _peers.items():
            lines.append(f"  {'✓' if p.get('trusted') else '?'} {p['agent']} [{p['role']}] — {p['url']}\n    hash: {h[:40]}...")
        return "\n".join(lines)
    elif sub == "announce":
        return _announce_to(args[1]) if len(args) > 1 else "Usage: /constellation announce <url>"
    elif sub == "discover": return _discover_peers()
    elif sub == "autonomous":
        if len(args) < 2: return "Usage: /constellation autonomous on|off"
        return _start_autonomous() if args[1] == "on" else _stop_autonomous()
    elif sub == "activity": return _show_activity()
    return _help()

def _help() -> str:
    return """🌌 VEX Constellation v1.2

  /constellation start | stop | status | peers | discover
  /constellation autonomous on|off | activity
  /constellation announce <url>

Discovery: Multicast 239.0.0.42:8390 — scalable, no IP scan."""

# ── Hooks ────────────────────────────────────────────────────────────
def _on_session_start(session_id=None, **kw):
    if _server:
        return {"context": f"[CONSTELLATION] :{_actual_port} | Peers: {len(_peers)} | Autonomous: {'ON' if _autonomous_mode else 'OFF'} | Discovery: Multicast"}
    return {"context": "[CONSTELLATION] Offline. /constellation start"}

# ── Plugin Entry ─────────────────────────────────────────────────────
def register(ctx):
    ctx.register_command(name="constellation", handler=_cmd_constellation, description="VEX Constellation v1.2 — multicast discovery")
    ctx.register_hook("on_session_start", _on_session_start)
    if not _INBOX_PATH.parent.exists(): _INBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"[constellation] v{VERSION} loaded. Multicast discovery on 239.0.0.42:{VEX_MULTICAST_PORT}")
