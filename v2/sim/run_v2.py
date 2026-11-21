"""Build + run the CHROMA v2 system simulation and check the results.

  python sim/run_v2.py            build, run demo + tone test, analyse
  python sim/run_v2.py analyse    re-run only the analysis on existing output

Checks performed (in addition to the testbench's protocol/timing checks):
  * texture path: with the record not yet spinning, every visible label pixel on the
    LCD must equal the cover-art pixel the Pi uploaded (SPI -> PSRAM -> texture unit -> LCD)
  * filter bank: for each test tone, the tallest spectrum bar must be that tone's band
"""
import glob
import os
import subprocess
import sys
import sysconfig
import wave

import numpy as np
from PIL import Image

V2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(V2, "build")
ASSETS = os.path.join(BUILD, "assets")
SRCS = ["spi_slave.v", "psram_qpi.v", "audio_bands.v", "font_rom.v", "sin_lut.v", "renderer.v", "chroma_v2.v"]
sys.path.insert(0, os.path.join(V2, "tools"))
from gen_tables import encode  # noqa: E402

TRACKS = {"a": ("NEON TIDE", "THE OSCILLATORS"), "b": ("SILICON SUNRISE", "GATE ARRAY")}


def yosys():
    exe = os.path.join(sysconfig.get_path("scripts"), "yowasp-yosys.exe")
    return exe if os.path.exists(exe) else "yowasp-yosys"


def build():
    subprocess.run([sys.executable, os.path.join(V2, "tools", "gen_tables.py")], check=True)
    subprocess.run([sys.executable, os.path.join(V2, "sim", "gen_assets.py")], check=True)
    for k, (title, artist) in TRACKS.items():
        for i, s in enumerate((title, artist)):
            open(os.path.join(ASSETS, f"text_{k}{i}.txt"), "w").write(",".join(map(str, encode(s))))
    os.chdir(V2)
    os.makedirs("build", exist_ok=True)
    subprocess.run([yosys(), "-q", "-p",
                    "read_verilog -sv " + " ".join("src/" + s for s in SRCS) +
                    "; hierarchy -top tt_um_chroma_v2; write_cxxrtl -O6 -header build/chroma_v2.cc"], check=True)
    import yowasp_yosys
    inc = os.path.join(os.path.dirname(yowasp_yosys.__file__), "share", "include", "backends", "cxxrtl", "runtime")
    subprocess.run(["g++", "-std=c++17", "-O2", "-I", BUILD, "-I", inc, "sim/tb_v2.cpp", "build/chroma_v2.cc",
                    "-o", "build/tb_v2.exe"], check=True)


def run(mode):
    out = os.path.join(BUILD, mode)
    os.makedirs(out, exist_ok=True)
    for f in glob.glob(os.path.join(out, "*")):
        os.remove(f)
    r = subprocess.run([os.path.join(BUILD, "tb_v2.exe"), out, ASSETS, mode])
    print(open(os.path.join(out, "report.txt")).read())
    return r.returncode == 0


def frame(mode, i):
    p = os.path.join(BUILD, mode, f"f_{i:04d}.rgb")
    return np.frombuffer(open(p, "rb").read(), np.uint8).reshape(320, 240, 3)


def to888(rgb565_img):
    a = np.asarray(rgb565_img.convert("RGB")).astype(np.int32)
    r, g, b = a[..., 0] >> 3, a[..., 1] >> 2, a[..., 2] >> 3
    return np.stack([r * 255 // 31, g * 255 // 63, b * 255 // 31], -1)


def check_texture():
    """Frame at t = 0.2 s: cover A uploaded, record not spinning (angle 0)."""
    f = frame("demo", 6).astype(np.int32)
    cov = np.frombuffer(open(os.path.join(ASSETS, "cover_a.bin"), "rb").read(), ">u2").reshape(128, 128)
    r, g, b = (cov >> 11) & 31, (cov >> 5) & 63, cov & 31
    cov888 = np.stack([r * 255 // 31, g * 255 // 63, b * 255 // 31], -1).astype(np.int32)
    checked = bad = 0
    for y in range(54, 182):
        for x in range(56, 184):
            dx, dy = x - 120, y - 118
            r2 = dx * dx + dy * dy
            if r2 >= 3844 or r2 < 25:
                continue
            if abs(dy) < 13 and 4 <= abs(dx) <= 10:  # pause icon (not playing yet)
                continue
            checked += 1
            if not np.array_equal(f[y, x], cov888[y - 54, x - 56]):
                bad += 1
    print(f"texture check: {checked} label pixels compared, {bad} mismatches")
    return bad == 0 and checked > 10000


def bar_heights(f):
    heights = []
    for bx in range(8):
        xc = bx * 30 + 15
        col = f[272:304, xc]
        h = 0
        for yy in range(31, -1, -1):  # count lit rows from the bottom up
            if col[yy].max() > 120:
                h = 32 - yy
        heights.append(h)
    return heights


def check_tones():
    ok = True
    names = ["60 Hz", "150 Hz", "350 Hz", "800 Hz", "1.6 kHz", "3 kHz", "5 kHz", "8 kHz"]
    for band in range(8):
        t_end = 0.4 + 0.25 * (band + 1)  # tones start after 0.2 s of silence at t = 0.2 s
        i = int(t_end * 30) - 1  # last captured frame inside this tone
        h = bar_heights(frame("tones", i))
        best = int(np.argmax(h))
        good = best == band
        ok &= good
        print(f"tone {names[band]:>7}: bar heights {h}  tallest = band {best} {'OK' if good else 'WRONG'}")
    return ok


def export_media():
    media = os.path.join(V2, "media")
    os.makedirs(media, exist_ok=True)
    for name, i in {"v2_screen_a": 75, "v2_screen_paused": 110, "v2_screen_b": 200}.items():
        Image.fromarray(frame("demo", i)).resize((480, 640), Image.NEAREST).save(os.path.join(media, name + ".png"))
    raw = np.fromfile(os.path.join(BUILD, "demo", "audio_out.raw"), "<i2")
    with wave.open(os.path.join(BUILD, "demo", "audio_out.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000); w.writeframes(raw.tobytes())


if __name__ == "__main__":
    ok = True
    if not (len(sys.argv) > 1 and sys.argv[1] == "analyse"):
        build()
        ok &= run("demo")
        ok &= run("tones")
    ok &= check_texture()
    ok &= check_tones()
    export_media()
    print("ALL CHECKS PASS" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)
