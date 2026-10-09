import os, time, secrets
from flask import Flask, render_template, redirect, request, session, jsonify, abort
from flask_socketio import SocketIO, join_room, emit

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
sio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

ADMIN_PATH = os.environ.get("ADMIN_PATH", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")
cfg = {"max_viewers": int(os.environ.get("MAX_VIEWERS", 4))}

rooms = {}  # room -> {members:[sid..] (first = host), kind, id, playing, t, at, chat}
where = {}  # sid -> room
last = {}   # sid -> last chat time


@app.route("/")
def home():
    return redirect(f"/r/{secrets.token_urlsafe(4)}")

@app.route("/r/<room>")
def room_page(room):
    return render_template("room.html", room=room)


# ---------- helpers ----------
def snap(r):
    s = rooms[r]
    t = s["t"] + (time.time() - s["at"] if s["playing"] else 0)
    return {"kind": s["kind"], "id": s["id"], "playing": s["playing"], "t": t, "at": time.time()}

def roster(r):
    m = rooms[r]["members"]
    sio.emit("roster", {"host": m[0], "viewers": m[1:], "max": cfg["max_viewers"]}, to=r)

def drop(sid):
    last.pop(sid, None)
    r = where.pop(sid, None)
    if not r or r not in rooms:
        return
    room = rooms[r]
    m = room["members"]
    was_host = bool(m) and m[0] == sid
    if sid in m:
        m.remove(sid)
    if not m:
        del rooms[r]
        return
    if was_host and room["kind"] == "local":
        room.update(kind=None, id=None, playing=False, t=0)
        sio.emit("reset", {}, to=r)
    roster(r)

def kick(sid):
    sio.emit("kicked", {}, to=sid)
    sio.server.disconnect(sid, namespace="/")

def is_host(sid):
    r = where.get(sid)
    return bool(r) and rooms[r]["members"][0] == sid


# ---------- sockets ----------
@sio.on("time")
def clock():
    return time.time()

@sio.on("join")
def join(d):
    r, sid = d["room"], request.sid
    if sid in where:
        return
    room = rooms.get(r)
    if room and len(room["members"]) - 1 >= cfg["max_viewers"]:
        emit("full", {"max": cfg["max_viewers"]})
        return
    room = rooms.setdefault(r, {"members": [], "kind": None, "id": None,
                                "playing": False, "t": 0, "at": time.time(), "chat": []})
    room["members"].append(sid)
    where[sid] = r
    join_room(r)
    roster(r)
    if room["chat"]:
        emit("history", room["chat"])
    if room["kind"]:
        emit("state", snap(r))

@sio.on("resync")
def resync():
    r = where.get(request.sid)
    if r and rooms[r]["kind"]:
        emit("state", snap(r))

@sio.on("load")
def load(d):
    if not is_host(request.sid) or d.get("kind") not in ("yt", "local"):
        return
    r = where[request.sid]
    rooms[r].update(kind=d["kind"], id=str(d["id"])[:200], playing=False, t=0, at=time.time())
    emit("load", {"kind": d["kind"], "id": rooms[r]["id"]}, to=r, include_self=False)

@sio.on("ctl")  # youtube sync, any member
def ctl(d):
    r = where.get(request.sid)
    if not r or rooms[r]["kind"] != "yt":
        return
    at = d.get("at")
    if not isinstance(at, (int, float)) or abs(at - time.time()) > 5:
        at = time.time()
    rooms[r].update(playing=bool(d["playing"]), t=float(d["t"]), at=at)
    emit("ctl", {"playing": bool(d["playing"]), "t": float(d["t"]), "at": at}, to=r, include_self=False)

@sio.on("pos")  # local file: host reports position to viewers
def pos(d):
    if not is_host(request.sid):
        return
    r = where[request.sid]
    if rooms[r]["kind"] == "local":
        emit("pos", {"t": float(d["t"]), "d": float(d["d"]), "p": bool(d["p"])}, to=r, include_self=False)

@sio.on("req")  # local file: viewer asks host to play/pause/seek
def req(d):
    r = where.get(request.sid)
    if not r or rooms[r]["kind"] != "local" or is_host(request.sid):
        return
    now = time.time()
    if now - last.get(("r", request.sid), 0) < 0.15:
        return
    last[("r", request.sid)] = now
    emit("req", {"playing": d.get("playing"), "t": d.get("t")}, to=rooms[r]["members"][0])

@sio.on("signal")
def signal(d):
    to = d["to"]
    if where.get(to) and where.get(to) == where.get(request.sid):
        emit("signal", {"from": request.sid, "data": d["data"]}, to=to)

@sio.on("chat")
def chat(d):
    r, sid = where.get(request.sid), request.sid
    if not r:
        return
    text = str(d.get("text", "")).strip()[:300]
    name = str(d.get("name", "")).strip()[:20] or "guest"
    now = time.time()
    if not text or now - last.get(sid, 0) < 0.4:
        return
    last[sid] = now
    m = {"name": name, "text": text}
    h = rooms[r]["chat"]
    h.append(m)
    del h[:-30]
    emit("chat", m, to=r)

@sio.on("disconnect")
def bye(*a):
    sid = request.sid
    last.pop(("r", sid), None)
    drop(sid)


# ---------- admin ----------
def authed():
    return bool(ADMIN_PASS) and session.get("adm") is True

@app.route(f"/{ADMIN_PATH}")
def admin():
    if not ADMIN_PASS:
        abort(404)
    return render_template("admin.html", authed=authed(), path=ADMIN_PATH)

@app.post(f"/{ADMIN_PATH}/login")
def admin_login():
    if ADMIN_PASS and secrets.compare_digest(request.form.get("p", "").encode(), ADMIN_PASS.encode()):
        session["adm"] = True
    return redirect(f"/{ADMIN_PATH}")

@app.get(f"/{ADMIN_PATH}/data")
def admin_data():
    if not authed():
        abort(404)
    return jsonify(max=cfg["max_viewers"], rooms=[
        {"room": r, "kind": v["kind"], "id": v["id"], "members": list(v["members"])}
        for r, v in rooms.items()])

@app.post(f"/{ADMIN_PATH}/set")
def admin_set():
    if not authed():
        abort(404)
    cfg["max_viewers"] = max(0, min(20, int(request.json["max"])))
    for r in list(rooms):
        roster(r)
    return "", 204

@app.post(f"/{ADMIN_PATH}/kick")
def admin_kick():
    if not authed():
        abort(404)
    kick(request.json["sid"])
    return "", 204

@app.post(f"/{ADMIN_PATH}/close")
def admin_close():
    if not authed():
        abort(404)
    for sid in list(rooms.get(request.json["room"], {}).get("members", [])):
        kick(sid)
    return "", 204


if __name__ == "__main__":
    sio.run(app, port=5000, debug=True)