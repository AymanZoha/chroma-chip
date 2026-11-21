"""Original test content for the CHROMA v2 simulation (no copyrighted songs/covers):
two procedurally drawn 128x128 album covers and two synthesized music tracks,
plus a tone sweep for verifying the filter bank.

Outputs in v2/build/assets/:
  cover_a.bin / cover_b.bin   128x128 RGB565 big-endian (what the Pi would send)
  cover_a.png / cover_b.png   previews
  track_a.raw / track_b.raw   48 kHz mono int16
  tones.raw                   tone sweep for the band test
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "build", "assets")
SR = 48000
BAND_HZ = [60, 150, 350, 800, 1600, 3000, 5000, 8000]


# ----------------------------------------------------------------- covers
def to_rgb565(img):
    a = np.asarray(img.convert("RGB")).astype(np.uint16)
    v = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    return v.astype(">u2").tobytes()


def cover_a():
    """'NEON TIDE' - deep blue sea, glowing waves, a moon."""
    s = 512
    y = np.linspace(0, 1, s)[:, None]
    base = np.zeros((s, s, 3))
    base[..., 0] = 10 + 30 * y
    base[..., 1] = 20 + 60 * y
    base[..., 2] = 60 + 110 * y
    img = Image.fromarray(base.astype(np.uint8))
    d = ImageDraw.Draw(img)
    d.ellipse([300, 70, 420, 190], fill=(255, 240, 200))
    glow = Image.new("RGB", (s, s))
    gd = ImageDraw.Draw(glow)
    for k in range(9):
        pts = [(x, 250 + k * 30 + 18 * math.sin(x / 40 + k)) for x in range(0, s + 8, 8)]
        gd.line(pts, fill=(0, 200 + 6 * k, 255 - 10 * k), width=7)
    glow = glow.filter(ImageFilter.GaussianBlur(4))
    img = Image.fromarray(np.clip(np.asarray(img, int) + np.asarray(glow, int), 0, 255).astype(np.uint8))
    return img.resize((128, 128), Image.LANCZOS)


def cover_b():
    """'SILICON SUNRISE' - orange sunrise over circuit traces."""
    s = 512
    y = np.linspace(0, 1, s)[:, None]
    base = np.zeros((s, s, 3))
    base[..., 0] = 255 - 120 * y
    base[..., 1] = 140 - 110 * y
    base[..., 2] = 60 + 40 * y
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    d.ellipse([136, 170, 376, 410], fill=(255, 220, 90))
    d.rectangle([0, 300, s, s], fill=(30, 12, 40))
    rng = np.random.default_rng(7)
    for _ in range(26):
        x = int(rng.integers(10, s - 10))
        yy = 300 + int(rng.integers(10, 200))
        x2 = x + int(rng.integers(-120, 120))
        d.line([(x, s), (x, yy), (x2, yy)], fill=(255, 150, 40), width=5)
        d.ellipse([x2 - 9, yy - 9, x2 + 9, yy + 9], fill=(255, 200, 80))
    return img.resize((128, 128), Image.LANCZOS)


# ----------------------------------------------------------------- music
def adsr(n, a, d, s_lvl, r):
    env = np.ones(n) * s_lvl
    a_n, d_n, r_n = int(a * SR), int(d * SR), int(r * SR)
    env[:a_n] = np.linspace(0, 1, a_n, endpoint=False)
    env[a_n:a_n + d_n] = np.linspace(1, s_lvl, d_n, endpoint=False)
    if r_n:
        env[-r_n:] *= np.linspace(1, 0, r_n)
    return env


def note_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def saw(f, n, detune=0.0):
    t = np.arange(n) / SR
    out = np.zeros(n)
    for dd in (-detune, 0, detune):
        out += 2 * ((t * f * (1 + dd)) % 1.0) - 1
    return out / 3


def lowpass(x, fc):
    a = math.exp(-2 * math.pi * fc / SR)
    y = np.zeros_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc = (1 - a) * x[i] + a * acc
        y[i] = acc
    return y


def track(seconds, bpm, chords, bass_oct, lead, seed):
    n = int(seconds * SR)
    out = np.zeros(n)
    beat = 60.0 / bpm
    rng = np.random.default_rng(seed)
    bar = 4 * beat
    # kick on every beat
    for k in range(int(seconds / beat) + 1):
        s0 = int(k * beat * SR)
        ln = int(0.25 * SR)
        t = np.arange(ln) / SR
        kick = np.sin(2 * np.pi * (45 * t + 90 * (1 - np.exp(-t * 30)) / 30)) * np.exp(-t * 14)
        e = min(n, s0 + ln)
        out[s0:e] += 0.9 * kick[:e - s0]
    # hats on 8ths (offbeats louder)
    for k in range(int(seconds / (beat / 2)) + 1):
        s0 = int(k * beat / 2 * SR)
        ln = int(0.05 * SR)
        hat = rng.standard_normal(ln) * np.exp(-np.arange(ln) / SR * 90)
        hat = np.diff(np.concatenate([[0], hat]))  # crude highpass
        e = min(n, s0 + ln)
        out[s0:e] += (0.25 if k % 2 else 0.12) * hat[:e - s0]
    # chords (pads) + bass
    for i in range(int(seconds / bar) + 1):
        root, quality = chords[i % len(chords)]
        s0 = int(i * bar * SR)
        ln = int(bar * SR)
        e = min(n, s0 + ln)
        if e <= s0:
            break
        third = 3 if quality == "m" else 4
        pad = sum(saw(note_hz(root + iv), ln, 0.004) for iv in (0, third, 7, 12))
        pad = lowpass(pad, 1800) * adsr(ln, 0.08, 0.3, 0.7, 0.2) * 0.10
        out[s0:e] += pad[:e - s0]
        for j in range(8):  # bass eighths
            b0 = s0 + int(j * beat / 2 * SR)
            bl = int(beat / 2 * SR * 0.9)
            if b0 >= n:
                break
            t = np.arange(bl) / SR
            f = note_hz(root - 12 * bass_oct)
            bs = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)) * adsr(bl, 0.005, 0.1, 0.6, 0.05)
            be = min(n, b0 + bl)
            out[b0:be] += 0.35 * bs[:be - b0]
    # lead arpeggio (16ths)
    step = beat / 4
    for k in range(int(seconds / step)):
        m = lead[k % len(lead)]
        if m is None:
            continue
        s0 = int(k * step * SR)
        ln = int(step * SR * 0.95)
        t = np.arange(ln) / SR
        f = note_hz(m)
        tone = (np.sin(2 * np.pi * f * t) + 0.25 * np.sign(np.sin(2 * np.pi * f * t))) * adsr(ln, 0.003, 0.05, 0.4, 0.03)
        e = min(n, s0 + ln)
        out[s0:e] += 0.12 * tone[:e - s0]
    out /= np.abs(out).max()
    return (out * 0.6 * 32767).astype("<i2")


def tones():
    """0.2 s of silence, then 0.25 s of each band's centre frequency."""
    parts = [np.zeros(int(0.2 * SR))]
    for f in BAND_HZ:
        t = np.arange(int(0.25 * SR)) / SR
        parts.append(0.5 * np.sin(2 * np.pi * f * t))
    return (np.concatenate(parts) * 32767).astype("<i2")


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, fn in (("a", cover_a), ("b", cover_b)):
        im = fn()
        im.save(os.path.join(OUT, f"cover_{name}.png"))
        open(os.path.join(OUT, f"cover_{name}.bin"), "wb").write(to_rgb565(im))
    a = track(4.6, 112, [(57, "m"), (53, ""), (60, ""), (55, "")], 1,
              [81, None, 76, 72, 81, None, 76, 72, 77, None, 72, 69, 79, None, 74, 71], 1)
    b = track(4.6, 126, [(62, ""), (57, ""), (59, "m"), (55, "")], 1,
              [74, 78, 81, 86, 73, 76, 81, 85, 71, 74, 78, 83, 67, 71, 74, 79], 2)
    a.tofile(os.path.join(OUT, "track_a.raw"))
    b.tofile(os.path.join(OUT, "track_b.raw"))
    tones().tofile(os.path.join(OUT, "tones.raw"))
    print("assets written to", OUT)


if __name__ == "__main__":
    main()
