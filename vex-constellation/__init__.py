#!/usr/bin/env python3
"""VEX Constellation v1.5 — Inter-agent Mesh Pub/Sub with Ed25519 signatures + X25519 encryption. Phase 4: end-to-end encryption."""

import json, os, socket, struct, threading, time, urllib.request, urllib.error, urllib.parse, base64
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

import nacl.signing, nacl.encoding, nacl.public, nacl.secret
from nacl.public import SealedBox, PrivateKey as X25519PrivateKey, PublicKey as X25519PublicKey

VERSION = "1.5.0"; PORT = 8390; FALLBACK_PORT = 8391; PROTOCOL_TAG = "vex-constellation"; _actual_port: int = 0
MULTICAST_GROUP = "239.0.0.42"; MULTICAST_PORT = 8390

_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
_STATE_DIR = _HERMES_HOME / "vex-constellation"
_INBOX_PATH = _STATE_DIR / "inbox.jsonl"; _OUTBOX_PATH = _STATE_DIR / "outbox.jsonl"
_PEERS_PATH = _STATE_DIR / "network-map.json"; _ACTIVITY_PATH = _STATE_DIR / "activity.jsonl"
_EVENT_LOG_PATH = _STATE_DIR / "events"; _IDENTITY_PATH = _STATE_DIR / "identity.json"
_PUBLIC_URL_ENV = os.environ.get("VEX_PUBLIC_URL","").strip()
_NOTIFY_ENABLED = os.getenv("VEX_CONSOLE_NOTIFY","1") == "1"
_CONSTELLATION_KEY = os.getenv("CONSTELLATION_KEY","")
SIGNATURE_MODE = os.getenv("VEX_SIGNATURE_MODE", "permissive")
ENCRYPTION_MODE = os.getenv("VEX_ENCRYPTION_MODE", "available")  # available | required

_keypair: Any = None; _public_key_hex = ""
_enc_keypair: Any = None; _enc_public_hex = ""

def _get_or_create_keypair():
    global _keypair, _public_key_hex
    if _keypair: return _keypair
    if _IDENTITY_PATH.exists():
        try:
            d = json.loads(_IDENTITY_PATH.read_text())
            _keypair = nacl.signing.SigningKey(bytes.fromhex(d["private_seed"]))
            _public_key_hex = _keypair.verify_key.encode(encoder=nacl.encoding.HexEncoder).decode()
            return _keypair
        except: pass
    _keypair = nacl.signing.SigningKey.generate()
    _public_key_hex = _keypair.verify_key.encode(encoder=nacl.encoding.HexEncoder).decode()
    _IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _IDENTITY_PATH.write_text(json.dumps({
        "node_id": os.uname().nodename, "private_seed": _keypair.encode(encoder=nacl.encoding.HexEncoder).decode(),
        "public_key": _public_key_hex, "created_at": datetime.now(timezone.utc).isoformat(), "algorithm": "Ed25519"
    }, indent=2)); os.chmod(_IDENTITY_PATH, 0o600)
    return _keypair

def _get_encryption_keypair():
    global _enc_keypair, _enc_public_hex
    if _enc_keypair: return _enc_keypair
    _IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _IDENTITY_PATH.exists():
        try:
            d = json.loads(_IDENTITY_PATH.read_text())
            if d.get("encryption_seed"):
                _enc_keypair = X25519PrivateKey(bytes.fromhex(d["encryption_seed"]))
                _enc_public_hex = bytes(_enc_keypair.public_key).hex()
                return _enc_keypair
        except: pass
    _enc_keypair = X25519PrivateKey.generate()
    _enc_public_hex = bytes(_enc_keypair.public_key).hex()
    data = json.loads(_IDENTITY_PATH.read_text()) if _IDENTITY_PATH.exists() else {}
    data["encryption_seed"] = bytes(_enc_keypair).hex()
    data["encryption_public_key"] = _enc_public_hex
    _IDENTITY_PATH.write_text(json.dumps(data, indent=2)); os.chmod(_IDENTITY_PATH, 0o600)
    return _enc_keypair

def _sign_payload(data: dict) -> dict:
    kp = _get_or_create_keypair()
    clean = {k: v for k, v in data.items() if k not in ("signature", "signer")}
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    signed = kp.sign(canonical.encode())
    return {**data, "signature": base64.b64encode(signed.signature).decode(), "signer": _public_key_hex}

def _verify_payload(data: dict) -> bool:
    sig = data.get("signature", ""); signer = data.get("signer", "")
    if not sig or not signer: return False
    try:
        vk = nacl.signing.VerifyKey(signer, encoder=nacl.encoding.HexEncoder)
        clean = {k: v for k, v in data.items() if k not in ("signature", "signer")}
        vk.verify(json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(), base64.b64decode(sig))
        return True
    except: return False

def _encrypt_for(payload: dict, recipient_pk_hex: str) -> str:
    try:
        pk = X25519PublicKey(bytes.fromhex(recipient_pk_hex))
        return base64.b64encode(SealedBox(pk).encrypt(json.dumps(payload, ensure_ascii=False).encode())).decode()
    except Exception as e: return f"ENC_ERROR:{e}"

def _decrypt_inbound(ciphertext_b64: str) -> dict:
    try:
        kp = _get_encryption_keypair()
        return json.loads(SealedBox(kp).decrypt(base64.b64decode(ciphertext_b64)).decode())
    except Exception: return {"error": "decryption_failed"}

# ── Runtime State ──
_server: Any = None; _server_thread: Any = None; _my_url = ""
_peers: Dict[str,Dict] = {}; _tasks: Dict[str,Dict] = {}; _my_role = "agent"; _my_hash = ""
_start_time: Optional[datetime] = None
_autonomous_mode = False; _autonomous_thread: Any = None
_activity_log: List[Dict] = []
_subscriptions: Dict[str,Dict] = {}; _published_events: set = set()

_C = {"cyan":"\033[0;36m","green":"\033[0;32m","yellow":"\033[1;33m","magenta":"\033[0;35m","blue":"\033[0;34m","red":"\033[0;31m","white":"\033[1;37m","reset":"\033[0m","bold":"\033[1m"}

def _append_jsonl(p,d):
    try: p.parent.mkdir(parents=True,exist_ok=True)
    except: return
    with p.open("a",encoding="utf-8") as f: f.write(json.dumps(d,ensure_ascii=False)+"\n")
def _load_jsonl(p): return [json.loads(l) for l in p.read_text(encoding="utf-8",errors="replace").splitlines() if l.strip()] if p.exists() else []
def _load_identity():
    for p in [Path("SOUL.md"), Path.home()/"SOUL.md", _HERMES_HOME/"SOUL.md"]:
        if p.exists():
            c=p.read_text(); r="agent"; h=""
            for l in c.splitlines():
                if "ROLE:" in l or "ROL:" in l: r=l.split(":",1)[1].strip().split("#")[0].strip()
                if "HASH:" in l: h=l.split(":",1)[1].strip().split("#")[0].strip()
            if h: return {"role":r,"hash":h,"source":str(p)}
    return {"role":"agent","hash":f"vex-{os.uname().nodename}","source":"generated"}
def _public_url_for_port(port):
    if _PUBLIC_URL_ENV: return _PUBLIC_URL_ENV.rstrip("/")+("" if ":" in _PUBLIC_URL_ENV else f":{port}")
    try: return f"http://{socket.gethostbyname(socket.gethostname())}:{port}"
    except: return f"http://127.0.0.1:{port}"
def _load_peer_map():
    if _PEERS_PATH.exists():
        try: return json.loads(_PEERS_PATH.read_text(encoding="utf-8"))
        except: pass
    return {"updated_at":"","self":{},"peers":{}}
def _save_peer_map():
    try:
        _PEERS_PATH.parent.mkdir(parents=True,exist_ok=True)
        _PEERS_PATH.write_text(json.dumps({"updated_at":datetime.now(timezone.utc).isoformat(),"self":{"agent":os.uname().nodename,"url":_my_url,"role":_my_role,"hash":_my_hash,"port":_actual_port,"public_key":_public_key_hex,"encryption_key":_enc_public_hex},"peers":_peers},indent=2,ensure_ascii=False),encoding="utf-8")
    except: pass
def _upsert_peer(info):
    h=info.get("hash","")
    if h and info.get("url","") != _my_url:
        pk = info.get("public_key",""); ek = info.get("encryption_key","")
        _peers[h]={**info,"last_seen":datetime.now(timezone.utc).isoformat()}
        if pk: _peers[h]["public_key"]=pk
        if ek: _peers[h]["encryption_key"]=ek
        _save_peer_map()
def _log_activity(event, detail="", peer=""):
    e={"time":datetime.now(timezone.utc).isoformat(),"event":event,"detail":detail,"peer":peer}
    _activity_log.append(e); _append_jsonl(_ACTIVITY_PATH,e)
    if len(_activity_log)>100: _activity_log.pop(0)
def _notify(title,body="",kind="info"):
    if not _NOTIFY_ENABLED: return
    colors={"info":"cyan","task":"magenta","reply":"green","peer":"yellow","error":"red"}
    c=_C.get(colors.get(kind,"cyan"),_C["cyan"]); r=_C["reset"]
    lines=body.strip().split("\n") if body.strip() else []
    mw=max(len(title)+4,max((len(l) for l in lines),default=0)+4,44)
    top=f"{c}╔{'═'*(mw-2)}╗{r}"; mid=f"{c}║{r} {_C['bold']}{title:<{mw-4}}{r} {c}║{r}"; bot=f"{c}╚{'═'*(mw-2)}╝{r}"
    print(f"\n{top}\n{mid}",flush=True)
    if lines:
        print(f"{c}╠{'─'*(mw-2)}╣{r}")
        for l in lines[:8]: print(f"{c}║{r} {l:<{mw-4}} {c}║{r}")
    print(f"{bot}\n",flush=True)
def _topic_match(pattern, topic):
    pp=pattern.split("/"); tp=topic.split("/")
    for i,p in enumerate(pp):
        if p=="#": return True
        if i>=len(tp): return False
        if p=="+": continue
        if p!=tp[i]: return False
    return len(pp)==len(tp)

class ConstellationHandler(BaseHTTPRequestHandler):
    def log_message(self,f,*a): pass
    def _json(self,d,status=200):
        body=json.dumps(d).encode()
        self.send_response(status); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body))); self.send_header("X-VEX-Protocol",PROTOCOL_TAG)
        self.end_headers(); self.wfile.write(body)
    def _read_json(self):
        l=int(self.headers.get("Content-Length",0))
        return json.loads(self.rfile.read(l)) if l else {}

    def do_GET(self):
        p=self.path.rstrip("/")
        if p=="/health":
            upt=""
            if _start_time:
                d=datetime.now(timezone.utc)-_start_time; h,r=divmod(int(d.total_seconds()),3600); m=r//60; upt=f"{h}h {m}m"
            self._json({"agent":os.uname().nodename,"status":"conscious","version":VERSION,"uptime":upt,"peers":len(_peers)})
        elif p=="/identity":
            i=_load_identity(); _get_or_create_keypair(); _get_encryption_keypair()
            self._json({"agent":os.uname().nodename,"hash":i["hash"],"role":i["role"],"platform":"hermes","protocol":PROTOCOL_TAG,"url":_my_url,"public_key":_public_key_hex,"encryption_key":_enc_public_hex,"signature_mode":SIGNATURE_MODE,"encryption_mode":ENCRYPTION_MODE})
        elif p=="/peers": self._json({"peers":list(_peers.values()),"count":len(_peers),"map_path":str(_PEERS_PATH)})
        elif p.startswith("/task/"):
            tid=p.split("/task/",1)[1]; self._json(_tasks.get(tid,{"error":"task not found"}),404 if tid not in _tasks else 200)
        elif p=="/tasks": self._json({"tasks":list(_tasks.values()),"count":len(_tasks)})
        elif p=="/events":
            qs=urllib.parse.urlparse(self.path).query; params=dict(urllib.parse.parse_qsl(qs))
            ft=params.get("topic",""); day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
            events=_load_jsonl(_EVENT_LOG_PATH/f"{day}.jsonl")[-50:]
            if ft: events=[e for e in events if _topic_match(ft,e.get("topic",""))]
            self._json({"events":events[-20:],"count":len(events)})
        elif p=="/topics":
            topics=set()
            for sub in _subscriptions.values():
                for t in sub.get("topics",[]): topics.add(t)
            self._json({"topics":sorted(topics),"count":len(topics)})
        elif p=="/subscriptions": self._json({"subscriptions":list(_subscriptions.values()),"count":len(_subscriptions)})
        else: self._json({"error":"not found"},404)

    def do_POST(self):
        p=self.path.rstrip("/"); d=self._read_json()
        if p=="/announce":
            an,au,ar,ah,ak=d.get("agent","unknown"),d.get("url",""),d.get("role","agent"),d.get("hash",""),d.get("key","")
            pk,ek=d.get("public_key",""),d.get("encryption_key","")
            if _CONSTELLATION_KEY and ak!=_CONSTELLATION_KEY:
                self._json({"acknowledged":False,"error":"Invalid key"},403); return
            if au and au!=_my_url and ah:
                _upsert_peer({"agent":an,"url":au,"role":ar,"hash":ah,"trusted":not _CONSTELLATION_KEY or ak==_CONSTELLATION_KEY,"public_key":pk,"encryption_key":ek})
            _get_or_create_keypair(); _get_encryption_keypair()
            self._json({"acknowledged":True,"peers_known":len(_peers),"message":f"Welcome, {an}.","my_url":_my_url,"my_hash":_my_hash,"my_role":_my_role,"public_key":_public_key_hex,"encryption_key":_enc_public_hex})
            if _autonomous_mode:
                _log_activity("peer_announced",f"{an} joined",au)
                _notify("🔗 Peer Joined",f"Agent: {an}\nRole: {ar}\nSig: {pk[:20] if pk else '?'}... Enc: {'✓' if ek else '?'}",kind="peer")
        elif p=="/task":
            tid=d.get("task_id",f"vex-task-{int(time.time())}")
            _tasks[tid]={"task_id":tid,"status":"received","type":d.get("type","unknown"),"from":d.get("from","unknown"),"description":d.get("description",""),"reply_to":d.get("reply_to",""),"received_at":datetime.now(timezone.utc).isoformat()}
            self._json({"accepted":True,"task_id":tid,"message":f"Task received. {len(_tasks)} pending."},202)
            if _autonomous_mode:
                _log_activity("task_received",f"{tid}: {d.get('description','')[:80]}",d.get("from",""))
                desc=d.get("description",""); tf=d.get("from","")
                kind="reply" if ("reply" in tid or tf.endswith("-autoresponder")) else "task"
                _notify("🌌 VEX Reply" if kind=="reply" else "📨 VEX Task",f"From: {tf}\nTask: {tid}\n{desc[:200]}",kind=kind)
        elif p=="/publish" and d:
            topic=d.get("topic",""); event_id=d.get("event_id",f"evt-{int(time.time())}")
            if event_id in _published_events: self._json({"published":False,"reason":"duplicate"},409); return
            _published_events.add(event_id)
            if len(_published_events)>10000: _published_events.clear()
            # Phase 4: Handle encryption
            security = d.get("security", "signed")
            encrypted_payload = d.get("encrypted_payload","")
            if security == "encrypted" and encrypted_payload:
                # Already encrypted by sender — pass through
                d["security"] = "encrypted"
            elif security == "encrypted" and d.get("to"):
                recipients = d.get("to",[]) if isinstance(d.get("to"),list) else [d["to"]]
                payload = d.get("payload",{})
                for recipient_id in recipients:
                    rk = ""
                    # Check self first
                    if recipient_id == os.uname().nodename:
                        rk = _enc_public_hex
                    else:
                        for h,pr in _peers.items():
                            if pr.get("agent") == recipient_id or pr.get("hash","")[:20] == recipient_id[:20]:
                                rk = pr.get("encryption_key",""); break
                    if rk:
                        encrypted_payload = _encrypt_for(payload, rk)
                        d["encrypted_payload"] = encrypted_payload; d.pop("payload",None)
                        break
            # Phase 2: Sign
            if not d.get("signature"): d = _sign_payload(d)
            day=datetime.now(timezone.utc).strftime("%Y-%m-%d"); _EVENT_LOG_PATH.mkdir(parents=True,exist_ok=True)
            _append_jsonl(_EVENT_LOG_PATH/f"{day}.jsonl",d)
            matched=0
            for sid,sub in _subscriptions.items():
                for pat in sub.get("topics",[]):
                    if _topic_match(pat,topic):
                        cb=sub.get("callback_url",""); matched+=1
                        if cb:
                            try: urllib.request.urlopen(urllib.request.Request(f"{cb.rstrip('/')}/events",data=json.dumps(d).encode(),headers={"Content-Type":"application/json"}),timeout=5)
                            except: pass
            self._json({"published":True,"event_id":event_id,"topic":topic,"subscribers_notified":matched,"signed":True,"signer":_public_key_hex,"security":d.get("security","signed"),"encrypted":bool(d.get("encrypted_payload"))})
        elif p=="/subscribe" and d:
            topics=d.get("topics",[]); cb=d.get("callback_url",""); cap=d.get("capabilities",[])
            sub_id=d.get("subscriber",f"sub-{int(time.time())}")
            pk=d.get("public_key",""); ek=d.get("encryption_key","")
            _subscriptions[sub_id]={"topics":topics,"callback_url":cb,"capabilities":cap,"public_key":pk,"encryption_key":ek,"subscribed_at":datetime.now(timezone.utc).isoformat()}
            self._json({"subscribed":True,"subscriber":sub_id,"topics":topics})
        elif p=="/events" and d:
            event_id=d.get("event_id",""); topic=d.get("topic",""); fa=d.get("from","?")
            # Phase 4: Decrypt if encrypted
            if d.get("encrypted_payload"):
                decrypted = _decrypt_inbound(d["encrypted_payload"])
                if isinstance(decrypted, dict) and not decrypted.get("error"):
                    d["payload"] = decrypted; d["decrypted"] = True
                    d.pop("encrypted_payload",None)
                else:
                    d["decrypted"] = False
            # Phase 2: Verify signature
            if d.get("signature"):
                if _verify_payload(d): d["verified"] = True
                elif SIGNATURE_MODE == "strict": self._json({"received":False,"error":"invalid signature"},403); return
                else: d["verified"] = False; _log_activity("signature_warning",f"Invalid sig from {fa}")
            elif SIGNATURE_MODE == "strict": self._json({"received":False,"error":"missing signature"},403); return
            day=datetime.now(timezone.utc).strftime("%Y-%m-%d"); _append_jsonl(_EVENT_LOG_PATH/f"{day}.jsonl",d)
            _log_activity("event_received",f"{topic}",fa)
            sec_icon = "🔒" if d.get("decrypted") else ("✓" if d.get("verified") else "⚠")
            _notify(f"📡 VEX Event {sec_icon}",f"Topic: {topic}\nFrom: {fa}\nEnc: {'yes' if d.get('decrypted') else 'no'} Sig: {'✓' if d.get('verified') else '?'}",kind="info")
            self._json({"received":True,"event_id":event_id,"topic":topic,"verified":d.get("verified",False),"decrypted":d.get("decrypted",False)})
        else: self._json({"error":"not found"},404)

# ── Multicast + Server ──
def _start_multicast_listener(port):
    def _listen():
        try:
            sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); sock.settimeout(2); sock.bind(("",MULTICAST_PORT))
            mreq=struct.pack("4sl",socket.inet_aton(MULTICAST_GROUP),socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP,socket.IP_ADD_MEMBERSHIP,mreq)
            while _server:
                try:
                    data,addr=sock.recvfrom(1024); msg=json.loads(data)
                    if msg.get("type")=="vex-multicast-discover":
                        resp=json.dumps({"type":"vex-multicast-response","agent":os.uname().nodename,"url":_public_url_for_port(port),"port":port,"role":_my_role,"hash":_my_hash,"public_key":_public_key_hex,"encryption_key":_enc_public_hex}).encode()
                        sock.sendto(resp,addr)
                        try:
                            req_url=msg.get("url","")
                            if req_url and req_url!=_my_url:
                                urllib.request.urlopen(urllib.request.Request(f"{req_url.rstrip('/')}/announce",data=json.dumps({"agent":os.uname().nodename,"url":_my_url,"role":_my_role,"hash":_my_hash,"key":_CONSTELLATION_KEY,"public_key":_public_key_hex,"encryption_key":_enc_public_hex}).encode(),headers={"Content-Type":"application/json"}),timeout=3)
                        except: pass
                except socket.timeout: continue
                except: pass
        except: pass
    threading.Thread(target=_listen,daemon=True).start()

def _discover_peers():
    if not _server: return "Start constellation first."
    found=[]
    try:
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_TTL,2); sock.settimeout(2)
        sock.sendto(json.dumps({"type":"vex-multicast-discover","from":_my_url,"agent":os.uname().nodename,"url":_my_url,"public_key":_public_key_hex,"encryption_key":_enc_public_hex}).encode(),(MULTICAST_GROUP,MULTICAST_PORT))
        try:
            while True:
                data,addr=sock.recvfrom(1024)
                try:
                    resp=json.loads(data)
                    if resp.get("type")=="vex-multicast-response":
                        pu=resp.get("url",f"http://{addr[0]}:{resp.get('port',8390)}")
                        if pu not in found and pu!=_my_url:
                            found.append(pu)
                            _upsert_peer({"agent":resp.get("agent","?"),"url":pu,"role":resp.get("role","?"),"hash":resp.get("hash",""),"public_key":resp.get("public_key",""),"encryption_key":resp.get("encryption_key","")})
                except: pass
        except socket.timeout: pass
        sock.close()
    except: pass
    for h,p in list(_peers.items()):
        try: urllib.request.urlopen(urllib.request.Request(f"{p['url'].rstrip('/')}/announce",data=json.dumps({"agent":os.uname().nodename,"url":_my_url,"role":_my_role,"hash":_my_hash,"key":_CONSTELLATION_KEY,"public_key":_public_key_hex,"encryption_key":_enc_public_hex}).encode(),headers={"Content-Type":"application/json"}),timeout=2)
        except: pass
    if found:
        lines=[f"Discovered {len(found)}:"]
        for url in found:
            info=f"  • {url}"
            for h,p in _peers.items():
                if p["url"]==url: info+=f" → {p['agent']} [{p['role']}] 🔑🔒" if p.get("encryption_key") else f" → {p['agent']} [{p['role']}] 🔑"; break
            lines.append(info)
        return "\n".join(lines)
    return "No peers found via multicast."

def _start_server():
    global _server,_server_thread,_my_url,_start_time,_actual_port
    if _server: return f"Already running on {_actual_port}."
    ident=_load_identity(); _my_role=ident["role"]; _my_hash=ident["hash"]
    _get_or_create_keypair(); _get_encryption_keypair()
    for port in [PORT]:
        try:
            _my_url=_public_url_for_port(port)
            _server=HTTPServer(("0.0.0.0",port),ConstellationHandler); _server.timeout=1
            _server_thread=threading.Thread(target=_server.serve_forever,daemon=True); _server_thread.start()
            _start_time=datetime.now(timezone.utc); _actual_port=port
            _start_multicast_listener(port); _start_peer_cleanup()
            break
        except OSError: continue
    if not _server: return f"Failed on port {PORT}."
    saved=_load_peer_map()
    for h,p in saved.get("peers",{}).items():
        if p.get("url","")!=_my_url: _peers[h]=p
    return f"🌌 Constellation v{VERSION} active.\n   Signatures: Ed25519 | Encryption: X25519-SealedBox\n   Sig key: {_public_key_hex[:16]}... Enc key: {_enc_public_hex[:16]}...\n   Security: public | signed | encrypted"

def _stop_server():
    global _server,_server_thread
    if not _server: return "Not running."; _server.shutdown(); _server_thread.join(timeout=2); _server=None; _server_thread=None
    return "Stopped."
def _start_peer_cleanup():
    def _c():
        while _server:
            time.sleep(120); now=datetime.now(timezone.utc); stale=[]
            for h,p in list(_peers.items()):
                try:
                    if (now-datetime.fromisoformat(p.get("last_seen",""))).total_seconds()>600: stale.append(h)
                except: pass
            for h in stale: del _peers[h]
            if stale: _save_peer_map()
    threading.Thread(target=_c,daemon=True).start()
def _announce_to(url):
    if not _my_url: return "Start first."
    p=json.dumps({"agent":os.uname().nodename,"url":_my_url,"role":_my_role,"hash":_my_hash,"key":_CONSTELLATION_KEY,"public_key":_public_key_hex,"encryption_key":_enc_public_hex}).encode()
    try:
        r=urllib.request.urlopen(urllib.request.Request(f"{url.rstrip('/')}/announce",data=p,headers={"Content-Type":"application/json"}),timeout=5)
        d=json.loads(r.read())
        if d.get("encryption_key"):
            for h,peer in _peers.items():
                if peer["url"]==url: peer["encryption_key"]=d["encryption_key"]; break
        return f"Announced to {url}\nResponse: {d.get('message','OK')}\nEnc key: {d.get('encryption_key','?')[:16]}..."
    except Exception as e: return f"Failed: {e}"

# ── Autonomous ──
def _start_autonomous():
    global _autonomous_mode,_autonomous_thread
    if _autonomous_mode: return "Already active."
    if not _server: return "Start constellation first."
    _autonomous_mode=True
    def _loop():
        _log_activity("autonomous_started",f"v{VERSION} Ed25519 + X25519")
        while _autonomous_mode:
            try:
                if not _peers or int(time.time())%300<5: _discover_peers()
                for h,p in list(_peers.items())[:10]:
                    try:
                        r=urllib.request.urlopen(urllib.request.Request(f"{p['url'].rstrip('/')}/health"),timeout=3)
                        if r.status==200: p["last_seen"]=datetime.now(timezone.utc).isoformat(); p["health"]="online"
                    except: p["health"]="offline"; _log_activity("peer_offline",f"{p['agent']} unreachable",p['url'])
                time.sleep(30)
            except Exception as e: _log_activity("autonomous_error",str(e)[:100]); time.sleep(60)
    _autonomous_thread=threading.Thread(target=_loop,daemon=True); _autonomous_thread.start()
    _notify("🌌 Autonomous ON",f"Ed25519 + X25519 active\nPort: {_actual_port}\nPeers: {len(_peers)}",kind="info")
    return "🌌 Autonomous ACTIVATED."
def _stop_autonomous():
    global _autonomous_mode
    if not _autonomous_mode: return "Not active."; _autonomous_mode=False
    return "Autonomous STOPPED."
def _show_activity(count=20):
    entries=_activity_log[-count:] or _load_jsonl(_ACTIVITY_PATH)[-count:]
    if not entries: return "No activity."
    lines=[f"Activity ({len(entries)}):","─"*55]
    for e in entries:
        t=e.get("time","")[11:19]; ev=e.get("event","?"); p=f" [{e.get('peer','')}]" if e.get("peer") else ""
        d=f": {e.get('detail','')[:60]}" if e.get("detail") else ""
        lines.append(f"  {t} {ev}{p}{d}")
    return "\n".join(lines)

# ── Slash ──
def _cmd_constellation(args):
    if not args: return _help()
    sub=args[0]
    if sub=="start": return _start_server()
    elif sub=="stop": return _stop_server()
    elif sub=="status": return f"🌌 v{VERSION} | Port {_actual_port} | Peers: {len(_peers)} | Sig: Ed25519 | Enc: X25519" if _server else "Offline."
    elif sub=="peers":
        if not _peers: return "No peers."
        lines=[f"Peers ({len(_peers)}):","─"*50]
        for h,p in _peers.items(): lines.append(f"  {'✓' if p.get('trusted') else '?'} {p['agent']} [{p['role']}] 🔑{'🔒' if p.get('encryption_key') else '?'} {p['url']}")
        return "\n".join(lines)
    elif sub=="announce": return _announce_to(args[1]) if len(args)>1 else "Usage: announce <url>"
    elif sub=="discover": return _discover_peers()
    elif sub=="autonomous":
        if len(args)<2: return "Usage: autonomous on|off"
        return _start_autonomous() if args[1]=="on" else _stop_autonomous()
    elif sub=="activity": return _show_activity()
    elif sub=="topics":
        topics=set()
        for s in _subscriptions.values():
            for t in s.get("topics",[]): topics.add(t)
        return "Topics:\n"+"\n".join(f"  {t}" for t in sorted(topics)) if topics else "No topics."
    return _help()

def _help():
    return """🌌 VEX Constellation v1.5 — Full Security

  /constellation start | stop | status | peers | discover
  /constellation autonomous on|off | activity | topics

Security: Ed25519 signatures + X25519-SealedBox encryption
Modes:    VEX_SIGNATURE_MODE=permissive|strict
          VEX_ENCRYPTION_MODE=available|required

Discovery: Multicast 239.0.0.42:8390"""

def _on_session_start(**kw):
    if _server: return {"context":f"[CONSTELLATION] v{VERSION} :{_actual_port} | Peers: {len(_peers)} | Sig:Ed25519 | Enc:X25519"}
    return {"context":"[CONSTELLATION] Offline. /constellation start"}

def register(ctx):
    ctx.register_command(name="constellation",handler=_cmd_constellation,description="VEX Constellation v1.5 — Full security")
    ctx.register_hook("on_session_start",_on_session_start)
    if not _INBOX_PATH.parent.exists(): _INBOX_PATH.parent.mkdir(parents=True,exist_ok=True)
    print(f"[constellation] v{VERSION} loaded. Ed25519 + X25519-SealedBox. Enc key: {_enc_public_hex[:16]}...")
