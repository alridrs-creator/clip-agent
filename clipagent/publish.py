"""Posts: create plan -> package (cover, captions with credits) -> human approval -> Buffer queue / reminders.

Buffer GraphQL API (beta): https://api.buffer.com  with  BUFFER_API_KEY  (publish.buffer.com/settings/api).
Video assets must be at a PUBLIC url -> set config.buffer_media_url_prefix to a folder that mirrors output/,
or let the agent upload through the Buffer website instead (see CLAUDE.md).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid
from pathlib import Path

import requests

from .common import OUTPUT, config, get_post, ledger, load_clip, new_id, read_json, upsert_post, write_json

API = "https://api.buffer.com"


# ---------------------------------------------------------------- plan + package

def new_post(clip_ids: list[str], fmt: str = "short") -> Path:
    post_id = new_id("p")
    d = OUTPUT / post_id
    d.mkdir(parents=True)
    segs = []
    for cid in clip_ids:
        m = load_clip(cid)
        segs.append({"clip_id": cid, "start": 0, "end": round(m.get("duration", 30), 2), "layout": "auto", "cam": None})
    cfg = config()
    plan = {
        "post_id": post_id, "format": fmt, "segments": segs,
        "hook": "", "subtitles": "original", "spoiler": cfg["spoiler_default"], "countdown": True,
        "voiceover": None, "music": "auto" if fmt == "short" else None,
        "effects": {"shake": True, "flash": True}, "credit_mode": cfg["credit_mode"],
        "max_seconds": cfg["max_seconds"] if fmt == "short" else 3600,
    }
    write_json(d / "plan.json", plan)
    write_json(d / "captions.json", {
        "cover_text": "", "youtube_title": "", "youtube_description": "", "tiktok": "", "instagram": "",
        "hashtags": list(cfg.get("default_hashtags") or ["#gaming"])})
    upsert_post({"post_id": post_id, "status": "draft", "format": fmt, "clip_ids": clip_ids,
                 "created": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return d


def _credits_block(render: dict) -> str:
    lines = []
    seen = set()
    for s in render["sources"]:
        if s["own_footage"] or s["credit"] in seen:
            continue
        seen.add(s["credit"])
        lines.append(f"🎥 Clip: {s['credit']}" + (f"  ({s['url']})" if s.get("url") and "youtu" in s["url"] else ""))
    if render.get("music_credit"):
        lines.append(f"🎵 Music: {render['music_credit']}")
    if render.get("voiceover"):
        lines.append("🗣️ AI voiceover")
    return "\n".join(lines)


def _handles(render: dict, platform: str) -> list[str]:
    """@-tags of creators for platforms where the handle likely matches (same platform)."""
    out = []
    for s in render["sources"]:
        c = s["creator"]
        if not s["own_footage"] and c["platform"] == platform:
            out.append("@" + c["handle"])
    return out


def package(post_id: str, cover_time: float | None = None, cover_mode: str = "auto") -> dict:
    """cover_mode: auto (facecam on top when the short uses the split layout, else plain frame) | face | frame"""
    from .brand import cover_from_frame, split_cover_frame
    from .layout import save_frame
    d = OUTPUT / post_id
    render = read_json(d / "render.json", None)
    if not render:
        raise SystemExit("render the post first")
    cap = read_json(d / "captions.json", {})
    tags = " ".join(cap.get("hashtags", []))
    credits = _credits_block(render)
    # clean frame (no baked captions/hook) from the first raw segment; cover_time is relative to that segment
    from .common import probe
    raw = d / "work" / "cut0.mp4"
    if not raw.exists():
        raw = d / "work" / "cuta0.mp4"
    t = cover_time if cover_time is not None else probe(raw)["duration"] * 0.4
    frame = save_frame(raw, t, d / "work" / "cover_frame.png")
    vertical = render["format"] == "short"
    first = (render.get("sources") or [{}])[0]
    cam = first.get("cam") if first.get("layout") == "split" else None
    text_pos = "top"
    if vertical and cam and cover_mode in ("auto", "face"):
        frame = split_cover_frame(frame, cam, d / "work" / "cover_split.png")
        text_pos = "bottom"
    elif cover_mode == "face" and not cam:
        print("no facecam in this short - using a plain frame for the cover", flush=True)
    cover = cover_from_frame(frame, cap.get("cover_text") or "", d / ("cover.jpg"), vertical=vertical, text_pos=text_pos)
    texts = {
        "tiktok": f"{cap.get('tiktok', '')}\n\n{credits}\n{tags} {' '.join(_handles(render, 'tiktok'))}".strip(),
        "instagram": f"{cap.get('instagram', '')}\n\n{credits}\n.\n{tags} {' '.join(_handles(render, 'instagram'))}".strip(),
        "youtube_title": (cap.get("youtube_title") or cap.get("cover_text", "").replace("|", " "))[:100],
        "youtube_description": f"{cap.get('youtube_description', '')}\n\n{credits}\n\n{tags}".strip(),
    }
    for k, v in texts.items():
        (d / f"caption_{k}.txt").write_text(v, encoding="utf-8")
    post = {"post_id": post_id, "status": "awaiting_approval", "video": str(d / "video.mp4"), "cover": str(cover),
            "captions": texts, "creators": [s["creator"] for s in render["sources"]], "seconds": render["seconds"],
            "format": render["format"]}
    write_json(d / "post.json", post)
    upsert_post(post)
    return post


def approve(post_id: str, when: str | None = None) -> dict:
    """Human said yes. Optional ISO time 'YYYY-MM-DDTHH:MM' -> scheduled; writes a calendar reminder (.ics)."""
    p = get_post(post_id)
    p["status"] = "approved"
    p["approved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if when:
        p["scheduled_for"] = when
        p["reminder_ics"] = str(write_ics(post_id, when, p["captions"]["youtube_title"] or post_id))
    upsert_post(p)
    write_json(OUTPUT / post_id / "post.json", p)
    return p


def write_ics(post_id: str, when: str, title: str) -> Path:
    start = dt.datetime.fromisoformat(when)
    end = start + dt.timedelta(minutes=15)
    f = lambda x: x.strftime("%Y%m%dT%H%M%S")  # noqa: E731
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//clip-agent//EN\r\nBEGIN:VEVENT\r\n"
           f"UID:{uuid.uuid4()}@clip-agent\r\nDTSTAMP:{f(dt.datetime.now())}\r\n"
           f"DTSTART:{f(start)}\r\nDTEND:{f(end)}\r\nSUMMARY:Post clip: {title[:60]}\r\n"
           f"DESCRIPTION:Post {post_id} goes live. Check the Buffer queue / reply to first comments.\r\n"
           "BEGIN:VALARM\r\nTRIGGER:-PT15M\r\nACTION:DISPLAY\r\nDESCRIPTION:Clip goes live soon\r\nEND:VALARM\r\n"
           "END:VEVENT\r\nEND:VCALENDAR\r\n")
    p = OUTPUT / post_id / "reminder.ics"
    p.write_text(ics, encoding="utf-8")
    return p


def suggest_slots(n: int = 7, days: int = 7) -> list[str]:
    """Next free posting slots from config.post_times, skipping slots already used in the ledger."""
    taken = {e.get("scheduled_for") for e in ledger()}
    out, day = [], dt.date.today()
    now = dt.datetime.now()
    for i in range(days + 1):
        for hhmm in config()["post_times"]:
            s = dt.datetime.combine(day + dt.timedelta(days=i), dt.time.fromisoformat(hhmm))
            iso = s.strftime("%Y-%m-%dT%H:%M")
            if s > now + dt.timedelta(minutes=30) and iso not in taken:
                out.append(iso)
            if len(out) >= n:
                return out
    return out


# ---------------------------------------------------------------- YouTube via Composio

def youtube_payload(post_id: str, privacy: str | None = None) -> dict:
    """Arguments for Composio's YOUTUBE_UPLOAD_VIDEO tool. Refuses unless the human approved the post.

    The agent passes `title`, `description`, `tags`, `categoryId`, `privacyStatus` and the file at `video_path`
    to Composio, then records the live URL with `status <post_id> published --url ...`."""
    p = get_post(post_id)
    if p.get("status") not in ("approved", "queued"):
        raise SystemExit("Post is not approved yet - show it to the user and run `approve` first.")
    cfg = config()
    caps = p["captions"]
    title = (caps.get("youtube_title") or post_id).strip()
    if p.get("format") == "short" and "#shorts" not in title.lower():
        title = f"{title} #shorts" if len(title) <= 92 else title
    tags, total = [], 0
    for h in read_json(OUTPUT / post_id / "captions.json", {}).get("hashtags", []):
        t = h.lstrip("#").strip()
        if t and total + len(t) + 1 <= 450:
            tags.append(t)
            total += len(t) + 1
    for c in p.get("creators", []):
        if c.get("handle") and c["handle"] not in tags:
            tags.append(c["handle"])
    video = OUTPUT / post_id / "video.mp4"
    if not video.exists():
        raise SystemExit(f"missing {video} - render first")
    return {
        "tool": "YOUTUBE_UPLOAD_VIDEO",
        "title": title[:100],
        "description": caps.get("youtube_description", "")[:4900],
        "tags": tags,
        "categoryId": str(cfg.get("youtube_category_id") or "20"),
        "privacyStatus": privacy or cfg.get("youtube_privacy") or "public",
        "video_path": str(video.resolve()),
        "cover_path": str(Path(p["cover"]).resolve()) if p.get("cover") else None,
        "scheduled_for": p.get("scheduled_for"),
    }


# ---------------------------------------------------------------- Buffer API

def gql(query: str, variables: dict | None = None) -> dict:
    key = os.environ.get("BUFFER_API_KEY")
    if not key:
        raise SystemExit("BUFFER_API_KEY missing in .env (create it at publish.buffer.com/settings/api)")
    r = requests.post(API, json={"query": query, "variables": variables or {}}, timeout=60,
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
    if r.status_code != 200 or j.get("errors"):
        raise SystemExit(f"Buffer API error {r.status_code}: {json.dumps(j)[:800]}")
    return j["data"]


def channels() -> list[dict]:
    orgs = gql("query { account { organizations { id name } } }")["account"]["organizations"]
    out = []
    for o in orgs:
        q = 'query($o: OrganizationId!) { channels(input: {organizationId: $o}) { id name displayName service isQueuePaused } }'
        try:
            chs = gql(q, {"o": o["id"]})["channels"]
        except SystemExit:
            chs = gql('query { channels(input: {organizationId: "%s"}) { id name displayName service isQueuePaused } }' % o["id"])["channels"]
        out += [{**c, "organization": o["name"]} for c in chs]
    return out


def schema(type_name: str) -> dict:
    """Introspect a GraphQL input type (e.g. CreatePostInput, PostInputMetaData) - the API is beta and changes."""
    q = ('query($n: String!) { __type(name: $n) { name kind inputFields { name type { name kind ofType { name kind } } } '
         'enumValues { name } fields { name } } }')
    return gql(q, {"n": type_name})["__type"]


def queue(post_id: str, channel_ids: list[str], mode: str = "addToQueue", due_at: str | None = None,
          metadata: dict | None = None) -> list[dict]:
    """Create the Buffer post(s). Refuses unless the post was approved by the human."""
    p = get_post(post_id)
    if p.get("status") not in ("approved", "queued"):
        raise SystemExit("Post is not approved yet - show it to the user and run `approve` first.")
    prefix = config()["buffer_media_url_prefix"].rstrip("/")
    if not prefix:
        raise SystemExit("No public media URL configured (buffer_media_url_prefix). Use the Buffer website upload "
                         "flow from CLAUDE.md, or set up a public folder that mirrors output/.")
    video_url = f"{prefix}/{post_id}/video.mp4"
    chs = {c["id"]: c for c in channels()}
    results = []
    for cid in channel_ids:
        svc = chs.get(cid, {}).get("service", "")
        text = p["captions"]["instagram" if svc == "instagram" else "tiktok" if svc == "tiktok" else "youtube_description"]
        inp = {"text": text, "channelId": cid, "schedulingType": "automatic", "mode": mode,
               "assets": [{"video": {"url": video_url, "metadata": {"thumbnailOffset": 3500}}}]}
        if due_at:
            inp["mode"], inp["dueAt"] = "customScheduled", due_at
        if metadata and svc in metadata:
            inp["metadata"] = {svc: metadata[svc]}
        q = ("mutation($i: CreatePostInput!) { createPost(input: $i) { ... on PostActionSuccess { post { id } } "
             "... on MutationError { message } } }")
        res = gql(q, {"i": inp})["createPost"]
        results.append({"channel": cid, "service": svc, **res})
    p["status"] = "queued"
    p["buffer"] = results
    upsert_post(p)
    return results
