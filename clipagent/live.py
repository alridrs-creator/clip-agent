"""Record a live Twitch stream + its chat at the same time, so the spike finder works on live streams too.

Video: streamlink -> library/<id>/live.ts   Chat: anonymous IRC -> library/<id>/chat.jsonl (t = seconds since start)
Stop with Ctrl+C or --minutes. Then: python -m clipagent spikes chat library/<id>/chat.jsonl
"""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time

from .common import clip_dir, creator, is_blocked, new_id, save_clip_meta, tool


def _chat_logger(channel: str, path, t0: float, stop: threading.Event) -> None:
    s = socket.socket()
    s.settimeout(2)
    s.connect(("irc.chat.twitch.tv", 6667))
    s.send(b"NICK justinfan%d\r\n" % (int(time.time()) % 99999))
    s.send(f"JOIN #{channel.lower()}\r\n".encode())
    buf = ""
    with open(path, "a", encoding="utf-8") as f:
        while not stop.is_set():
            try:
                buf += s.recv(4096).decode("utf-8", "ignore")
            except socket.timeout:
                continue
            *lines, buf = buf.split("\r\n")
            for line in lines:
                if line.startswith("PING"):
                    s.send(b"PONG :tmi.twitch.tv\r\n")
                elif " PRIVMSG " in line:
                    user = line[1:line.find("!")]
                    msg = line.split(" :", 1)[1] if " :" in line else ""
                    f.write(json.dumps({"t": round(time.time() - t0, 2), "user": user, "msg": msg}) + "\n")
                    f.flush()
    s.close()


def record(channel: str, minutes: float | None = None, quality: str = "best") -> dict:
    cr = creator(channel, "twitch", channel)
    if is_blocked(cr):
        raise SystemExit(f"{channel} is on the blocklist.")
    sl = tool("streamlink")
    if not sl:
        raise SystemExit("streamlink not installed (pip install streamlink).")
    clip_id = new_id("lv")
    d = clip_dir(clip_id)
    d.mkdir(parents=True, exist_ok=True)
    video, chat = d / "live.ts", d / "chat.jsonl"
    stop = threading.Event()
    proc = subprocess.Popen([sl, "--twitch-disable-ads", "-o", str(video), f"https://twitch.tv/{channel}", quality])
    t0 = time.time()
    th = threading.Thread(target=_chat_logger, args=(channel, chat, t0, stop), daemon=True)
    th.start()
    print(f"Recording {channel} -> {video}  (chat -> {chat}). Ctrl+C to stop.", flush=True)
    try:
        while proc.poll() is None:
            if minutes and time.time() - t0 > minutes * 60:
                proc.terminate()
                break
            time.sleep(1)
    except KeyboardInterrupt:
        proc.terminate()
    stop.set()
    th.join(timeout=5)
    proc.wait(timeout=30)
    meta = {"id": clip_id, "source": "twitch_live", "source_id": f"twitch_live:{channel}:{int(t0)}",
            "title": f"{channel} live {time.strftime('%Y-%m-%d %H:%M', time.localtime(t0))}",
            "url": f"https://twitch.tv/{channel}", "file": video.name, "chat": chat.name,
            "creator": cr, "recorded_seconds": round(time.time() - t0)}
    return save_clip_meta(meta)
