"""ASS subtitle/graphics builder for a 1080x1920 (or 1920x1080) canvas.

All on-screen text (word-by-word captions, hook title, credit tag, game-style spoiler card) is drawn by libass,
so there's no fragile ffmpeg drawtext escaping.
"""
from __future__ import annotations

import re


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")


class ASS:
    def __init__(self, font: str, w: int = 1080, h: int = 1920, highlight: str = "&H0000FFFF"):
        self.font, self.w, self.h, self.hl = font, w, h, highlight
        self.events: list[str] = []

    def header(self) -> str:
        f = self.font
        s = self.w / 1080 if self.w < self.h else self.h / 1080   # scale styles for 16:9 canvas
        sz = lambda v: int(v * s)  # noqa: E731
        return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {self.w}
PlayResY: {self.h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{f},{sz(84)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,{sz(7)},{sz(3)},5,60,60,0,1
Style: Hook,{f},{sz(70)},&H00FFFFFF,&H00FFFFFF,&H00000000,&HC0000000,1,0,0,0,100,100,1,0,1,{sz(6)},{sz(4)},8,70,70,0,1
Style: Credit,{f},{sz(38)},&H20FFFFFF,&H00FFFFFF,&H00000000,&H90000000,1,0,0,0,100,100,1,0,1,{sz(3)},{sz(2)},1,40,40,0,1
Style: CreditBig,{f},{sz(56)},&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,1,0,0,0,100,100,1,0,3,{sz(14)},0,5,60,60,0,1
Style: Spoiler,{f},{sz(150)},&H001C1CD6,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,2,0,1,{sz(8)},{sz(6)},5,40,40,0,1
Style: SpoilerSub,{f},{sz(52)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,2,0,1,{sz(5)},{sz(3)},5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def add(self, start: float, end: float, style: str, text: str, layer: int = 0) -> None:
        self.events.append(f"Dialogue: {layer},{_ts(start)},{_ts(end)},{style},,0,0,0,,{text}")

    # ---------------------------------------------------------------- pieces

    def captions(self, words: list[dict], y: int, offset: float = 0.0, max_words: int = 3,
                 max_gap: float = 0.6, until: float | None = None) -> None:
        """Word-by-word captions: chunk of up to 3 words, current word highlighted + popped."""
        chunks: list[list[dict]] = []
        for w in words:
            if not chunks:
                chunks.append([w])
                continue
            last = chunks[-1]
            if (len(last) >= max_words or w["start"] - last[-1]["end"] > max_gap
                    or re.search(r"[.!?,]$", last[-1]["word"])):
                chunks.append([w])
            else:
                last.append(w)
        x = self.w // 2
        for ci, ch in enumerate(chunks):
            chunk_end = ch[-1]["end"] + 0.15
            if ci + 1 < len(chunks):
                chunk_end = min(chunk_end + 0.25, chunks[ci + 1][0]["start"])
            for wi, w in enumerate(ch):
                st = w["start"]
                en = ch[wi + 1]["start"] if wi + 1 < len(ch) else chunk_end
                st, en = st + offset, en + offset
                if until is not None:
                    if st >= until:
                        return
                    en = min(en, until)
                if en - st < 0.04:
                    continue
                parts = []
                for wj, ww in enumerate(ch):
                    word = _esc(re.sub(r"[.,]$", "", ww["word"]).upper())
                    if wj == wi:
                        parts.append(r"{\c" + self.hl + r"\fscx112\fscy112\t(0,90,\fscx100\fscy100)}" + word + r"{\c&H00FFFFFF&}")
                    else:
                        parts.append(word)
                self.add(st, en, "Sub", r"{\an5\pos(%d,%d)}" % (x, y) + " ".join(parts), layer=2)

    def hook(self, text: str, start: float, end: float, y: int) -> None:
        self.add(start, end, "Hook", r"{\an8\pos(%d,%d)\fad(0,200)}" % (self.w // 2, y) + _esc(text.upper()), layer=3)

    def credit(self, text: str, start: float, end: float, x: int, y: int, big: bool = False) -> None:
        if big:
            self.add(start, end, "CreditBig", r"{\an5\pos(%d,%d)\fad(200,250)}" % (x, y) + _esc(text), layer=4)
        else:
            self.add(start, end, "Credit", r"{\an1\pos(%d,%d)}" % (x, y) + _esc(text), layer=4)

    def spoiler(self, seconds: float = 2.6, countdown: bool = True, subtitle: str = "SPOILERS AHEAD") -> None:
        """'WASTED'-style card: big red text slams in over a grey, blurred frame."""
        cx, cy = self.w // 2, int(self.h * 0.45)
        big = r"{\an5\pos(%d,%d)\fscx160\fscy160\alpha&HFF&\t(0,220,\fscx100\fscy100\alpha&H00&)\fad(0,180)}" % (cx, cy)
        self.add(0, seconds, "Spoiler", big + "SPOILER\\NWARNING", layer=5)
        self.add(0.25, seconds, "SpoilerSub", r"{\an5\pos(%d,%d)\fad(150,180)}" % (cx, cy + int(self.h * 0.12)) + _esc(subtitle), layer=5)
        if countdown:
            n = 3
            step = (seconds - 0.3) / n
            for i in range(n):
                st = 0.3 + i * step
                self.add(st, st + step, "SpoilerSub",
                         r"{\an5\pos(%d,%d)\fs%d\fscx140\fscy140\t(0,150,\fscx100\fscy100)}" % (cx, cy + int(self.h * 0.20), int(90 * self.w / 1080)) + str(n - i),
                         layer=5)

    def write(self, path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.header())
            f.write("\n".join(self.events) + "\n")
