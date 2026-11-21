// CHROMA v2 system testbench (CXXRTL). Everything around the chip is modelled at
// the pin level:
//   * Pi host     - SPI master sending STATE / TEXT / COVER commands on a schedule
//   * Pi audio    - I2S transmitter (48 kHz, 64 BCLK/frame) playing the test tracks
//   * LCD         - ST7789-style 8080 controller: decodes commands, owns a 240x320
//                   frame memory, checks write timing and protocol
//   * PSRAM       - APS6404-style QPI memory: SPI-mode 0x35 entry, 0xEB reads with
//                   6 wait cycles, 0x38 writes; checks bus contention and CE# time
// Usage: tb_v2 <outdir> <assetdir> <mode: demo|tones>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <deque>
#include <string>
#include <vector>
#include "chroma_v2.h"

static const double CLK = 50e6;
static long long cycle = 0;
static inline long long at(double s) { return (long long)(s * CLK); }

static std::vector<uint8_t> load(const std::string &p) {
  FILE *f = fopen(p.c_str(), "rb");
  if (!f) { fprintf(stderr, "missing %s\n", p.c_str()); exit(2); }
  std::vector<uint8_t> d;
  uint8_t buf[65536];
  size_t n;
  while ((n = fread(buf, 1, sizeof buf, f)) > 0) d.insert(d.end(), buf, buf + n);
  fclose(f);
  return d;
}

// ============================================================================ SPI host
struct SpiHost {
  struct Tx { long long when; std::vector<uint8_t> bytes; };
  std::deque<Tx> q;
  bool sck = false, mosi = false, csn = true;
  int state = 0;  // 0 idle, 1 lead-in, 2 bits, 3 lead-out, 4 gap
  long long t = 0;
  size_t byte_i = 0;
  int bit_i = 0, phase = 0;
  static const int HALF = 4;  // 6.25 MHz SCK at 50 MHz
  long long tx_done = 0;

  void add(double s, std::vector<uint8_t> b) { q.push_back({at(s), std::move(b)}); }

  void step() {
    switch (state) {
      case 0:
        if (!q.empty() && cycle >= q.front().when) { csn = false; state = 1; t = 0; }
        break;
      case 1:
        if (++t >= HALF) { state = 2; byte_i = 0; bit_i = 0; phase = 0; t = 0; set_bit(); }
        break;
      case 2:
        if (++t >= HALF) {
          t = 0;
          if (phase == 0) { sck = true; phase = 1; }
          else {
            sck = false; phase = 0;
            if (++bit_i == 8) { bit_i = 0; byte_i++; }
            if (byte_i >= q.front().bytes.size()) state = 3;
            else set_bit();
          }
        }
        break;
      case 3:
        if (++t >= HALF) { csn = true; state = 4; t = 0; }
        break;
      case 4:
        if (++t >= 4 * HALF) { q.pop_front(); state = 0; tx_done++; }
        break;
    }
  }
  void set_bit() { mosi = (q.front().bytes[byte_i] >> (7 - bit_i)) & 1; }
};

// ============================================================================ I2S source
struct Segment { long long start, end; const std::vector<int16_t> *pcm; size_t offset; };
struct I2S {
  std::vector<Segment> segs;
  bool bclk = false, lrclk = false, din = false;
  long long acc = 0;
  int bitpos = 63;
  int16_t cur = 0;
  FILE *out = nullptr;  // log of every left sample sent = what the speaker plays

  int16_t sample_now() {
    for (auto &s : segs)
      if (cycle >= s.start && cycle < s.end) {
        size_t i = s.offset + (size_t)((cycle - s.start) * 48000 / (long long)CLK);
        return i < s.pcm->size() ? (*s.pcm)[i] : 0;
      }
    return 0;
  }
  void step() {
    acc += 2LL * 3072000;
    if (acc >= (long long)CLK) {
      acc -= (long long)CLK;
      bclk = !bclk;
      if (!bclk) {  // falling edge: present the next bit
        bitpos = (bitpos + 1) & 63;
        if (bitpos == 0) {
          cur = sample_now();
          if (out) fwrite(&cur, 2, 1, out);
        }
        lrclk = bitpos >= 32;
        int q = bitpos & 31;
        uint32_t word = (bitpos < 32) ? ((uint32_t)(uint16_t)cur << 16) : 0u;
        din = (q == 0) ? false : ((word >> (32 - q)) & 1);
      }
    }
  }
};

// ============================================================================ LCD model
struct Lcd {
  std::vector<uint16_t> gram = std::vector<uint16_t>(240 * 320, 0);
  bool prev_wr = true;
  long long wr_fall = -1, wr_rise = -1;
  long long min_low = 1 << 30, min_high = 1 << 30, min_period = 1 << 30;
  uint8_t cmd = 0;
  int param = 0, xs = 0, xe = 239, ys = 0, ye = 319, x = 0, y = 0, phase = 0;
  uint8_t hi = 0;
  bool colmod_ok = false, disp_on = false, slp_out = false;
  long long slp_cycle = -1;
  long long errors = 0, early_cmds = 0, frames = 0;
  std::vector<long long> frame_cycles;

  void on_byte(uint8_t b, bool dc) {
    if (!dc) {
      cmd = b; param = 0;
      if (slp_out && slp_cycle >= 0 && cycle - slp_cycle < at(0.005)) early_cmds++;
      switch (b) {
        case 0x11: slp_out = true; slp_cycle = cycle; break;
        case 0x29: disp_on = true; break;
        case 0x2C: x = xs; y = ys; phase = 0; break;
        case 0x2A: case 0x2B: case 0x3A: case 0x36: break;
        default: errors++;
      }
      return;
    }
    switch (cmd) {
      case 0x2A: if (param == 1) xs = b; if (param == 3) xe = b; if (param == 0 || param == 2) { if (b) errors++; } break;
      case 0x2B: if (param == 0) ys = b << 8; if (param == 1) ys |= b; if (param == 2) ye = b << 8; if (param == 3) ye |= b; break;
      case 0x3A: colmod_ok = (b == 0x55); break;
      case 0x36: break;
      case 0x2C:
        if (!colmod_ok || !disp_on) errors++;
        if (phase == 0) { hi = b; phase = 1; }
        else {
          phase = 0;
          if (x < 240 && y < 320) gram[y * 240 + x] = (uint16_t)(hi << 8 | b);
          if (++x > xe) { x = xs; if (++y > ye) { y = ys; frames++; frame_cycles.push_back(cycle); } }
        }
        break;
      default: errors++;
    }
    param++;
  }
  void step(uint8_t d, bool wr, bool dc) {
    if (prev_wr && !wr) {
      if (wr_fall >= 0) min_period = std::min(min_period, cycle - wr_fall);
      if (wr_rise >= 0) min_high = std::min(min_high, cycle - wr_rise);
      wr_fall = cycle;
    }
    if (!prev_wr && wr) {
      min_low = std::min(min_low, cycle - wr_fall);
      wr_rise = cycle;
      on_byte(d, dc);  // ST7789 latches D/DC on the WR rising edge
    }
    prev_wr = wr;
  }
};

// ============================================================================ PSRAM model
struct Psram {
  std::vector<uint8_t> mem = std::vector<uint8_t>(1 << 20, 0);
  bool prev_cs = true, prev_sck = false, qpi = false, driving = false;
  uint8_t drive = 0;
  int n = 0, spi_bits = 0;
  uint32_t cmd = 0, addr = 0;
  long long cs_fall = 0, max_cs_low = 0, contention = 0, proto_err = 0, reads = 0, writes = 0;
  uint8_t wbyte = 0;

  void step(bool cs_n, bool sck, uint8_t io_o, uint8_t io_oe) {
    if (driving && io_oe) contention++;
    if (prev_cs && !cs_n) { n = 0; spi_bits = 0; cmd = 0; addr = 0; cs_fall = cycle; driving = false; }
    if (!prev_cs && cs_n) {
      max_cs_low = std::max(max_cs_low, cycle - cs_fall);
      if (!qpi) { if (spi_bits == 8 && cmd == 0x35) qpi = true; else proto_err++; }
      else if (cmd == 0xEB) { reads++; if (n != 18) proto_err++; }
      else if (cmd == 0x38) { writes++; if (n != 12) proto_err++; }
      else proto_err++;
      driving = false;
    }
    if (!cs_n && !prev_sck && sck) {  // rising edge: memory samples
      if (!qpi) { cmd = (cmd << 1) | (io_o & 1); spi_bits++; }
      else {
        uint8_t nib = io_o & 0xF;
        if (n < 8 && io_oe != 0xF) proto_err++;
        if (n < 2) cmd = (cmd << 4) | nib;
        else if (n < 8) addr = (addr << 4) | nib;
        else if (cmd == 0x38) {
          if (io_oe != 0xF) proto_err++;
          if (((n - 8) & 1) == 0) wbyte = nib << 4;
          else mem[(addr + (n - 8) / 2) & (mem.size() - 1)] = wbyte | nib;
        }
        n++;
      }
    }
    if (!cs_n && prev_sck && !sck && qpi && cmd == 0xEB && n >= 14) {  // falling edge: drive data
      int k = n - 14;
      uint8_t byte = mem[(addr + k / 2) & (mem.size() - 1)];
      drive = (k & 1) ? (byte & 0xF) : (byte >> 4);
      driving = true;
    }
    prev_cs = cs_n;
    prev_sck = sck;
  }
};

// ============================================================================ main
static std::vector<uint8_t> text_cmd(int line, const char *codes_csv) {
  std::vector<uint8_t> b = {0x02, (uint8_t)line};
  const char *p = codes_csv;
  while (*p) { b.push_back((uint8_t)strtol(p, (char **)&p, 10)); if (*p == ',') p++; }
  return b;
}
static std::vector<uint8_t> state_cmd(int play, int prog, uint16_t acc) {
  return {0x01, (uint8_t)play, (uint8_t)prog, (uint8_t)(acc >> 8), (uint8_t)acc};
}

int main(int argc, char **argv) {
  std::string out = argv[1], assets = argv[2], mode = argc > 3 ? argv[3] : "demo";
  cxxrtl_design::p_tt__um__chroma__v2 top;
  SpiHost spi;
  I2S i2s;
  Lcd lcd;
  Psram ps;
  i2s.out = fopen((out + "/audio_out.raw").c_str(), "wb");

  auto to16 = [](const std::vector<uint8_t> &b) {
    std::vector<int16_t> v(b.size() / 2);
    memcpy(v.data(), b.data(), v.size() * 2);
    return v;
  };
  std::vector<int16_t> ta = to16(load(assets + "/track_a.raw"));
  std::vector<int16_t> tb = to16(load(assets + "/track_b.raw"));
  std::vector<int16_t> tones = to16(load(assets + "/tones.raw"));
  std::vector<uint8_t> cov_a = load(assets + "/cover_a.bin"), cov_b = load(assets + "/cover_b.bin");
  auto txt = [&](const char *name) { auto d = load(assets + "/" + name); d.push_back(0); return std::string((char *)d.data()); };

  double end_s;
  if (mode == "tones") {
    i2s.segs.push_back({at(0.2), at(0.2 + tones.size() / 48000.0), &tones, 0});
    spi.add(0.01, state_cmd(1, 0, 0x07FF));
    end_s = 0.2 + tones.size() / 48000.0 + 0.05;
  } else {
    // track A: metadata + cover while the LCD wakes up, start playing at 0.25 s
    spi.add(0.002, text_cmd(0, txt("text_a0.txt").c_str()));
    spi.add(0.002, text_cmd(1, txt("text_a1.txt").c_str()));
    spi.add(0.002, state_cmd(0, 20, 0x2DFF));
    std::vector<uint8_t> c = {0x03}; c.insert(c.end(), cov_a.begin(), cov_a.end());
    spi.add(0.003, c);
    i2s.segs.push_back({at(0.25), at(3.4), &ta, 0});
    i2s.segs.push_back({at(4.0), at(4.8), &ta, (size_t)((3.4 - 0.25) * 48000)});
    for (double t = 0.25; t < 4.8; t += 0.1) {
      bool paused = t >= 3.4 && t < 4.0;
      double played = std::min(t, 3.4) - 0.25 + (t >= 4.0 ? t - 4.0 : 0);
      spi.add(t, state_cmd(paused ? 0 : 1, (int)(20 + played * 22), 0x2DFF));
    }
    // NEXT: track B
    spi.add(4.8, text_cmd(0, txt("text_b0.txt").c_str()));
    spi.add(4.8, text_cmd(1, txt("text_b1.txt").c_str()));
    spi.add(4.8, state_cmd(1, 0, 0xFC80));
    std::vector<uint8_t> d = {0x03}; d.insert(d.end(), cov_b.begin(), cov_b.end());
    spi.add(4.8, d);
    i2s.segs.push_back({at(4.86), at(8.2), &tb, 0});
    for (double t = 4.9; t < 8.2; t += 0.1) spi.add(t, state_cmd(1, (int)((t - 4.86) * 22), 0xFC80));
    end_s = 8.2;
  }

  if (argc > 4) end_s = atof(argv[4]);  // optional: stop early (quick checks)

  // reset
  top.p_ena.set<bool>(true);
  top.p_rst__n.set<bool>(false);
  for (int k = 0; k < 10; k++) { top.p_clk.set<bool>(false); top.step(); top.p_clk.set<bool>(true); top.step(); }
  top.p_rst__n.set<bool>(true);

  const long long dump_every = (long long)(CLK / 30);
  long long next_dump = 0;
  int dump_i = 0;
  std::vector<uint8_t> rgb(240 * 320 * 3);
  long long end_c = at(end_s);

  while (cycle < end_c) {
    spi.step();
    i2s.step();
    uint8_t ui = (spi.sck ? 1 : 0) | (spi.mosi ? 2 : 0) | (spi.csn ? 4 : 0) |
                 (i2s.bclk ? 8 : 0) | (i2s.lrclk ? 16 : 0) | (i2s.din ? 32 : 0);
    top.p_ui__in.set<uint8_t>(ui);
    top.p_uio__in.set<uint8_t>(ps.driving ? (uint8_t)(ps.drive << 4) : 0);
    top.p_clk.set<bool>(false); top.step();
    top.p_clk.set<bool>(true);  top.step();
    top.step();  // settle: make outputs reflect the registers updated on this edge
    cycle++;

    uint8_t uo = top.p_uo__out.get<uint8_t>();
    uint8_t uio = top.p_uio__out.get<uint8_t>();
    uint8_t oe = top.p_uio__oe.get<uint8_t>();
    lcd.step(uo, uio & 1, (uio >> 1) & 1);
    ps.step((uio >> 2) & 1, (uio >> 3) & 1, uio >> 4, oe >> 4);
    if (cycle >= next_dump) {
      for (int i = 0; i < 240 * 320; i++) {
        uint16_t p = lcd.gram[i];
        rgb[i * 3 + 0] = (uint8_t)(((p >> 11) & 31) * 255 / 31);
        rgb[i * 3 + 1] = (uint8_t)(((p >> 5) & 63) * 255 / 63);
        rgb[i * 3 + 2] = (uint8_t)((p & 31) * 255 / 31);
      }
      char name[64];
      snprintf(name, sizeof name, "/f_%04d.rgb", dump_i++);
      FILE *f = fopen((out + name).c_str(), "wb");
      fwrite(rgb.data(), 1, rgb.size(), f);
      fclose(f);
      next_dump += dump_every;
    }
  }
  fclose(i2s.out);

  FILE *r = fopen((out + "/report.txt").c_str(), "w");
  double fps = 0;
  if (lcd.frame_cycles.size() > 2)
    fps = (lcd.frame_cycles.size() - 1) * CLK / (double)(lcd.frame_cycles.back() - lcd.frame_cycles.front());
  fprintf(r, "cycles               %lld\n", cycle);
  fprintf(r, "lcd_frames           %lld\n", lcd.frames);
  fprintf(r, "lcd_fps              %.1f\n", fps);
  fprintf(r, "lcd_protocol_errors  %lld\n", lcd.errors);
  fprintf(r, "lcd_cmds_too_soon    %lld\n", lcd.early_cmds);
  fprintf(r, "lcd_wr_low_min_ns    %lld\n", lcd.min_low * 20);
  fprintf(r, "lcd_wr_high_min_ns   %lld\n", lcd.min_high * 20);
  fprintf(r, "lcd_wr_cycle_min_ns  %lld\n", lcd.min_period * 20);
  fprintf(r, "psram_qpi_mode       %d\n", ps.qpi ? 1 : 0);
  fprintf(r, "psram_reads          %lld\n", ps.reads);
  fprintf(r, "psram_writes         %lld\n", ps.writes);
  fprintf(r, "psram_protocol_errors %lld\n", ps.proto_err);
  fprintf(r, "psram_bus_contention %lld\n", ps.contention);
  fprintf(r, "psram_max_cs_low_ns  %lld\n", ps.max_cs_low * 20);
  fprintf(r, "spi_transactions     %lld\n", spi.tx_done);
  fclose(r);
  FILE *ft = fopen((out + "/frame_cycles.txt").c_str(), "w");
  for (auto c : lcd.frame_cycles) fprintf(ft, "%lld\n", c);
  fclose(ft);
  bool ok = lcd.errors == 0 && lcd.early_cmds == 0 && ps.proto_err == 0 && ps.contention == 0 &&
            ps.qpi && lcd.min_low * 20 >= 15 && lcd.min_high * 20 >= 15 && lcd.min_period * 20 >= 66 &&
            ps.max_cs_low * 20 <= 8000;
  printf("%s\n", ok ? "PASS" : "FAIL");
  return ok ? 0 : 1;
}
