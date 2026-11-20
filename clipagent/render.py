"""Renderer: turns an edit plan into a finished 9:16 short (or a 16:9 compilation).

Pass 1 (per segment): layout (facecam split / blur / crop), shake + flash effects, word captions, credit tag.
Pass 2 (whole video): spoiler card, hook title, voiceover + its captions, ducked music, loudness normalize.

Plan JSON (written by the agent, see CLAUDE.md):
{
  "post_id": "...", "format": "short" | "long",
  "segments": [{"clip_id": "...", "start": 0, "end": 20, "layout": "auto", "cam": null}],
  "hook": "HE ROBBED THE BANK WITH A TRACTOR", "subtitles": "original" | "voiceover" | "none",
  "spoiler": true, "countdown": true,
  "voiceover": {"text": "...", "voice": "male", "start": 2.8},
  "music": "auto" | "music/file.mp3" | null, "effects": {"shake": true, "flash": true},
  "credit_mode": "corner" | "intro" | "both", "max_seconds": 59
}
"""
from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

from PIL import ImageFont

from .common import (BRAND, MUSIC, OUTPUT, ROOT, clip_dir, config, credit_line, load_clip, new_id, probe,
                     read_json, run, save_clip_meta, tool, write_json)
from .layout import detect_facecam
from .spikes import loud_moments
from .subs import ASS
from .transcribe import transcribe
from .tts import voiceover

W, H = 1080, 1920
FPS = 30


def ffmpeg() -> str:
    return tool("ffmpeg") or "ffmpeg"


def _encoder(final: bool) -> list[str]:
    enc = config()["encoder"]
    if enc == "auto":
        enc = "h264_nvenc" if _has_nvenc() else "libx264"
    if enc == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20" if final else "16",
                "-b:v", "0", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "medium" if final else "fast", "-crf", "20" if final else "16",
            "-profile:v", "high", "-pix_fmt", "yuv420p"]


_NVENC = None


def _has_nvenc() -> bool:
    global _NVENC
    if _NVENC is None:
        r = run([ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2", "-c:v", "h264_nvenc",
                 "-f", "null", "-"], quiet=True, check=False)
        _NVENC = r.returncode == 0
    return _NVENC


def font_file(caption: bool = False) -> Path:
    cfg = config()
    for p in (cfg.get("caption_font_file") if caption else None, cfg.get("font_file")):
        if p and Path(p).exists():
            return Path(p)
    for p in sorted((BRAND / "fonts").glob("*.[ot]tf")):
        return p
    for p in (Path("C:/Windows/Fonts/impact.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
              Path("/Library/Fonts/Impact.ttf")):
        if p.exists():
            return p
    raise SystemExit("No font found - put a .ttf in brand/fonts/")


def _prep_fonts(work: Path) -> str:
    fdir = work / "fonts"
    fdir.mkdir(exist_ok=True)
    f = font_file()
    shutil.copy(f, fdir / f.name)
    return ImageFont.truetype(str(f), 40).getname()[0]


# ---------------------------------------------------------------- cutting

def cut_clip(clip_id: str, start: float, end: float, title: str | None = None) -> dict:
    """New library clip from a part of a longer one (VOD, live recording, own upload). Keeps the credit."""
    src = load_clip(clip_id)
    new = new_id(src["id"][:2])
    d = clip_dir(new)
    d.mkdir(parents=True, exist_ok=True)
    out = d / "source.mp4"
    run([ffmpeg(), "-y", "-v", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i",
         clip_dir(clip_id) / src["file"], *_encoder(False), "-c:a", "aac", "-b:a", "192k", out])
    meta = {k: v for k, v in src.items() if k not in ("id", "file", "chat", "duration")}
    meta.update({"id": new, "file": out.name, "parent": clip_id, "parent_range": [start, end],
                 "source_id": f"{src.get('source_id', clip_id)}:{int(start)}", "title": title or src.get("title"),
                 **probe(out)})
    return save_clip_meta(meta)


# ---------------------------------------------------------------- pass 1: one segment

def _gameplay_fx(t_hits: list[float], shake: bool) -> str:
    if not shake or not t_hits:
        return "crop=iw:ih"
    xs = "+".join(f"14*sin(t*63)*between(t,{t:.2f},{t + 0.45:.2f})" for t in t_hits)
    ys = "+".join(f"10*cos(t*71)*between(t,{t:.2f},{t + 0.45:.2f})" for t in t_hits)
    return f"scale=iw*1.04:ih*1.04,crop=iw/1.04:ih/1.04:x='(iw-ow)/2+{xs}':y='(ih-oh)/2+{ys}'"


def _scene_cuts(src: Path, at: float, length: float, thresh: float = 0.3) -> list[float]:
    """Times (relative to `at`) of hard scene cuts inside [at, at+length]."""
    import re
    r = run([ffmpeg(), "-hide_banner", "-ss", f"{at:.2f}", "-t", f"{length:.2f}", "-i", src, "-an", "-vf",
             f"select='gt(scene,{thresh})',showinfo", "-f", "null", "-"], quiet=True, check=False)
    return [float(t) for t in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def _has_speech(media: Path, work: Path, idx: int) -> bool:
    try:
        return len(transcribe(media, work / f"words{idx}.json")) >= 4
    except Exception:
        return False


def render_segment(seg: dict, idx: int, work: Path, plan: dict, fmt: str) -> tuple[Path, dict]:
    meta = load_clip(seg["clip_id"])
    src = clip_dir(meta["id"]) / meta["file"]
    info = probe(src)
    start = float(seg.get("start", 0))
    end = float(seg.get("end", info["duration"]))
    end = min(end, info["duration"])
    dur = end - start
    cw, ch = (W, H) if fmt == "short" else (1920, 1080)

    # skip a scene cut right at the start (stray frames from the previous shot look broken and can
    # become the platform's auto-cover); same for a cut in the last second
    # never cut someone off mid-sentence when their speech is kept
    if seg.get("snap_speech", plan.get("subtitles", "original") == "original" and not (plan.get("voiceover") or {}).get("text")):
        from .transcribe import clip_transcript, snap_to_sentences
        start, end = snap_to_sentences(clip_transcript(meta["id"]), start, end)
        end = min(end, info["duration"])
        dur = end - start
    if seg.get("snap_cuts", True):
        cuts = _scene_cuts(src, start, min(1.2, dur / 3))
        if cuts:
            start += cuts[-1] + 0.04
        cuts = _scene_cuts(src, max(start, end - 1.2), min(1.2, dur / 3))
        if cuts:
            end = max(start + 3, end - 1.2 + cuts[0] - 0.04)
        dur = end - start

    # cut first (accurate seek) so all timings below are segment-relative
    cut = work / f"cut{idx}.mp4"
    run([ffmpeg(), "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", src,
         *_encoder(False), "-c:a", "aac", "-ar", "48000", "-ac", "2", cut])
    if not info["has_audio"]:
        run([ffmpeg(), "-y", "-v", "error", "-i", cut, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-shortest", "-c:v", "copy", "-c:a", "aac", work / f"cuta{idx}.mp4"])
        cut = work / f"cuta{idx}.mp4"

    layout = seg.get("layout", "auto")
    cam = seg.get("cam")
    if fmt == "short" and layout in ("auto", "split") and not cam:
        cam = detect_facecam(cut, work / f"facecam{idx}.png")
        if cam:
            print(f"  facecam found {cam} -> preview {work / f'facecam{idx}.png'}", flush=True)
    if fmt == "short":
        layout = "split" if (layout in ("auto", "split") and cam) else ("blur" if layout in ("auto", "split") else layout)

    fx = plan.get("effects", {"shake": True, "flash": True})
    hits = loud_moments(cut) if (fx.get("shake") or fx.get("flash")) else []
    gfx = _gameplay_fx(hits, fx.get("shake", True))
    flash = ""
    if fx.get("flash") and hits:
        en = "+".join(f"between(t,{t:.2f},{t + 0.07:.2f})" for t in hits[:4])
        flash = f",drawbox=x=0:y=0:w=iw:h=ih:color=white@0.35:t=fill:enable='{en}'"

    # cut the bottom of the gameplay off so the streamer's own burned-in captions don't show twice
    from .common import config as _cfg
    tb = float(seg.get("trim_bottom", plan.get("trim_bottom", _cfg().get("trim_bottom", 0.14)) if fmt == "short" else 0))
    tb = max(0.0, min(0.4, tb))
    eh = int(info["height"] * (1 - tb)) & ~1
    tbc = f"crop=iw:{eh}:0:0," if tb else ""

    # --- layout graph
    if fmt == "long":
        vgraph = (f"[0:v]fps={FPS},{gfx},scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
                  f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:black,setsar=1{flash}[v0]")
        sub_y, credit_xy = int(ch * 0.82), (40, ch - 60)
    elif layout == "split":
        camh = int(min(760, max(600, W * cam["h"] / cam["w"]))) & ~1
        gh = H - camh
        # crop the gameplay band away from the webcam overlay so it doesn't show twice
        k = gh / max(1, eh)
        sw = int(info["width"] * k)
        gx = (sw - W) // 2
        if cam["x"] + cam["w"] / 2 > info["width"] / 2:      # cam on the right
            gx = max(0, min(gx, int(cam["x"] * k) - W))
        else:                                                # cam on the left
            gx = min(sw - W, max(gx, int((cam["x"] + cam["w"]) * k)))
        vgraph = (f"[0:v]fps={FPS},split[a][b];"
                  f"[a]crop={cam['w']}:{cam['h']}:{cam['x']}:{cam['y']},scale={W}:{camh}:force_original_aspect_ratio=increase,crop={W}:{camh}[cam];"
                  f"[b]{tbc}{gfx},scale=-2:{gh},crop={W}:{gh}:{gx}:0{flash}[game];"
                  f"[cam][game]vstack,setsar=1[v0]")
        sub_y, credit_xy = camh + 170, (30, camh - 22)
    elif layout == "crop":
        vgraph = f"[0:v]fps={FPS},{tbc}{gfx},scale=-2:{H},crop={W}:{H},setsar=1{flash}[v0]"
        sub_y, credit_xy = int(H * 0.66), (40, 300)
    else:  # blur: zoomed gameplay band on a blurred copy of itself
        zoom = float(seg.get("zoom", 1.3))
        fw = int(W * zoom) & ~1
        fh = int(fw * eh / max(1, info["width"])) & ~1
        fh = min(fh, int(H * 0.62)) & ~1
        y0 = (H - fh) // 2 - 60
        vgraph = (f"[0:v]fps={FPS},split[a][b];"
                  f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=28,eq=brightness=-0.12[bg];"
                  f"[b]{tbc}{gfx},scale={fw}:-2,crop={W}:{fh}{flash}[fg];"
                  f"[bg][fg]overlay=0:{y0},setsar=1[v0]")
        sub_y, credit_xy = y0 + fh + 120, (40, y0 + fh + 10)

    # --- captions + credit for this segment
    font = _prep_fonts(work)
    ass = ASS(font, cw, ch, config()["subtitle_color"])
    if plan.get("subtitles", "original") == "original":
        words = transcribe(cut, work / f"words{idx}.json")
        ass.captions(words, sub_y)
    cr = meta["creator"]
    mode = plan.get("credit_mode", config()["credit_mode"])
    tag = credit_line(cr)
    if not meta.get("own_footage"):
        if mode in ("corner", "both"):
            ass.credit(tag, 0, dur, *credit_xy)
    ass_name = f"seg{idx}.ass"
    ass.write(work / ass_name)
    vgraph += f";[v0]subtitles={ass_name}:fontsdir=fonts[v]"

    out = work / f"seg{idx}.mp4"
    run([ffmpeg(), "-y", "-v", "error", "-i", cut.name, "-filter_complex", vgraph, "-map", "[v]", "-map", "0:a",
         *_encoder(False), "-r", str(FPS), "-c:a", "aac", "-ar", "48000", "-ac", "2", out.name], cwd=work)
    used = {"clip_id": meta["id"], "creator": cr, "credit": tag, "url": meta.get("url", ""),
            "own_footage": bool(meta.get("own_footage")), "layout": layout, "cam": cam, "seconds": round(dur, 2),
            "speech": _has_speech(cut, work, idx) if (plan.get("voiceover") or {}).get("text") else None}
    return out, used


# ---------------------------------------------------------------- pass 2: whole video

def _pick_music(choice) -> tuple[Path | None, str | None]:
    if not choice:
        return None, None
    credits = read_json(MUSIC / "credits.json", {})
    if choice == "auto":
        tracks = [p for p in MUSIC.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac")]
        if not tracks:
            print("  no music in music/ - skipping music", flush=True)
            return None, None
        p = random.choice(tracks)
    else:
        p = (ROOT / choice) if not Path(choice).is_absolute() else Path(choice)
    return p, credits.get(p.name, p.stem)


def _fix_loudness(video: Path, target: float = -14.0) -> None:
    """Single-pass loudnorm undershoots on short clips: measure, then apply exact gain + limiter (video copied)."""
    import re
    r = run([ffmpeg(), "-hide_banner", "-i", video, "-vn", "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
            quiet=True, check=False)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    if not m:
        return
    gain = target - float(m[-1])
    if abs(gain) < 1.0:
        return
    tmp = video.with_name("video_ln.mp4")
    run([ffmpeg(), "-y", "-v", "error", "-i", video, "-c:v", "copy", "-af",
         f"volume={gain:.2f}dB,alimiter=limit=0.89:level=false", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", tmp], quiet=True)
    tmp.replace(video)


def render(plan_path: Path) -> dict:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    fmt = plan.get("format", "short")
    cw, ch = (W, H) if fmt == "short" else (1920, 1080)
    post_dir = Path(plan_path).parent
    work = post_dir / "work"
    work.mkdir(exist_ok=True)

    segs, used = [], []
    for i, seg in enumerate(plan["segments"]):
        p, u = render_segment(seg, i, work, plan, fmt)
        segs.append(p)
        used.append(u)

    # concat segments
    if len(segs) == 1:
        joined = segs[0]
    else:
        lst = work / "concat.txt"
        lst.write_text("".join(f"file '{p.name}'\n" for p in segs), encoding="utf-8")
        joined = work / "joined.mp4"
        run([ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", joined.name], cwd=work)
    # spoiler card is PREPENDED (frozen, blurred first frame) so no gameplay is hidden under it
    spoiler_s = 2.6 if plan.get("spoiler", config()["spoiler_default"]) else 0.0
    max_s = float(plan.get("max_seconds", config()["max_seconds"] if fmt == "short" else 3600))
    content = min(probe(joined)["duration"], max_s - spoiler_s)
    total = content + spoiler_s

    font = _prep_fonts(work)
    ass = ASS(font, cw, ch, config()["subtitle_color"])
    if spoiler_s:
        ass.spoiler(spoiler_s, plan.get("countdown", True), plan.get("spoiler_text") or config()["spoiler_text"])
    mode = plan.get("credit_mode", config()["credit_mode"])
    first = next((u for u in used if not u["own_footage"]), None)
    if first and mode in ("intro", "both"):
        ass.credit(f"CLIP: {first['credit']}", spoiler_s + 0.1, min(total, spoiler_s + 3.2), cw // 2,
                   int(ch * 0.30) if fmt == "short" else int(ch * 0.85), big=True)
    if plan.get("hook"):
        ass.hook(plan["hook"], spoiler_s, min(total, spoiler_s + float(plan.get("hook_seconds", 4.5))),
                 230 if fmt == "short" else 60)

    inputs = ["-i", joined.name]
    vo_words, vo_file = None, None
    vo = plan.get("voiceover")
    if vo and vo.get("text"):
        vo_words, vo_file = voiceover(vo["text"], work / "voice.wav", vo.get("voice", "male"), vo.get("rate", "+0%"))
        inputs += ["-i", vo_file.name]
        if plan.get("subtitles", "original") != "none":
            ass.captions(vo_words, int(ch * 0.62) if fmt == "short" else int(ch * 0.82),
                         offset=float(vo.get("start", spoiler_s)), until=total)
    music, music_credit = _pick_music(plan.get("music", "auto" if fmt == "short" else None))
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
    # never talk over someone: with a voiceover, the original track is muted if anyone speaks in it
    # (plan "original_audio": auto | mute | duck | keep)
    oa = plan.get("original_audio", "auto")
    if vo_file and oa == "auto":
        oa = "mute" if any(u.get("speech") for u in used) else "duck"
    if vo_file and oa == "mute" and not music:
        # nothing left to hear after the voice ends -> end the video ~1 s after it instead of dead air
        vo_end = float(vo.get("start", spoiler_s)) + probe(vo_file)["duration"] + 1.0
        if vo_end < total:
            total = max(spoiler_s + 4.0, vo_end)
            content = total - spoiler_s
    ass.write(work / "final.ass")

    v = (f"[0:v]trim=0:{content:.2f},setpts=PTS-STARTPTS" +
         (f",tpad=start_duration={spoiler_s}:start_mode=clone,gblur=sigma=22:enable='lt(t,{spoiler_s})',"
          f"hue=s=0:enable='lt(t,{spoiler_s})',eq=brightness=-0.15:enable='lt(t,{spoiler_s})'" if spoiler_s else "") +
         ",subtitles=final.ass:fontsdir=fonts[v]")
    sd = int(spoiler_s * 1000)
    a = f"[0:a]atrim=0:{content:.2f},asetpts=PTS-STARTPTS" + (f",adelay={sd}|{sd}" if sd else "")
    a += {"mute": ",volume=0[orig]", "duck": ",volume=0.35[orig]"}.get(oa, "[orig]") if vo_file else "[orig]"
    graph = [v, a]
    speech = "[orig]"
    nxt = 1
    if vo_file:
        delay = int(float(vo.get("start", spoiler_s)) * 1000)
        graph.append(f"[{nxt}:a]adelay={delay}|{delay},volume={float(vo.get('volume', 0.9))},apad[vo]")
        graph.append(f"[orig][vo]amix=inputs=2:duration=first:normalize=0[speech]")
        speech = "[speech]"
        nxt += 1
    if music:
        mv = float(plan.get("music_volume", config()["music_volume"]))
        graph.append(f"{speech}asplit[sp1][sp2]")
        graph.append(f"[{nxt}:a]atrim=0:{total:.2f},volume={mv},afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5[mus]")
        graph.append("[mus][sp2]sidechaincompress=threshold=0.04:ratio=6:attack=15:release=350[musd]")
        graph.append("[sp1][musd]amix=inputs=2:duration=first:normalize=0[mixed]")
        speech = "[mixed]"
    graph.append(f"{speech}loudnorm=I=-14:TP=-1.5:LRA=11[a]")

    final = post_dir / "video.mp4"
    run([ffmpeg(), "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
         "-t", f"{total:.2f}", *_encoder(True), "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", "../video.mp4"], cwd=work)
    _fix_loudness(final)

    result = {"post_id": plan.get("post_id", post_dir.name), "video": str(final), "seconds": round(total, 2),
              "format": fmt, "sources": used, "music": music.name if music else None,
              "music_credit": music_credit, "voiceover": bool(vo_file),
              "has_spoiler_card": bool(spoiler_s)}
    write_json(post_dir / "render.json", result)
    return result
