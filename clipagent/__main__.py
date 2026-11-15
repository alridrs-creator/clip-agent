"""One CLI for everything:  python -m clipagent <command> ...   (prints JSON so the agent can read it)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def out(x) -> None:
    print(json.dumps(x, indent=2, ensure_ascii=False, default=str))


def main() -> None:
    ap = argparse.ArgumentParser(prog="clipagent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check PC, tools, keys")
    pr = sub.add_parser("preset", help="apply a game preset from presets/ (e.g. gta6), or list them")
    pr.add_argument("name", nargs="?")
    c = sub.add_parser("config", help="show or set config: config key=value ...")
    c.add_argument("pairs", nargs="*")

    # --- sources
    t = sub.add_parser("twitch-top", help="top Twitch clips for the game or one streamer")
    t.add_argument("--hours", type=int, default=24)
    t.add_argument("--limit", type=int, default=15)
    t.add_argument("--streamer")
    t.add_argument("--min-views", type=int, default=0)
    t.add_argument("--language")
    t = sub.add_parser("twitch-clip", help="download Twitch clip(s) by url/id")
    t.add_argument("urls", nargs="+")
    t = sub.add_parser("twitch-vods", help="latest VODs of a streamer")
    t.add_argument("streamer")
    t.add_argument("-n", type=int, default=5)
    t = sub.add_parser("twitch-chat", help="download VOD chat replay")
    t.add_argument("vod")
    t = sub.add_parser("twitch-vod-cut", help="download part of a VOD")
    t.add_argument("vod")
    t.add_argument("start", type=float)
    t.add_argument("end", type=float)
    t.add_argument("--title")
    t = sub.add_parser("live", help="record a live stream + chat")
    t.add_argument("channel")
    t.add_argument("--minutes", type=float)
    y = sub.add_parser("yt-search", help="search YouTube")
    y.add_argument("query")
    y.add_argument("-n", type=int, default=10)
    y.add_argument("--recent", action="store_true")
    y = sub.add_parser("yt-info", help="save YouTube metadata + most-replayed heatmap")
    y.add_argument("url")
    y = sub.add_parser("yt-download", help="download a YouTube video or a section of it")
    y.add_argument("url")
    y.add_argument("--start", type=float)
    y.add_argument("--end", type=float)
    y.add_argument("--title")
    i = sub.add_parser("inbox", help="import user's own recordings from inbox/")
    i.add_argument("--handle")
    i.add_argument("--platform", default="youtube")
    sub.add_parser("clips", help="list library clips")
    k = sub.add_parser("cut", help="cut a part of a library clip into a new clip")
    k.add_argument("clip_id")
    k.add_argument("start", type=float)
    k.add_argument("end", type=float)
    k.add_argument("--title")

    # --- highlights
    s = sub.add_parser("spikes", help="find highlights: chat <file> | audio <video> | heatmap <info.json>")
    s.add_argument("kind", choices=["chat", "audio", "heatmap"])
    s.add_argument("path")
    s.add_argument("--top", type=int, default=10)
    f = sub.add_parser("frame", help="save a frame (to look at): frame <clip_id|video> <t> <out.png>")
    f.add_argument("src")
    f.add_argument("t", type=float)
    f.add_argument("out")
    f = sub.add_parser("transcript", help="sentence transcript of a clip (pick cuts that make sense)")
    f.add_argument("clip_id")
    f.add_argument("--start", type=float, default=0)
    f.add_argument("--end", type=float, default=1e9)
    f = sub.add_parser("facecam", help="detect webcam box in a clip, writes preview png")
    f.add_argument("clip_id")
    sub.add_parser("voices", help="list free voiceover voices")

    # --- edit + publish
    p = sub.add_parser("new-post", help="create output/<post_id>/plan.json from clip ids")
    p.add_argument("clip_ids", nargs="+")
    p.add_argument("--long", action="store_true", help="16:9 compilation instead of 9:16 short")
    r = sub.add_parser("render", help="render output/<post_id>/plan.json")
    r.add_argument("post_id")
    r = sub.add_parser("preview", help="contact sheet of the rendered video (for the agent to look at)")
    r.add_argument("post_id")
    r = sub.add_parser("package", help="cover + captions with credits, ready for approval")
    r.add_argument("post_id")
    r.add_argument("--cover-time", type=float)
    a = sub.add_parser("approve", help="human approved the post (optionally schedule)")
    a.add_argument("post_id")
    a.add_argument("--when", help="YYYY-MM-DDTHH:MM")
    st = sub.add_parser("status", help="set post status (queued/published) and live URLs")
    st.add_argument("post_id")
    st.add_argument("status", choices=["queued", "published", "rejected", "draft"])
    st.add_argument("--url", action="append", default=[])
    sub.add_parser("slots", help="suggest next posting slots")
    sub.add_parser("posts", help="list posts and status")
    sub.add_parser("buffer-channels", help="list Buffer channels")
    b = sub.add_parser("buffer-schema", help="introspect a Buffer GraphQL type")
    b.add_argument("type_name")
    b = sub.add_parser("buffer-queue", help="queue an APPROVED post to Buffer channels")
    b.add_argument("post_id")
    b.add_argument("channel_ids", nargs="+")
    b.add_argument("--due", help="ISO time for customScheduled")
    b.add_argument("--metadata", help="JSON per service, e.g. '{\"youtube\": {...}}'")

    # --- brand
    g = sub.add_parser("brand", help="make brand image: brand <kind> '<prompt>' --text 'NAME|TAG'")
    g.add_argument("kind", choices=["profile", "youtube_banner", "twitch_banner", "x_header", "thumbnail", "reel_cover"])
    g.add_argument("prompt")
    g.add_argument("--text", default="")
    g.add_argument("--out")
    g.add_argument("--provider", choices=["pollinations", "gemini"])
    n = sub.add_parser("names", help="check handle availability on YouTube/Twitch/TikTok/Instagram")
    n.add_argument("handles", nargs="+")

    d = sub.add_parser("takedown", help="creator asked to stop: blocklist + list posts to remove")
    d.add_argument("handle")
    d.add_argument("--platform")
    d.add_argument("--reason", default="creator request")
    d.add_argument("--delete-files", action="store_true")
    sub.add_parser("blocklist", help="show blocklisted creators")

    A = ap.parse_args()
    cmd = A.cmd

    if cmd == "doctor":
        from .doctor import check
        out(check())
    elif cmd == "preset":
        from .common import PRESETS, apply_preset, read_json
        if A.name:
            out(apply_preset(A.name))
        else:
            out([{"name": p.stem, **{k: read_json(p, {}).get(k) for k in ("game_name", "game_short")}}
                 for p in sorted(PRESETS.glob("*.json")) if not p.stem.startswith("_")])
    elif cmd == "config":
        from .common import config, save_config
        if A.pairs:
            upd = {}
            for pr in A.pairs:
                k, v = pr.split("=", 1)
                try:
                    upd[k] = json.loads(v)
                except json.JSONDecodeError:
                    upd[k] = v
            out(save_config(upd))
        else:
            out(config())
    elif cmd == "twitch-top":
        from .twitch import top_clips
        out(top_clips(A.hours, A.limit, A.streamer, A.min_views, A.language))
    elif cmd == "twitch-clip":
        from .twitch import download_clip
        out([download_clip(u) for u in A.urls])
    elif cmd == "twitch-vods":
        from .twitch import latest_vods
        out(latest_vods(A.streamer, A.n))
    elif cmd == "twitch-chat":
        from .common import DATA
        from .twitch import download_chat, vod_info
        v = vod_info(A.vod)
        p = download_chat(A.vod, DATA / "chat" / f"{v['id']}.json")
        out({"chat_file": str(p), "vod": v["id"], "streamer": v["user_name"], "duration": v["duration"]})
    elif cmd == "twitch-vod-cut":
        from .twitch import download_vod_segment
        out(download_vod_segment(A.vod, A.start, A.end, A.title))
    elif cmd == "live":
        from .live import record
        out(record(A.channel, A.minutes))
    elif cmd == "yt-search":
        from .youtube import search
        out(search(A.query, A.n, A.recent))
    elif cmd == "yt-info":
        from .youtube import info
        p = info(A.url)
        j = json.loads(p.read_text(encoding="utf-8"))
        out({"info_json": str(p), "title": j.get("title"), "channel": j.get("channel"), "handle": j.get("uploader_id"),
             "duration": j.get("duration"), "has_heatmap": bool(j.get("heatmap")), "views": j.get("view_count")})
    elif cmd == "yt-download":
        from .youtube import download
        out(download(A.url, A.start, A.end, A.title))
    elif cmd == "inbox":
        from .inbox import import_inbox
        out(import_inbox(A.handle, A.platform))
    elif cmd == "clips":
        from .common import all_clips
        out([{k: m.get(k) for k in ("id", "source", "title", "duration", "width", "height")} |
             {"credit": m["creator"]["handle"] + " @" + m["creator"]["platform"]} for m in all_clips()])
    elif cmd == "cut":
        from .render import cut_clip
        out(cut_clip(A.clip_id, A.start, A.end, A.title))
    elif cmd == "spikes":
        from . import spikes
        fn = {"chat": spikes.chat_spikes, "audio": spikes.audio_spikes, "heatmap": spikes.heatmap_spikes}[A.kind]
        out(fn(Path(A.path), top=A.top))
    elif cmd == "frame":
        from .common import LIBRARY, load_clip
        from .layout import save_frame
        src = Path(A.src)
        if not src.exists():
            m = load_clip(A.src)
            src = LIBRARY / m["id"] / m["file"]
        out({"frame": str(save_frame(src, A.t, Path(A.out)))})
    elif cmd == "transcript":
        from .transcribe import clip_transcript
        out([{"start": round(x["start"], 1), "end": round(x["end"], 1), "text": x["text"]}
             for x in clip_transcript(A.clip_id) if A.start <= x["start"] <= A.end])
    elif cmd == "facecam":
        from .common import LIBRARY, load_clip
        from .layout import detect_facecam
        m = load_clip(A.clip_id)
        prev = LIBRARY / m["id"] / "facecam.png"
        out({"cam": detect_facecam(LIBRARY / m["id"] / m["file"], prev), "preview": str(prev)})
    elif cmd == "voices":
        from .tts import list_voices
        out(list_voices())
    elif cmd == "new-post":
        from .publish import new_post
        d_ = new_post(A.clip_ids, "long" if A.long else "short")
        out({"post_dir": str(d_), "plan": str(d_ / "plan.json"), "captions": str(d_ / "captions.json")})
    elif cmd == "render":
        from .common import OUTPUT, upsert_post
        from .render import render
        res = render(OUTPUT / A.post_id / "plan.json")
        upsert_post({"post_id": A.post_id, "status": "rendered", "creators": [s["creator"] for s in res["sources"]]})
        out(res)
    elif cmd == "preview":
        from .common import OUTPUT, probe, run, tool
        d_ = OUTPUT / A.post_id
        dur = probe(d_ / "video.mp4")["duration"]
        ts = [1.3, 4.0, dur * 0.45, dur * 0.8]
        ins, labels = [], []
        for i, t in enumerate(ts):
            ins += ["-ss", f"{t:.2f}", "-i", str(d_ / "video.mp4")]
        sheet = d_ / "work" / "preview.png"
        run([tool("ffmpeg") or "ffmpeg", "-y", "-v", "error", *ins, "-frames:v", "1", "-filter_complex",
             "".join(f"[{i}:v]scale=360:-2[s{i}];" for i in range(4)) + "".join(f"[s{i}]" for i in range(4)) + "hstack=4",
             sheet], quiet=True)
        out({"preview": str(sheet), "times": [round(t, 1) for t in ts]})
    elif cmd == "package":
        from .publish import package
        out(package(A.post_id, A.cover_time))
    elif cmd == "approve":
        from .publish import approve
        out(approve(A.post_id, A.when))
    elif cmd == "status":
        from .common import get_post, upsert_post
        p_ = get_post(A.post_id)
        if A.status in ("queued", "published") and p_.get("status") not in ("approved", "queued", "published"):
            raise SystemExit("Post was never approved - get the user's OK and run `approve` first.")
        p_["status"] = A.status
        if A.url:
            p_["published_urls"] = sorted(set(p_.get("published_urls", []) + A.url))
        upsert_post(p_)
        out(p_)
    elif cmd == "slots":
        from .publish import suggest_slots
        out(suggest_slots())
    elif cmd == "posts":
        from .common import ledger
        out([{k: e.get(k) for k in ("post_id", "status", "format", "seconds", "scheduled_for")} for e in ledger()])
    elif cmd == "buffer-channels":
        from .publish import channels
        out(channels())
    elif cmd == "buffer-schema":
        from .publish import schema
        out(schema(A.type_name))
    elif cmd == "buffer-queue":
        from .publish import queue
        out(queue(A.post_id, A.channel_ids, due_at=A.due, metadata=json.loads(A.metadata) if A.metadata else None))
    elif cmd == "brand":
        from .brand import make
        out({"image": str(make(A.kind, A.prompt, A.text, Path(A.out) if A.out else None, A.provider))})
    elif cmd == "names":
        from .names import check
        out([check(h) for h in A.handles])
    elif cmd == "takedown":
        from .takedown import takedown
        out(takedown(A.handle, A.platform, A.reason, A.delete_files))
    elif cmd == "blocklist":
        from .common import blocklist
        out(blocklist())


if __name__ == "__main__":
    main()
