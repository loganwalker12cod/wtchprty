import time, secrets
from flask import Flask, render_template, redirect, request
from flask_socketio import SocketIO, join_room, emit

app = Flask(__name__)
sio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")
rooms = {}  # room -> {members:set, kind, id, size, playing, t, at}
where = {}  # sid -> room
last = {}   # sid -> last chat time

@app.route("/")
def home():
    return redirect(f"/r/{secrets.token_urlsafe(4)}")

@app.route("/r/<room>")
def room_page(room):
    return render_template("room.html", room=room)

def snap(r):
    s = rooms[r]
    t = s["t"] + (time.time() - s["at"] if s["playing"] else 0)
    return {"kind": s["kind"], "id": s["id"], "size": s["size"], "playing": s["playing"], "t": t, "at": time.time()}

@sio.on("time")
def clock():
    return time.time()

@sio.on("join")
def join(d):
    r, sid = d["room"], request.sid
    if sid in where:
        return
    s = rooms.setdefault(r, {"members": set(), "kind": None, "id": None, "size": 0,
                             "playing": False, "t": 0, "at": time.time(), "chat": []})
    s["members"].add(sid)
    where[sid] = r
    join_room(r)
    sio.emit("count", len(s["members"]), to=r)
    if s["chat"]:
        emit("history", s["chat"])
    if s["kind"]:
        emit("state", snap(r))

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

@sio.on("resync")
def resync():
    r = where.get(request.sid)
    if r and rooms[r]["kind"]:
        emit("state", snap(r))

@sio.on("load")
def load(d):
    r = where.get(request.sid)
    if not r:
        return
    rooms[r].update(kind=d["kind"], id=d["id"], size=d.get("size", 0),
                    playing=False, t=0, at=time.time())
    emit("load", {"kind": d["kind"], "id": d["id"], "size": d.get("size", 0)},
         to=r, include_self=False)

@sio.on("ctl")
def ctl(d):
    r = where.get(request.sid)
    if not r or not rooms[r]["kind"]:
        return
    at = d.get("at")
    if not isinstance(at, (int, float)) or abs(at - time.time()) > 5:
        at = time.time()
    rooms[r].update(playing=d["playing"], t=d["t"], at=at)
    emit("ctl", {"playing": d["playing"], "t": d["t"], "at": at}, to=r, include_self=False)

@sio.on("disconnect")
def bye(*a):
    r = where.pop(request.sid, None)
    last.pop(request.sid, None)
    if r and r in rooms:
        rooms[r]["members"].discard(request.sid)
        if rooms[r]["members"]:
            sio.emit("count", len(rooms[r]["members"]), to=r)
        else:
            del rooms[r]

if __name__ == "__main__":
    sio.run(app, port=5000, debug=True)