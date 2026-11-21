"""Render the CHROMA video guide: narrated slides + real simulation footage + chip audio.

Needs: Windows (System.Speech voices), ffmpeg on PATH, numpy, pillow, and a prior
`python sim/run_sim.py` run (uses build/sim_out frames and audio).
Usage: python video/make_video.py   ->  media/chroma_video_guide.mp4
"""
import glob
import os
import re
import subprocess
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(ROOT, "build", "sim_out")
MEDIA = os.path.join(ROOT, "media")
WORK = os.path.join(ROOT, "build", "video")
OUT = os.path.join(MEDIA, "chroma_video_guide.mp4")
W, H, FPS, SR = 1280, 720, 30, 48000
VOICE = "Microsoft David Desktop"

BG, PANEL = (9, 13, 26), (20, 27, 48)
ACC, ACC2, TXT, DIM = (0, 229, 255), (255, 200, 0), (232, 238, 246), (140, 152, 176)
GREEN = (60, 220, 110)
FD = "C:/Windows/Fonts/"


def font(name, size):
    return ImageFont.truetype(FD + name, size)


F_HUGE, F_TITLE, F_H2 = font("segoeuib.ttf", 96), font("segoeuib.ttf", 44), font("seguisb.ttf", 30)
F_BODY, F_SMALL, F_MONO = font("segoeui.ttf", 26), font("segoeui.ttf", 21), font("consola.ttf", 22)
F_SUB = font("seguisb.ttf", 24)
F_SYM = font("seguisym.ttf", 40)

# --------------------------------------------------------------------------- script
SCENES = [
    ("title", "This is CHROMA, a custom chip I designed. It's the brain of a handheld music "
              "player: one chip that plays the music and draws the player's screen, with no "
              "processor and no frame buffer."),
    ("demo", "Here it is running. Everything you see and hear comes straight from the chip's pins."),
    ("anatomy", "The screen has a title with a light sweep, a sixteen band visualizer whose peak "
                "follows the pitch of each note and shrinks as the note fades, a progress bar, "
                "play, pause and next icons, and dots showing which of the four tracks is playing."),
    ("beam", "A full frame buffer would need almost two million bits of memory, far bigger than "
             "the whole chip. So CHROMA races the beam. Twenty five million times a second, it "
             "works out the color of the one pixel being drawn right now, from its x and y "
             "position and a few bytes of state."),
    ("audio", "Once per frame, a sequencer steps through a thirty two note melody stored on the "
              "chip. Each note sets the pitch of a square wave oscillator and restarts a volume "
              "envelope, and a fast pulse width modulator turns that into sound on a single pin. "
              "The four tracks play the melody normally, an octave up, backwards, or both, each "
              "with its own color theme."),
    ("pins", "Two inputs control it: pause, and next track. Eight outputs drive a VGA monitor, "
             "and one more drives a speaker. It runs from a twenty five megahertz clock."),
    ("layout", "This is the real physical layout, built with the open source LibreLane flow on "
               "SkyWater's one hundred thirty nanometer process. About a thousand standard cells, "
               "including ninety four flip flops, in an area smaller than a quarter of a "
               "millimeter on each side. It passes every manufacturing design rule check."),
    ("verify", "It's verified at three levels: a cycle accurate simulation that checks two hundred "
               "fifty million clock cycles of video timing and audio pitch, automated tests on "
               "the design, and the same tests rerun on the final gate level netlist."),
    ("next", "Next, I'll run it on an F P G A with a real monitor and speaker, build the handheld "
             "around it, and send it to be manufactured on a Tiny Tapeout shuttle. The whole "
             "design is open on GitHub."),
]
DEMO_SECONDS = 10.0
LEAD, TAIL = 0.35, 0.75


# --------------------------------------------------------------------------- voice
def synthesize():
    os.makedirs(WORK, exist_ok=True)
    for i, (_, text) in enumerate(SCENES):
        open(os.path.join(WORK, f"narr_{i}.txt"), "w", encoding="utf-8").write(text)
    ps = f"""
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo({SR}, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
for ($i = 0; $i -lt {len(SCENES)}; $i++) {{
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice('{VOICE}')
  $s.Rate = 1
  $s.SetOutputToWaveFile("{WORK}\\narr_$i.wav", $fmt)
  $s.Speak((Get-Content "{WORK}\\narr_$i.txt" -Raw -Encoding UTF8))
  $s.Dispose()
}}
"""
    script = os.path.join(WORK, "tts.ps1")
    open(script, "w", encoding="utf-8").write(ps)
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script], check=True)
    clips = []
    for i in range(len(SCENES)):
        with wave.open(os.path.join(WORK, f"narr_{i}.wav")) as w:
            assert w.getframerate() == SR
            clips.append(np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768)
    return clips


def chip_audio():
    a = np.fromfile(os.path.join(SIM, "audio.raw"), dtype="<f4").astype(np.float32)
    a -= a.mean()
    return a / max(1e-9, np.abs(a).max())


# --------------------------------------------------------------------------- drawing helpers
def canvas(kicker=None, title=None):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    if title:
        d.rectangle([0, 0, W, 6], fill=ACC)
        if kicker:
            d.text((60, 34), kicker.upper(), font=F_SMALL, fill=ACC)
        d.text((60, 58), title, font=F_TITLE, fill=TXT)
    return im, d


def wrap(d, text, fnt, width):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if d.textlength(t, font=fnt) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w_
    return lines + ([cur] if cur else [])


def subtitle(im, text):
    if not text:
        return im
    im = im.copy()
    d = ImageDraw.Draw(im, "RGBA")
    lines = wrap(d, text, F_SUB, W - 160)[:3]
    h = 16 + 34 * len(lines)
    d.rectangle([0, H - h - 10, W, H], fill=(0, 0, 0, 185))
    for k, ln in enumerate(lines):
        tw = d.textlength(ln, font=F_SUB)
        d.text(((W - tw) / 2, H - h + 34 * k), ln, font=F_SUB, fill=(255, 255, 255))
    return im


def sentence_timing(text, dur):
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    total = sum(len(s) for s in sents)
    out, t = [], 0.0
    for s in sents:
        dt = dur * len(s) / total
        out.append((t, t + dt, s))
        t += dt
    return out


def box(d, xy, label, sub=None, color=ACC):
    x0, y0, x1, y1 = xy
    d.rounded_rectangle(xy, radius=12, fill=PANEL, outline=color, width=3)
    tw = d.textlength(label, font=F_H2)
    d.text(((x0 + x1 - tw) / 2, y0 + 14), label, font=F_H2, fill=TXT)
    if sub:
        sw = d.textlength(sub, font=F_SMALL)
        d.text(((x0 + x1 - sw) / 2, y0 + 56), sub, font=F_SMALL, fill=DIM)


def arrow(d, a, b, color=DIM):
    d.line([a, b], fill=color, width=4)
    v = np.array(b, float) - np.array(a, float)
    v /= np.linalg.norm(v)
    n = np.array([-v[1], v[0]])
    p = np.array(b, float)
    d.polygon([tuple(p), tuple(p - 14 * v + 8 * n), tuple(p - 14 * v - 8 * n)], fill=color)


SIM_FILES = sorted(glob.glob(os.path.join(SIM, "frame_*.rgb")))
SIM_NUMS = [int(os.path.basename(f)[6:10]) for f in SIM_FILES]
LAYOUT = Image.open(os.path.join(MEDIA, "chroma_layout.png")).convert("RGB")


def sim_at(seconds):
    """The captured frame closest to simulated time `seconds` (60 frames per second)."""
    target = seconds * 60
    k = int(np.argmin([abs(n - target) for n in SIM_NUMS]))
    return Image.frombytes("RGB", (640, 480), open(SIM_FILES[k], "rb").read())


# --------------------------------------------------------------------------- scenes
def scene_title(t, dur):
    im, d = canvas()
    d.rectangle([0, 0, W, 6], fill=ACC)
    d.text((70, 170), "CHROMA", font=F_HUGE, fill=ACC)
    for k, ln in enumerate(["A custom chip that plays music", "and draws its own user interface"]):
        d.text((74, 300 + 44 * k), ln, font=F_H2, fill=TXT)
    d.text((74, 420), "Verilog  ·  Tiny Tapeout  ·  SkyWater 130 nm", font=F_BODY, fill=DIM)
    d.text((74, 460), "Video guide", font=F_SMALL, fill=DIM)
    lay = LAYOUT.resize((int(LAYOUT.width * 600 / LAYOUT.height), 600), Image.LANCZOS)
    x = W - lay.width - 90
    im.paste(lay, (x, 60))
    d.rectangle([x - 2, 58, x + lay.width + 1, 661], outline=ACC, width=2)
    return im


def scene_demo(t, dur):
    im, d = canvas("Live from the chip's pins", "CHROMA running")
    st = min(t, DEMO_SECONDS - 0.01)
    fr = sim_at(st).resize((800, 600), Image.NEAREST)
    im.paste(fr, (60, 112))
    d.rectangle([58, 110, 861, 713], outline=DIM, width=2)
    x = 900
    events = [(0.0, 5.0, "Track 1 · neon theme", "Playing the built-in melody"),
              (5.0, 7.5, "NEXT pressed", "Track 2: octave up, sunset theme"),
              (7.5, 8.5, "PAUSE held", "Note rings out, bars drain"),
              (8.5, 99, "Resumed", "Sequencer continues")]
    for a, b, head, sub in events:
        if a <= st < b:
            d.text((x, 150), head, font=F_H2, fill=ACC2 if head != "Track 1 · neon theme" else ACC)
            for k, ln in enumerate(wrap(d, sub, F_BODY, 330)):
                d.text((x, 196 + 34 * k), ln, font=F_BODY, fill=TXT)
    d.text((x, 470), f"t = {st:4.1f} s", font=F_MONO, fill=DIM)
    d.text((x, 500), f"{int(st * 25_175_000):,} clock cycles", font=F_MONO, fill=DIM)
    for k, ln in enumerate(wrap(d, "Picture rebuilt from hsync, vsync and RGB pins. Sound from the 1-bit audio pin.",
                                F_SMALL, 330)):
        d.text((x, 560 + 28 * k), ln, font=F_SMALL, fill=DIM)
    return im


ANATOMY = [("Title + light sweep", 38, "5x7 font ROM, scaled 4x"),
           ("16-band visualizer", 208, "peak follows the note's pitch"),
           ("Progress bar", 396, "playhead moves every frame"),
           ("Controls + track dots", 440, "play/pause, next, 4 tracks")]


def scene_anatomy(t, dur):
    im, d = canvas("What's on screen", "One chip draws all of this")
    s = 1.04
    scr = sim_at(2.75).resize((int(640 * s), int(480 * s)), Image.NEAREST)
    ox, oy = 60, 118
    im.paste(scr, (ox, oy))
    shown = int(len(ANATOMY) * min(1, t / max(0.1, dur - 1.0)) + 1)
    for k, (label, y, sub) in enumerate(ANATOMY[:shown]):
        py = oy + y * s
        lx = 880
        ly = 150 + 120 * k
        d.ellipse([ox + 640 * s - 30 - 6, py - 6, ox + 640 * s - 30 + 6, py + 6], fill=ACC2)
        d.line([(ox + 640 * s - 30, py), (lx - 12, ly + 18)], fill=ACC2, width=2)
        d.text((lx, ly), label, font=F_H2, fill=TXT)
        d.text((lx, ly + 40), sub, font=F_SMALL, fill=DIM)
    return im


def scene_beam(t, dur):
    im, d = canvas("How it draws", "Racing the beam")
    mini = sim_at(2.75).resize((480, 360), Image.NEAREST)
    frac = (t / 3.0) % 1.0
    row = int(frac * 360)
    reveal = Image.new("RGB", (480, 360), (0, 0, 0))
    reveal.paste(mini.crop((0, 0, 480, row)), (0, 0))
    col = int((t * 2.6 % 1.0) * 480)
    reveal.paste(mini.crop((0, row, col, min(360, row + 3))), (0, row))
    im.paste(reveal, (60, 140))
    d.rectangle([58, 138, 541, 501], outline=DIM, width=2)
    d.ellipse([60 + col - 7, 140 + row - 7, 60 + col + 7, 140 + row + 7], fill=(255, 255, 255))
    d.text((60, 516), "the beam scans 640 x 480 pixels, 60 times a second", font=F_SMALL, fill=DIM)
    x = 600
    d.text((x, 150), "Frame buffer", font=F_H2, fill=(255, 110, 110))
    d.text((x, 192), "640 x 480 x 6 bits = 1.8 Mbit of memory", font=F_BODY, fill=TXT)
    d.text((x, 228), "far bigger than the whole chip", font=F_SMALL, fill=DIM)
    d.text((x, 300), "CHROMA", font=F_H2, fill=GREEN)
    d.text((x, 342), "color = f(x, y, state)", font=F_MONO, fill=ACC)
    d.text((x, 376), "computed fresh every 40 ns (25.175 MHz)", font=F_BODY, fill=TXT)
    d.text((x, 412), "state: song step, track, envelope, note", font=F_SMALL, fill=DIM)
    d.text((x, 470), "Layers, front to back:", font=F_SMALL, fill=DIM)
    d.text((x, 500), "title > bars > playhead > progress > icons > dithered background",
           font=F_SMALL, fill=TXT)
    return im


def scene_audio(t, dur):
    im, d = canvas("How it plays", "The audio engine")
    box(d, (60, 160, 330, 260), "Melody ROM", "32 notes on-chip")
    box(d, (60, 330, 330, 430), "Sequencer", "1 step / 8 frames")
    box(d, (470, 160, 760, 260), "Oscillator", "12-note period table")
    box(d, (470, 330, 760, 430), "Envelope", "4-bit decay per frame")
    box(d, (900, 245, 1200, 345), "PWM volume", "~1.5 MHz, 1-bit out", ACC2)
    arrow(d, (195, 330), (195, 262))
    arrow(d, (330, 210), (468, 210))
    arrow(d, (330, 380), (468, 380))
    arrow(d, (760, 210), (898, 280))
    arrow(d, (760, 380), (898, 315))
    arrow(d, (1050, 345), (1050, 420), ACC2)
    d.text((990, 425), "pin uio[7]", font=F_MONO, fill=ACC2)
    # decaying square wave
    x0, y0 = 60, 560
    pts = []
    for px in range(0, 1140):
        env = np.exp(-((px % 285) / 140.0))
        v = 1 if (px // 12) % 2 == 0 else -1
        pts.append((x0 + px, y0 - v * env * 42))
    d.line(pts, fill=ACC, width=2)
    d.text((60, 490), "square wave x envelope, retriggered on every note", font=F_SMALL, fill=DIM)
    return im


def scene_pins(t, dur):
    im, d = canvas("Interface", "Pins")
    d.rounded_rectangle((470, 150, 810, 560), radius=16, fill=PANEL, outline=ACC, width=3)
    d.text((560, 330), "CHROMA", font=F_H2, fill=ACC)
    left = [("ui[0]", "PAUSE"), ("ui[1]", "NEXT"), ("clk", "25.175 MHz"), ("rst_n", "reset")]
    for k, (p, n) in enumerate(left):
        y = 200 + 90 * k
        d.line([(380, y), (470, y)], fill=DIM, width=3)
        d.text((480, y - 14), p, font=F_MONO, fill=TXT)
        tw = d.textlength(n, font=F_BODY)
        d.text((360 - tw, y - 18), n, font=F_BODY, fill=TXT)
    right = [("uo[7:0]", "VGA: R G B + HSYNC VSYNC"), ("uio[7]", "AUDIO (to speaker)")]
    for k, (p, n) in enumerate(right):
        y = 260 + 140 * k
        d.line([(810, y), (900, y)], fill=ACC2 if k else ACC, width=3)
        tw = d.textlength(p, font=F_MONO)
        d.text((800 - tw, y - 14), p, font=F_MONO, fill=TXT)
        for j, ln in enumerate(wrap(d, n, F_BODY, 320)):
            d.text((915, y - 18 + 32 * j), ln, font=F_BODY, fill=TXT)
    d.text((60, 600), "Plugs into standard Tiny Tapeout VGA and audio PMOD boards.", font=F_SMALL, fill=DIM)
    return im


def scene_layout(t, dur):
    im, d = canvas("Physical design", "The real chip layout")
    # slow zoom from the whole die into the logic-dense top region
    k = min(1.0, t / max(0.1, dur))
    k = k * k * (3 - 2 * k)
    full_h = LAYOUT.height
    ch = int(full_h * (1 - 0.55 * k))
    cw = int(ch * 540 / 600)
    cw = min(cw, LAYOUT.width)
    cx = LAYOUT.width // 2
    cy = int(full_h / 2 - (full_h / 2 - full_h * 0.32) * k)
    box_ = (max(0, cx - cw // 2), max(0, cy - ch // 2))
    crop = LAYOUT.crop((box_[0], box_[1], box_[0] + cw, box_[1] + ch)).resize((540, 600), Image.LANCZOS)
    im.paste(crop, (W - 600, 100))
    d.rectangle([W - 602, 98, W - 59, 701], outline=ACC, width=2)
    stats = [("1,022", "standard cells"), ("94", "flip-flops"), ("31%", "placement utilisation"),
             ("24.3 mm", "of routed wire"), ("~160 x 225 µm", "2 Tiny Tapeout tiles"),
             ("16 / 16", "manufacturing checks pass")]
    for i, (big, small) in enumerate(stats):
        y = 140 + 82 * i
        d.text((60, y), big, font=F_H2, fill=GREEN if i == 5 else ACC)
        d.text((60, y + 38), small, font=F_SMALL, fill=DIM)
    d.text((340, 160), "LibreLane flow", font=F_BODY, fill=TXT)
    d.text((340, 194), "SkyWater sky130", font=F_BODY, fill=TXT)
    return im


def scene_verify(t, dur):
    im, d = canvas("Verification", "How I know it works")
    rows = [("Cycle-accurate simulation", "252 M clock cycles · VGA timing: 0 errors · note pitch within 1%"),
            ("RTL tests (cocotb)", "sync timing, blanking, pin directions"),
            ("Gate-level tests", "same tests on the post-layout netlist"),
            ("Tiny Tapeout precheck", "DRC, FEOL/BEOL, pins, power, boundary")]
    shown = int(len(rows) * min(1, t / max(0.1, dur - 1.0)) + 1)
    for i, (a, b) in enumerate(rows[:shown]):
        y = 160 + 115 * i
        d.rounded_rectangle((60, y, 1220, y + 92), radius=12, fill=PANEL)
        d.text((90, y + 18), "✓", font=F_SYM, fill=GREEN)
        d.text((160, y + 12), a, font=F_H2, fill=TXT)
        d.text((160, y + 52), b, font=F_SMALL, fill=DIM)
    return im


def scene_next(t, dur):
    im, d = canvas("Roadmap", "What's next")
    items = [(True, "Design + verify the chip in Verilog"),
             (True, "Real layout, all checks passing"),
             (False, "FPGA prototype on a real monitor + speaker"),
             (False, "Handheld music-player PCB in KiCad"),
             (False, "Manufacture on a Tiny Tapeout shuttle"),
             (False, "v2: sprite engine + multiply-accumulate unit")]
    for i, (done, s) in enumerate(items):
        y = 150 + 66 * i
        d.text((66, y - 4), "✓" if done else "○", font=F_SYM, fill=GREEN if done else DIM)
        d.text((120, y + 2), s, font=F_BODY, fill=TXT if done else DIM)
    d.text((70, 570), "github.com/AymanZoha/chroma-chip", font=F_H2, fill=ACC)
    return im


RENDER = {"title": scene_title, "demo": scene_demo, "anatomy": scene_anatomy, "beam": scene_beam,
          "audio": scene_audio, "pins": scene_pins, "layout": scene_layout, "verify": scene_verify,
          "next": scene_next}
STATIC = {"title", "pins", "audio"}


# --------------------------------------------------------------------------- main
def main():
    os.makedirs(MEDIA, exist_ok=True)
    clips = synthesize()
    chip = chip_audio()
    durs = []
    for (kind, _), c in zip(SCENES, clips):
        dur = LEAD + len(c) / SR + TAIL
        if kind == "demo":
            dur = max(dur, DEMO_SECONDS + 0.4)
        durs.append(dur)
    total = sum(durs)
    audio = np.zeros(int(total * SR) + SR, np.float32)

    # Build the audio mix first so ffmpeg can read it alongside the piped frames.
    start = 0.0
    for (kind, _), c, dur in zip(SCENES, clips, durs):
        s0 = int((start + LEAD) * SR)
        audio[s0:s0 + len(c)] += c * 0.95
        if kind == "demo":
            c0 = int(start * SR)
            seg = chip[: int(DEMO_SECONDS * SR)]
            gain = np.full(len(seg), 0.30, np.float32)
            nar_end = int((LEAD + len(c) / SR + 0.3) * SR)
            gain[nar_end:] = 0.55  # chip audio comes up once the narrator stops
            audio[c0:c0 + len(seg)] += seg * gain
        start += dur
    audio = np.clip(audio, -1, 1)
    with wave.open(os.path.join(WORK, "mix.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((audio * 32000).astype("<i2").tobytes())

    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-i", os.path.join(WORK, "mix.wav"), "-c:v", "libx264", "-preset",
         "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest",
         "-movflags", "+faststart", OUT], stdin=subprocess.PIPE)

    for (kind, text), c, dur in zip(SCENES, clips, durs):
        subs = sentence_timing(text, len(c) / SR)
        cache = RENDER[kind](0, dur) if kind in STATIC else None
        for fi in range(int(round(dur * FPS))):
            t = fi / FPS
            base = cache if cache is not None else RENDER[kind](t, dur)
            tn = t - LEAD
            line = next((s for a, b, s in subs if a <= tn < b), None)
            ff.stdin.write(subtitle(base, line).tobytes())
        print(f"scene {kind:8s} {dur:5.1f} s")
    ff.stdin.close()
    ff.wait()
    print(f"wrote {OUT}  ({total:.1f} s)")


if __name__ == "__main__":
    main()
