// Cycle-accurate testbench for the CHROMA chip.
//
// The Verilog is compiled to C++ by Yosys (CXXRTL). This testbench drives the
// clock and buttons, then behaves like a VGA monitor: it only looks at the chip's
// output pins (hsync, vsync, RGB), checks the sync timing, and reconstructs frames
// from them. It also low-pass filters the 1-bit audio pin into a 48 kHz WAV stream.
//
// Outputs (in the directory given as argv[1]):
//   frame_NNNN.rgb  raw 640x480 RGB888 frames (every FRAME_EVERY-th frame)
//   audio.raw       48 kHz mono float32 samples
//   report.txt      timing checks and statistics

#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include "chroma.h"

static const int H_TOTAL = 800, V_TOTAL = 525;
static const int H_SYNC = 96, V_SYNC = 2;
static const int H_VIS_OFFSET = 144;  // hsync fall -> first visible pixel (sync 96 + back porch 48)
static const int V_VIS_OFFSET = 35;   // vsync fall -> first visible line (sync 2 + back porch 33)
static const double CLK_HZ = 25175000.0, AUDIO_HZ = 48000.0;

int main(int argc, char **argv) {
  std::string out = argc > 1 ? argv[1] : ".";
  int total_frames = argc > 2 ? atoi(argv[2]) : 600;
  int frame_every = argc > 3 ? atoi(argv[3]) : 3;

  cxxrtl_design::p_tt__um__chroma__player top;
  auto tick = [&]() {
    top.p_clk.set<bool>(false); top.step();
    top.p_clk.set<bool>(true);  top.step();
  };

  // Reset
  top.p_ena.set<bool>(true);
  top.p_ui__in.set<uint8_t>(0);
  top.p_uio__in.set<uint8_t>(0);
  top.p_rst__n.set<bool>(false);
  for (int i = 0; i < 10; i++) tick();
  top.p_rst__n.set<bool>(true);

  std::vector<uint8_t> img(640 * 480 * 3, 0);
  FILE *faudio = fopen((out + "/audio.raw").c_str(), "wb");
  FILE *frep = fopen((out + "/report.txt").c_str(), "w");

  long long cycle = 0, last_hfall = -1, last_hrise_len = 0;
  long long hsync_period_errors = 0, hsync_width_errors = 0, blank_errors = 0;
  long long vsync_period_errors = 0, vsync_width_errors = 0;
  int line_since_vfall = -1, frames_seen = 0, lines_in_frame = 0, vlow_lines = 0;
  bool prev_h = true, prev_v = true, vlow = false;
  double audio_acc = 0, audio_cnt = 0, audio_phase = 0, lp = 0;
  long long audio_ones = 0;

  // Button script, by frame number (60 frames = 1 second)
  auto buttons_for_frame = [](int f) -> uint8_t {
    uint8_t v = 0;
    if (f >= 300 && f < 306) v |= 0x02;  // press NEXT at 5.0 s
    if (f >= 450 && f < 510) v |= 0x01;  // PAUSE from 7.5 s to 8.5 s
    return v;
  };

  while (frames_seen < total_frames) {
    top.p_ui__in.set<uint8_t>(buttons_for_frame(frames_seen));
    tick();
    cycle++;

    uint8_t uo = top.p_uo__out.get<uint8_t>();
    uint8_t uio = top.p_uio__out.get<uint8_t>();
    bool h = (uo >> 7) & 1, v = (uo >> 3) & 1;
    int r = ((uo >> 0) & 1) << 1 | ((uo >> 4) & 1);
    int g = ((uo >> 1) & 1) << 1 | ((uo >> 5) & 1);
    int b = ((uo >> 2) & 1) << 1 | ((uo >> 6) & 1);

    // ---- hsync ----
    if (prev_h && !h) {
      if (last_hfall >= 0 && cycle - last_hfall != H_TOTAL) hsync_period_errors++;
      last_hfall = cycle;
      if (line_since_vfall >= 0) line_since_vfall++;
      lines_in_frame++;
      if (vlow) vlow_lines++;
    }
    if (!prev_h && h && last_hfall >= 0) {
      last_hrise_len = cycle - last_hfall;
      if (last_hrise_len != H_SYNC) hsync_width_errors++;
    }

    // ---- vsync ----
    if (prev_v && !v) {
      if (frames_seen > 0 && lines_in_frame != V_TOTAL) vsync_period_errors++;
      if (frames_seen > 0) {
        if (frames_seen % frame_every == 0) {
          char name[64];
          snprintf(name, sizeof name, "/frame_%04d.rgb", frames_seen);
          FILE *f = fopen((out + name).c_str(), "wb");
          fwrite(img.data(), 1, img.size(), f);
          fclose(f);
        }
      }
      frames_seen++;
      lines_in_frame = 0;
      line_since_vfall = 0;
      vlow = true;
      vlow_lines = 0;
    }
    if (!prev_v && v) {
      vlow = false;
      if (frames_seen > 0 && vlow_lines != V_SYNC) vsync_width_errors++;
    }

    // ---- pixel capture (like a monitor: positions derived from the sync pulses) ----
    if (last_hfall >= 0 && line_since_vfall >= 0) {
      int col = (int)(cycle - last_hfall) - H_VIS_OFFSET;
      int row = line_since_vfall - V_VIS_OFFSET;
      bool visible = col >= 0 && col < 640 && row >= 0 && row < 480;
      if (visible) {
        uint8_t *p = &img[(row * 640 + col) * 3];
        p[0] = r * 85; p[1] = g * 85; p[2] = b * 85;
      } else if (r | g | b) {
        blank_errors++;  // color during blanking breaks real monitors
      }
    }
    prev_h = h; prev_v = v;

    // ---- audio: average the 1-bit pin over each 48 kHz sample period ----
    bool a = (uio >> 7) & 1;
    audio_ones += a;
    audio_acc += a; audio_cnt += 1;
    audio_phase += AUDIO_HZ;
    if (audio_phase >= CLK_HZ) {
      audio_phase -= CLK_HZ;
      float s = (float)(audio_acc / audio_cnt);
      lp += 0.35 * (s - lp);  // gentle extra low-pass, like the Audio PMOD's RC filter
      float o = (float)lp;
      fwrite(&o, sizeof o, 1, faudio);
      audio_acc = 0; audio_cnt = 0;
    }
  }

  fclose(faudio);
  fprintf(frep, "cycles_simulated      %lld\n", cycle);
  fprintf(frep, "frames                %d\n", frames_seen);
  fprintf(frep, "hsync_period_errors   %lld (expected period %d clocks)\n", hsync_period_errors, H_TOTAL);
  fprintf(frep, "hsync_width_errors    %lld (expected width %d clocks)\n", hsync_width_errors, H_SYNC);
  fprintf(frep, "vsync_period_errors   %lld (expected %d lines/frame)\n", vsync_period_errors, V_TOTAL);
  fprintf(frep, "vsync_width_errors    %lld (expected width %d lines)\n", vsync_width_errors, V_SYNC);
  fprintf(frep, "color_in_blanking     %lld\n", blank_errors);
  fprintf(frep, "audio_duty            %.4f\n", (double)audio_ones / cycle);
  fclose(frep);
  bool ok = !hsync_period_errors && !hsync_width_errors && !vsync_period_errors &&
            !vsync_width_errors && !blank_errors;
  printf("%s\n", ok ? "PASS" : "FAIL");
  return ok ? 0 : 1;
}
