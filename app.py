import time, secrets
from flask import Flask, render_template, redirect
from flask_socketio import SocketIO, join_room, emit

app = Flask(__name__)
sio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")
rooms = {}

@app.route("/")
def home():
    return redirect(f"/r/{secrets.token_urlsafe(4)}")

@app.route("/r/<room>")
def room(room):
    return render_template("room.html", room=room)

@sio.on("join")
def join(d):
    join_room(d["room"])
    s = rooms.get(d["room"])
    if s:
        t = s["t"] + (time.time() - s["at"] if s["playing"] else 0)
        emit("state", {**s, "t": t})

@sio.on("load")
def load(d):
    rooms[d["room"]] = {"kind": d["kind"], "id": d["id"], "playing": False, "t": 0, "at": time.time()}
    emit("load", d, to=d["room"], include_self=False)

@sio.on("ctl")
def ctl(d):
    s = rooms.get(d["room"])
    if not s:
        return
    s.update(playing=d["playing"], t=d["t"], at=time.time())
    emit("ctl", d, to=d["room"], include_self=False)

if __name__ == "__main__":
    sio.run(app, port=5000, debug=True)