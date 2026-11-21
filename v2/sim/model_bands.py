"""Bit-exact Python model of audio_bands.v (filters, peaks, levels), fed with the exact
I2S samples the testbench sent. Prints the model's levels over time so they can be
compared with the bar heights the chip actually drew.

  python sim/model_bands.py [offset]
"""
import os
import sys

import numpy as np

V2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F = [129, 322, 751, 1715, 3425, 6393, 10533, 16384]
OFFSET = int(sys.argv[1]) if len(sys.argv) > 1 else 19


def level(p, offset):
    pos = p.bit_length()
    m = (p >> (pos - 2)) & 1 if pos >= 2 else 0
    return max(0, min(15, 2 * pos + m - offset))


def main():
    samples = np.fromfile(os.path.join(V2, "build", "demo", "audio_out.raw"), "<i2").astype(int).tolist()
    frames = [int(x) for x in open(os.path.join(V2, "build", "demo", "frame_cycles.txt"))]
    low = [0] * 8; band = [0] * 8; peak = [0] * 8; disp = [0] * 8; lvl = [0] * 8
    fi = 0
    out = []
    for n, s in enumerate(samples):
        t_cyc = n * 50_000_000 // 48000
        decay = False
        while fi < len(frames) and frames[fi] <= t_cyc:
            decay = True
            fi += 1
        x = s << 6
        lv = []
        for b in range(8):
            low[b] += (F[b] * band[b]) >> 14
            high = x - low[b] - (band[b] >> 2)
            band[b] += (F[b] * high) >> 14
            m20 = (abs(band[b]) >> 6) & 0xFFFFF
            base = peak[b] - (peak[b] >> 2) if decay else peak[b]
            if decay:
                lv.append(level(peak[b], OFFSET))
            peak[b] = max(m20, base)
        if decay:
            # renderer latches the levels from the previous frame's update, then falls by 1/frame
            disp = [l if l > dd else max(dd - 1, 0) for l, dd in zip(lvl, disp)]
            lvl = lv
            out.append((n / 48000, list(disp)))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import run_v2
    diffs = []
    for i in range(12, 240):
        t = i / 30
        prev = [d for tt, d in out if tt <= t]
        if not prev:
            continue
        model = prev[-2] if len(prev) > 1 else prev[-1]   # bars are drawn near the end of a frame
        chip = [(h + 1) // 2 for h in run_v2.bar_heights(run_v2.frame("demo", i))]
        diffs.append(max(abs(a - b) for a, b in zip(model, chip)))
    diffs = np.array(diffs)
    print(f"frames compared: {len(diffs)}; exact match: {(diffs == 0).mean() * 100:.0f}%; "
          f"within 1 level: {(diffs <= 1).mean() * 100:.0f}%; worst: {diffs.max()}")


if __name__ == "__main__":
    main()
