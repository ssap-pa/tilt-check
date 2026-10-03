"""Render docs/demo.gif from a captured run of `tiltcheck check` (no typing is faked:
the text is the program's real output, revealed in the order it prints)."""
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

src = Path(sys.argv[1]).read_text(encoding="utf-8").strip().splitlines()
src = [l for l in src if not l.startswith("Logged.")]
cmd = '$ python -m tiltcheck check "short 2 MNQ" --at "2026-10-02 13:05"'

W, H, PAD, LH = 1180, 760, 24, 22
font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 17)
bold = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", 17)
BG, FG, DIM, ACC, RED, GRN = (18, 18, 20), (225, 225, 225), (140, 140, 150), (125, 211, 252), (248, 113, 113), (134, 239, 172)


def wrap(lines):
    out = []
    for l in lines:
        out += textwrap.wrap(l, 108, subsequent_indent="  ") or [""]
    return out


def color(line):
    if line.startswith("$"):
        return ACC
    if "$-" in line or "47.1%" in line or "54.9%" in line:
        return RED
    if line.startswith("Model check") or "coin flip" in line or line.startswith("  Your history") or "for the log" in line:
        return DIM
    if line.startswith(("What your", "Closest", "Plan:")):
        return FG
    return FG


def frame(lines, cursor=False):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([8, 8, W - 8, 40], radius=8, fill=(36, 36, 40))
    for i, c in enumerate([(248, 113, 113), (250, 204, 21), (134, 239, 172)]):
        d.ellipse([22 + i * 22, 18, 34 + i * 22, 30], fill=c)
    d.text((110, 15), "tilt-check  (TabPFN + Gemma, running locally)", font=font, fill=DIM)
    y = 56
    for l in wrap(lines)[-31:]:
        d.text((PAD, y), l, font=bold if l.startswith(("$", "What your", "Closest")) else font, fill=color(l))
        y += LH
    if cursor:
        d.rectangle([PAD + 9 * len(wrap(lines)[-1]) + 4, y - LH + 3, PAD + 9 * len(wrap(lines)[-1]) + 13, y - 3], fill=FG)
    return im


frames, durs = [], []
for k in range(8, len(cmd) + 8, 8):          # the command appears in a few steps
    frames.append(frame([cmd[:k]], cursor=True)); durs.append(90)
durs[-1] = 700
blocks, cur = [], []
for l in src:
    cur.append(l)
    if l == "":
        blocks.append(cur); cur = []
blocks.append(cur)
shown = [cmd, ""]
for b in blocks:
    shown += b
    frames.append(frame(shown)); durs.append(1600)
shown += ["", "take / skip / wait?  skip", "why (one line)?  first trade of the day, lunch window"]
frames.append(frame(shown)); durs.append(5000)
out = Path(__file__).with_name("demo.gif")
frames[0].save(out, save_all=True, append_images=frames[1:], duration=durs, loop=0, optimize=True)
print(out, len(frames), "frames")
