"""Build and run the CHROMA cycle-accurate simulation, then turn its output into
screenshots (PNG), an animation (GIF) and the chip's audio (WAV).

Requirements:  pip install yowasp-yosys pillow   and a C++17 compiler (g++).
Usage:         python sim/run_sim.py [frames]
"""
import glob
import os
import shutil
import struct
import subprocess
import sys
import sysconfig
import wave

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
BUILD = os.path.join(ROOT, "build")
OUT = os.path.join(BUILD, "sim_out")
MEDIA = os.path.join(ROOT, "media")
FRAMES = int(sys.argv[1]) if len(sys.argv) > 1 else 600
FRAME_EVERY = 3


def find_yosys():
    exe = shutil.which("yowasp-yosys")
    if exe:
        return exe
    cand = os.path.join(sysconfig.get_path("scripts"), "yowasp-yosys.exe")
    if os.path.exists(cand):
        return cand
    sys.exit("yowasp-yosys not found: pip install yowasp-yosys")


def cxxrtl_include():
    import yowasp_yosys
    return os.path.join(os.path.dirname(yowasp_yosys.__file__),
                        "share", "include", "backends", "cxxrtl", "runtime")


def run(cmd):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(MEDIA, exist_ok=True)
    for f in glob.glob(os.path.join(OUT, "*")):
        os.remove(f)

    # YoWASP Yosys runs sandboxed and only sees the working directory: use relative paths.
    os.chdir(ROOT)
    run([find_yosys(), "-q", "-p",
         "read_verilog -sv src/hvsync_generator.v src/project.v; "
         "hierarchy -top tt_um_chroma_player; write_cxxrtl -O6 -header build/chroma.cc"])
    cc = os.path.join(BUILD, "chroma.cc")

    exe = os.path.join(BUILD, "tb_chroma.exe" if os.name == "nt" else "tb_chroma")
    run(["g++", "-std=c++17", "-O2", "-I", BUILD, "-I", cxxrtl_include(),
         os.path.join(ROOT, "sim", "tb_chroma.cpp"), cc, "-o", exe])

    result = subprocess.run([exe, OUT, str(FRAMES), str(FRAME_EVERY)])
    print(open(os.path.join(OUT, "report.txt")).read())

    # Frames -> PNG screenshots + GIF
    files = sorted(glob.glob(os.path.join(OUT, "frame_*.rgb")))
    imgs = [Image.frombytes("RGB", (640, 480), open(f, "rb").read()) for f in files]
    by_frame = {int(os.path.basename(f)[6:10]): im for f, im in zip(files, imgs)}
    for name, fr in {"screen_playing": 162, "screen_track2": 399, "screen_paused": 495}.items():
        if fr in by_frame:
            by_frame[fr].save(os.path.join(MEDIA, f"{name}.png"))
    if imgs:
        small = [im.resize((320, 240), Image.NEAREST) for im in imgs]
        small[0].save(os.path.join(MEDIA, "chroma_demo.gif"), save_all=True,
                      append_images=small[1:], duration=50, loop=0, optimize=True)

    # Audio -> WAV (remove DC, normalise)
    raw = open(os.path.join(OUT, "audio.raw"), "rb").read()
    samples = struct.unpack(f"{len(raw) // 4}f", raw)
    mean = sum(samples) / max(1, len(samples))
    peak = max(1e-9, max(abs(s - mean) for s in samples))
    pcm = b"".join(struct.pack("<h", int(32000 * 0.9 * (s - mean) / peak)) for s in samples)
    with wave.open(os.path.join(MEDIA, "chroma_audio.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000); w.writeframes(pcm)
    print(f"wrote {len(imgs)} frames, {len(samples) / 48000:.1f} s of audio to {MEDIA}")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
