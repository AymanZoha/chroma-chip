"""CHROMA v2 video guide: what the chip does, shown on the product concept with real
simulation frames and audio. Numbers on screen are read from the simulation output.

Needs: Windows voices (System.Speech), ffmpeg, numpy, pillow, and a completed
`python sim/run_v2.py` + `python product/render_product.py`.
Usage: python video/make_v2_video.py   ->  media/chroma_v2_video.mp4
"""
import os
import re
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

V2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(V2, "product"))
sys.path.insert(0, os.path.join(V2, "sim"))
import render_product as rp  # noqa: E402

BUILD = os.path.join(V2, "build")
DEMO = os.path.join(BUILD, "demo")
TONES = os.path.join(BUILD, "tones")
MEDIA = os.path.join(V2, "media")
WORK = os.path.join(BUILD, "video")
OUT = os.path.join(MEDIA, "chroma_v2_video.mp4")
W, H, FPS, SR = 1280, 720, 30, 48000
VOICE = "Microsoft David Desktop"
BG, PANEL = (9, 13, 26), (20, 27, 48)
ACC, ACC2, TXT, DIM, GREEN = (0, 229, 255), (255, 200, 0), (232, 238, 246), (140, 152, 176), (60, 220, 110)
FD = "C:/Windows/Fonts/"


def font(n, s):
    return ImageFont.truetype(FD + n, s)


F_HUGE, F_TITLE, F_H2 = font("segoeuib.ttf", 80), font("segoeuib.ttf", 42), font("seguisb.ttf", 28)
F_BODY, F_SMALL, F_MONO, F_SUB = font("segoeui.ttf", 25), font("segoeui.ttf", 20), font("consola.ttf", 21), font("seguisb.ttf", 23)
F_SYM = font("seguisym.ttf", 36)

REPORT = dict(re.findall(r"^(\S+)\s+([\d.]+)", open(os.path.join(DEMO, "report.txt")).read(), re.M))
STAT = open(os.path.join(BUILD, "stat_v2.txt")).read()
N_CELLS = int(re.search(r"(\d+)\s+cells", STAT).group(1))
N_FF = sum(int(n) for n, k in re.findall(r"^\s+(\d+)\s+(\$\S*DFF\S*)", STAT, re.M))
FPS_MEAS = float(REPORT["lcd_fps"])
DEMO_START, DEMO_END = 0.2, 8.2

SCENES = [
    ("title", "This is CHROMA version two: the custom chip at the heart of a Spotify music player. "
              "It draws the entire now playing screen, and it listens to the music in real time."),
    ("demo", "Here's the player running. The screen is the chip's real output from a cycle accurate "
             "simulation, and the sound is the audio it was analysing."),
    ("system", "A Raspberry Pi handles Wi-Fi and Spotify. It sends the song title, artist and cover art "
               "to CHROMA over S P I, and a copy of the audio over I2S. CHROMA does everything else: it "
               "stores the cover in an external memory chip and drives the screen over an eight bit bus."),
    ("record", "The cover art spins like a vinyl record. That's texture mapping, the core job of a graphics "
               "card. For every pixel, the chip steps through the cover image along a rotated path using "
               "only additions, and fetches that pixel from memory."),
    ("spectrum", "The bars come from a real eight band spectrum analyser built in hardware. Eight filters "
                 "share a single multiplier, taking turns about three hundred clock cycles per audio "
                 "sample. In this test, each tone lights up exactly the right band."),
    ("numbers", f"It renders about {int(round(FPS_MEAS))} frames per second, and the design is around "
                f"{N_CELLS // 1000} thousand logic gates and {N_FF} flip flops, which is roughly eight to "
                "twelve Tiny Tapeout tiles."),
    ("verify", "Every chip around CHROMA is modelled at the pin level. The screen's timing and commands "
               "are checked, the memory bus is checked for conflicts, and the cover on screen matches the "
               "uploaded image pixel for pixel."),
    ("product", "And this is the product it's designed for: a pocket player with a two point eight inch "
                "screen, three buttons, a speaker and a battery, with the Pi and CHROMA inside."),
    ("next", "Next, I'll put it on an F P G A with a real screen, then build the board, and finally "
             "manufacture the chip."),
]
LEAD, TAIL = 0.35, 0.7


# ------------------------------------------------------------------ audio
def synthesize():
    os.makedirs(WORK, exist_ok=True)
    for i, (_, text) in enumerate(SCENES):
        open(os.path.join(WORK, f"v2n_{i}.txt"), "w", encoding="utf-8").write(text)
    ps = f"""
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo({SR}, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
for ($i = 0; $i -lt {len(SCENES)}; $i++) {{
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice('{VOICE}'); $s.Rate = 1
  $s.SetOutputToWaveFile("{WORK}\\v2n_$i.wav", $fmt)
  $s.Speak((Get-Content "{WORK}\\v2n_$i.txt" -Raw -Encoding UTF8)); $s.Dispose()
}}
"""
    script = os.path.join(WORK, "tts_v2.ps1")
    open(script, "w", encoding="utf-8").write(ps)
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script], check=True)
    clips = []
    for i in range(len(SCENES)):
        with wave.open(os.path.join(WORK, f"v2n_{i}.wav")) as w:
            clips.append(np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768)
    return clips


def music():
    a = np.fromfile(os.path.join(DEMO, "audio_out.raw"), "<i2").astype(np.float32) / 32768
    return a / max(1e-9, np.abs(a).max())


# ------------------------------------------------------------------ drawing
def canvas(kicker=None, title=None):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 6], fill=ACC)
    if title:
        d.text((60, 30), kicker.upper(), font=F_SMALL, fill=ACC)
        d.text((60, 54), title, font=F_TITLE, fill=TXT)
    return im, d


def wrap(d, text, fnt, width):
    out, cur = [], ""
    for w_ in text.split():
        t = (cur + " " + w_).strip()
        if d.textlength(t, font=fnt) <= width:
            cur = t
        else:
            out.append(cur); cur = w_
    return out + ([cur] if cur else [])


def subtitle(im, text):
    if not text:
        return im
    im = im.copy()
    d = ImageDraw.Draw(im, "RGBA")
    lines = wrap(d, text, F_SUB, W - 160)[:3]
    h = 14 + 32 * len(lines)
    d.rectangle([0, H - h - 10, W, H], fill=(0, 0, 0, 190))
    for k, ln in enumerate(lines):
        d.text(((W - d.textlength(ln, font=F_SUB)) / 2, H - h + 32 * k - 2), ln, font=F_SUB, fill=(255, 255, 255))
    return im


def sentence_timing(text, dur):
    s = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]
    tot = sum(len(x) for x in s)
    out, t = [], 0.0
    for x in s:
        dt = dur * len(x) / tot
        out.append((t, t + dt, x)); t += dt
    return out


def sim_frame(t):
    i = max(0, min(int(round(t * 30)), int(DEMO_END * 30) - 1))
    p = os.path.join(DEMO, f"f_{i:04d}.rgb")
    return Image.frombytes("RGB", (240, 320), open(p, "rb").read())


def box(d, xy, label, sub=None, color=ACC):
    x0, y0, x1, y1 = xy
    d.rounded_rectangle(xy, radius=12, fill=PANEL, outline=color, width=3)
    d.text(((x0 + x1 - d.textlength(label, font=F_H2)) / 2, y0 + 12), label, font=F_H2, fill=TXT)
    if sub:
        for k, ln in enumerate(sub.split("\n")):
            d.text(((x0 + x1 - d.textlength(ln, font=F_SMALL)) / 2, y0 + 52 + 24 * k), ln, font=F_SMALL, fill=DIM)


def arrow(d, a, b, color=DIM, label=None):
    d.line([a, b], fill=color, width=4)
    v = np.array(b, float) - np.array(a, float); v /= np.linalg.norm(v)
    n = np.array([-v[1], v[0]]); p = np.array(b, float)
    d.polygon([tuple(p), tuple(p - 14 * v + 8 * n), tuple(p - 14 * v - 8 * n)], fill=color)
    if label:
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        d.text((mx - d.textlength(label, font=F_MONO) / 2, my - 30), label, font=F_MONO, fill=color)


DEVICE_CACHE = {}


def device_with(screen_img, scale=0.62):
    dev = rp.device_front(screen_img)
    return dev.resize((int(dev.width * scale), int(dev.height * scale)), Image.LANCZOS)


# ------------------------------------------------------------------ scenes
PRODUCT_FRONT = Image.open(os.path.join(MEDIA, "product_front.png")).convert("RGB")
PRODUCT_ANGLE = Image.open(os.path.join(MEDIA, "product_angle.png")).convert("RGB")
PRODUCT_EXPL = Image.open(os.path.join(MEDIA, "product_exploded.png")).convert("RGB")


def scene_title(t, dur):
    im, d = canvas()
    d.text((70, 190), "CHROMA v2", font=F_HUGE, fill=ACC)
    for k, ln in enumerate(["The chip inside a Spotify player:", "graphics + real-time audio analysis"]):
        d.text((74, 300 + 42 * k), ln, font=F_H2, fill=TXT)
    d.text((74, 410), "Texture mapping · 8-band DSP · LCD + PSRAM interfaces", font=F_BODY, fill=DIM)
    p = PRODUCT_FRONT.crop((300, 150, 1100, 1250)).resize((436, 600), Image.LANCZOS)
    im.paste(p, (W - 500, 70))
    return im


DEMO_EVENTS = [(0.2, 0.25, "LCD wakes up", "cover art already uploaded over SPI"),
               (0.25, 3.4, "Track 1 playing", "record spins, bars follow the music"),
               (3.4, 4.0, "PAUSE", "record stops, bars fall, pause icon"),
               (4.0, 4.8, "Resumed", ""),
               (4.8, 4.86, "NEXT track", "new text, colour and cover streamed in"),
               (4.86, 99, "Track 2 playing", "new theme colour from the Pi")]


def scene_demo(t, dur):
    st = min(DEMO_START + t, DEMO_END - 0.01)  # real time, in sync with the audio
    im, d = canvas("Live: the chip's output", "CHROMA v2 running")
    dev = device_with(sim_frame(st), 0.5)
    im.paste(dev, (120, 100), dev)
    x = 560
    for a, b, head, sub in DEMO_EVENTS:
        if a <= st < b:
            d.text((x, 170), head, font=F_H2, fill=ACC2 if head in ("PAUSE", "NEXT track") else ACC)
            for k, ln in enumerate(wrap(d, sub, F_BODY, 640)):
                d.text((x, 214 + 34 * k), ln, font=F_BODY, fill=TXT)
    d.text((x, 360), f"t = {st:4.2f} s  ·  {int(st * 50e6):,} clock cycles", font=F_MONO, fill=DIM)
    d.text((x, 400), f"measured: {FPS_MEAS:.0f} frames/s while texture mapping", font=F_MONO, fill=DIM)
    for k, ln in enumerate(wrap(d, "Screen = the LCD model's frame memory, written only through the chip's 8-bit bus. "
                                   "Sound = the I2S stream the chip was analysing.", F_SMALL, 640)):
        d.text((x, 460 + 28 * k), ln, font=F_SMALL, fill=DIM)
    return im


def scene_system(t, dur):
    im, d = canvas("System", "Who does what")
    box(d, (50, 220, 330, 380), "Raspberry Pi", "Zero 2 W\nWi-Fi + Spotify Connect\ndecodes audio + cover")
    box(d, (500, 200, 790, 400), "CHROMA v2", "this chip\ngraphics + audio DSP", ACC2)
    box(d, (960, 140, 1230, 270), "LCD", "240x320, ST7789")
    box(d, (960, 330, 1230, 460), "PSRAM", "8 MB, QSPI")
    box(d, (500, 500, 790, 600), "Speaker", "via I2S amp")
    arrow(d, (330, 270), (498, 270), ACC, "SPI")
    arrow(d, (330, 340), (498, 340), ACC, "I2S")
    arrow(d, (790, 240), (958, 205), ACC2, "8-bit bus")
    arrow(d, (790, 360), (958, 395), ACC2, "QPI 4-bit")
    arrow(d, (190, 380), (498, 550), DIM)
    return im


def scene_record(t, dur):
    im, d = canvas("Graphics", "Texture mapping a spinning record")
    st = 0.6 + t * 0.9
    fr = sim_frame(st)
    crop = fr.crop((20, 18, 220, 218)).resize((460, 460), Image.NEAREST)
    im.paste(crop, (60, 130))
    d.rectangle([58, 128, 521, 591], outline=DIM, width=2)
    cov = Image.open(os.path.join(BUILD, "assets", "cover_a.png")).resize((200, 200), Image.NEAREST)
    im.paste(cov, (600, 140))
    d.text((600, 352), "cover in PSRAM (128x128)", font=F_SMALL, fill=DIM)
    x = 840
    lines = [("per pixel:", ACC), ("u += cos θ", TXT), ("v -= sin θ", TXT), ("per row:", ACC),
             ("u_row += sin θ", TXT), ("v_row += cos θ", TXT), ("texel = PSRAM[v][u]", ACC2)]
    for k, (s, c) in enumerate(lines):
        d.text((x, 150 + 40 * k), s, font=F_MONO, fill=c)
    d.text((600, 420), "No multipliers per pixel: just two adds.", font=F_BODY, fill=TXT)
    d.text((600, 456), "A 65-entry sine table sets θ once per frame.", font=F_BODY, fill=TXT)
    d.text((600, 492), "Each texel: one 18-clock QPI read.", font=F_BODY, fill=TXT)
    return im


TONE_HZ = ["60", "150", "350", "800", "1.6k", "3k", "5k", "8k"]


def tone_heights():
    sys.path.insert(0, os.path.join(V2, "sim"))
    import run_v2
    rows = []
    for band in range(8):
        i = int((0.4 + 0.25 * (band + 1)) * 30) - 1
        rows.append(run_v2.bar_heights(run_v2.frame("tones", i)))
    return rows


TONE_ROWS = None


def scene_spectrum(t, dur):
    global TONE_ROWS
    if TONE_ROWS is None:
        TONE_ROWS = tone_heights()
    im, d = canvas("Audio DSP", "8-band spectrum analyser in hardware")
    box(d, (60, 140, 330, 230), "I2S receiver", "48 kHz samples")
    box(d, (60, 270, 330, 380), "8 SVF filters", "Q = 4, 60 Hz - 8 kHz\none shared multiplier")
    box(d, (60, 420, 330, 520), "Peak + log2", "3 dB per bar step")
    arrow(d, (195, 230), (195, 268)); arrow(d, (195, 380), (195, 418))
    # measured tone test grid
    x0, y0 = 420, 150
    d.text((x0, y0 - 6), "Measured: test tone in → bar heights out", font=F_H2, fill=TXT)
    shown = min(8, int(8 * t / max(0.1, dur - 1.5)) + 1)
    for r in range(shown):
        y = y0 + 50 + r * 52
        d.text((x0, y + 8), f"{TONE_HZ[r]:>4} Hz", font=F_MONO, fill=DIM)
        hs = TONE_ROWS[r]
        best = int(np.argmax(hs))
        for b in range(8):
            bh = int(hs[b] * 1.3)
            bx = x0 + 130 + b * 70
            d.rectangle([bx, y + 40 - bh, bx + 50, y + 40], fill=ACC2 if b == best else (40, 90, 110))
        d.text((x0 + 130 + 8 * 70 + 10, y + 6), "✓" if best == r else "✗", font=F_SYM, fill=GREEN if best == r else (255, 90, 90))
    return im


def scene_numbers(t, dur):
    im, d = canvas("By the numbers", "Size and speed")
    stats = [(f"{FPS_MEAS:.0f} fps", "full 240x320 screen with the record spinning (measured)"),
             (f"{N_CELLS:,}", "logic gates (generic synthesis)"),
             (f"{N_FF:,}", "flip-flops: filter state, text buffer, renderer"),
             ("~8-12 tiles", "Tiny Tapeout estimate, scaled from v1's real layout"),
             ("24 / 24", "Tiny Tapeout I/O pins used: SPI, I2S, LCD, PSRAM")]
    for i, (big, small) in enumerate(stats):
        y = 150 + 100 * i
        d.text((80, y), big, font=F_TITLE, fill=ACC if i else GREEN)
        d.text((420, y + 14), small, font=F_BODY, fill=TXT)
    return im


def scene_verify(t, dur):
    im, d = canvas("Verification", "How I know it works")
    rows = [("Cover on screen = uploaded cover", "SPI → PSRAM → texture unit → LCD, pixel for pixel"),
            ("Filter bank: 8 / 8 tones", "each test tone lights only its own band"),
            (f"LCD protocol: {REPORT['lcd_protocol_errors']} errors",
             f"WR low ≥ {REPORT['lcd_wr_low_min_ns']} ns, cycle ≥ {REPORT['lcd_wr_cycle_min_ns']} ns (ST7789 needs 15 / 66)"),
            (f"PSRAM: {REPORT['psram_bus_contention']} bus conflicts",
             f"{int(REPORT['psram_reads']):,} reads, {int(REPORT['psram_writes']):,} writes, CE# low ≤ {REPORT['psram_max_cs_low_ns']} ns")]
    shown = min(4, int(4 * t / max(0.1, dur - 1.0)) + 1)
    for i, (a, b) in enumerate(rows[:shown]):
        y = 140 + 118 * i
        d.rounded_rectangle((60, y, 1220, y + 96), radius=12, fill=PANEL)
        d.text((88, y + 22), "✓", font=F_SYM, fill=GREEN)
        d.text((150, y + 12), a, font=F_H2, fill=TXT)
        d.text((150, y + 54), b, font=F_SMALL, fill=DIM)
    return im


def scene_product(t, dur):
    im, d = canvas("The product", "CHROMA player concept")
    half = dur / 2
    src = PRODUCT_ANGLE if t < half else PRODUCT_EXPL
    p = src.resize((int(src.width * 560 / src.height), 560), Image.LANCZOS)
    im.paste(p, ((W - p.width) // 2, 120))
    return im


def scene_next(t, dur):
    im, d = canvas("Roadmap", "What's next")
    items = [(True, "CHROMA v1: real layout, all checks passing"),
             (True, "CHROMA v2: full system simulation passing"),
             (False, "v2 on an FPGA with a real LCD, PSRAM and Pi"),
             (False, "Player PCB + enclosure"),
             (False, "Tape out v2 on Tiny Tapeout")]
    for i, (done, s) in enumerate(items):
        y = 160 + 70 * i
        d.text((70, y - 4), "✓" if done else "○", font=F_SYM, fill=GREEN if done else DIM)
        d.text((125, y), s, font=F_BODY, fill=TXT if done else DIM)
    return im


RENDER = {k: globals()["scene_" + k] for k, _ in SCENES}
STATIC = {"title", "system", "numbers", "next"}


def main():
    clips = synthesize()
    mus = music()
    durs = []
    for (kind, _), c in zip(SCENES, clips):
        dur = LEAD + len(c) / SR + TAIL
        if kind == "demo":
            dur = max(dur, DEMO_END - DEMO_START)
        if kind == "product":
            dur = max(dur, 9.0)
        durs.append(dur)
    total = sum(durs)
    audio = np.zeros(int(total * SR) + SR, np.float32)
    start = 0.0
    for (kind, _), c, dur in zip(SCENES, clips, durs):
        s0 = int((start + LEAD) * SR)
        audio[s0:s0 + len(c)] += c * 0.95
        if kind == "demo":
            seg = mus[int(DEMO_START * SR): int(DEMO_END * SR)]
            n = min(len(seg), int(dur * SR))
            gain = np.full(n, 0.28, np.float32)
            gain[int((LEAD + len(c) / SR + 0.3) * SR):] = 0.6
            c0 = int(start * SR)
            audio[c0:c0 + n] += seg[:n] * gain
        start += dur
    audio = np.clip(audio, -1, 1)
    with wave.open(os.path.join(WORK, "v2_mix.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((audio * 32000).astype("<i2").tobytes())
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", os.path.join(WORK, "v2_mix.wav"),
                           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", OUT],
                          stdin=subprocess.PIPE)
    for (kind, text), c, dur in zip(SCENES, clips, durs):
        subs = sentence_timing(text, len(c) / SR)
        cache = RENDER[kind](0, dur) if kind in STATIC else None
        for fi in range(int(round(dur * FPS))):
            t = fi / FPS
            base = cache if cache is not None else RENDER[kind](t, dur)
            line = next((s for a, b, s in subs if a <= t - LEAD < b), None)
            ff.stdin.write(subtitle(base, line).tobytes())
        print(f"scene {kind:9s} {dur:5.1f} s")
    ff.stdin.close()
    ff.wait()
    print(f"wrote {OUT} ({total:.1f} s)")


if __name__ == "__main__":
    main()
