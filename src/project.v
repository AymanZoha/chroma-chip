/*
 * CHROMA - a tiny graphics + audio chip for a music player
 *
 * One chip that both PLAYS music and DRAWS the player's UI:
 *   - 640x480 @ 60 Hz VGA output (TinyVGA PMOD pinout) rendered "racing the beam"
 *     (no framebuffer: every pixel is computed on the fly as the beam scans)
 *   - "Now playing" screen: title, 16-band LED-style visualizer that reacts to the
 *     note being played, smooth progress bar, play/pause + next icons, track dots
 *   - Square-wave synthesizer with a decaying volume envelope, 1-bit PWM audio
 *     output (Tiny Tapeout Audio PMOD pin uio[7])
 *   - 32-step note sequencer with 4 track variations, each with its own color theme
 *
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_chroma_player (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock (25.175 MHz for 640x480 VGA)
    input  wire       rst_n     // reset_n - low to reset
);

  // ===========================================================================
  // Video timing
  // ===========================================================================
  wire       hsync, vsync, video_active;
  wire [9:0] pix_x, pix_y;

  hvsync_generator vga (
      .clk       (clk),
      .reset     (~rst_n),
      .hsync     (hsync),
      .vsync     (vsync),
      .display_on(video_active),
      .hpos      (pix_x),
      .vpos      (pix_y)
  );

  // One-cycle pulse at the start of vertical blanking: our 60 Hz "game loop" tick.
  wire frame_tick = (pix_x == 10'd0) && (pix_y == 10'd480);

  // ===========================================================================
  // Controls + sequencer
  // ===========================================================================
  wire pause_in = ui_in[0];
  wire next_in  = ui_in[1];

  reg  [2:0] frame_div;   // 8 frames per step
  reg  [4:0] step;        // position in the 32-step song
  reg  [1:0] track;       // which of 4 track variations is selected
  reg        next_prev;   // previous sample of the next button (sampled per frame = debounce)
  reg  [3:0] env;         // volume envelope, also drives the visualizer
  reg  [4:0] note;        // {octave, semitone} of the note currently sounding
  reg  [7:0] lfsr;        // per-frame pseudo-random jitter for the visualizer

  // Track bit 1 plays the melody backwards.
  wire [4:0] rom_addr = track[1] ? ~step : step;
  wire [5:0] rom_data;    // {play, octave, semitone[3:0]}

  melody_rom rom (
      .addr(rom_addr),
      .data(rom_data)
  );

  wire next_pressed = next_in && !next_prev;

  always @(posedge clk) begin
    if (!rst_n) begin
      frame_div <= 3'd0;
      step      <= 5'd0;
      track     <= 2'd0;
      next_prev <= 1'b0;
      env       <= 4'd0;
      note      <= 5'd0;
      lfsr      <= 8'h5A;
    end else if (frame_tick) begin
      next_prev <= next_in;
      lfsr      <= {lfsr[6:0], lfsr[7] ^ lfsr[5] ^ lfsr[4] ^ lfsr[3]};

      if (next_pressed) begin
        track     <= track + 2'd1;
        step      <= 5'd0;
        frame_div <= 3'd0;
        env       <= 4'd0;
      end else if (!pause_in) begin
        frame_div <= frame_div + 3'd1;
        if (frame_div == 3'd7) step <= step + 5'd1;

        if (frame_div == 3'd0 && rom_data[5]) begin
          env  <= 4'd15;               // note on: retrigger envelope
          note <= rom_data[4:0];
        end else if (env != 4'd0) begin
          env <= env - 4'd1;           // decay
        end
      end else if (env != 4'd0) begin
        env <= env - 4'd1;             // paused: let the last note ring out
      end
    end
  end

  // ===========================================================================
  // Synthesizer: square wave oscillator + PWM volume
  // ===========================================================================
  reg [15:0] half_period_base;  // clock cycles per half wave, octave 4
  always @(*) begin
    case (note[3:0])
      4'd0:    half_period_base = 16'd48113;  // C4
      4'd1:    half_period_base = 16'd45412;  // C#4
      4'd2:    half_period_base = 16'd42863;  // D4
      4'd3:    half_period_base = 16'd40458;  // D#4
      4'd4:    half_period_base = 16'd38187;  // E4
      4'd5:    half_period_base = 16'd36044;  // F4
      4'd6:    half_period_base = 16'd34021;  // F#4
      4'd7:    half_period_base = 16'd32111;  // G4
      4'd8:    half_period_base = 16'd30309;  // G#4
      4'd9:    half_period_base = 16'd28608;  // A4 (440 Hz)
      4'd10:   half_period_base = 16'd27002;  // A#4
      4'd11:   half_period_base = 16'd25487;  // B4
      default: half_period_base = 16'd48113;
    endcase
  end

  // Octave up = halve the period. Track bit 0 shifts the whole song up an octave.
  wire [ 1:0] oct_shift   = {1'b0, note[4]} + {1'b0, track[0]};
  wire [15:0] half_period = half_period_base >> oct_shift;

  reg  [15:0] osc_cnt;
  reg         osc_out;
  reg  [ 3:0] pwm_cnt;
  reg         audio;

  always @(posedge clk) begin
    if (!rst_n) begin
      osc_cnt <= 16'd0;
      osc_out <= 1'b0;
      pwm_cnt <= 4'd0;
      audio   <= 1'b0;
    end else begin
      // ">=" (not "==") so a sudden jump to a shorter period never overflows
      if (osc_cnt >= half_period - 16'd1) begin
        osc_cnt <= 16'd0;
        osc_out <= ~osc_out;
      end else begin
        osc_cnt <= osc_cnt + 16'd1;
      end
      pwm_cnt <= pwm_cnt + 4'd1;
      // Volume: gate the square wave with a ~1.5 MHz PWM whose duty is the envelope.
      audio   <= osc_out && (pwm_cnt < env);
    end
  end

  // ===========================================================================
  // Color themes (6-bit RRGGBB), one per track
  // ===========================================================================
  reg [5:0] c_low, c_mid, c_high, c_acc, c_bg;
  always @(*) begin
    case (track)
      2'd0: begin  // neon
        c_low = 6'b00_11_00; c_mid = 6'b11_11_00; c_high = 6'b11_00_00;
        c_acc = 6'b00_11_11; c_bg  = 6'b00_00_01;
      end
      2'd1: begin  // sunset
        c_low = 6'b11_01_00; c_mid = 6'b11_00_10; c_high = 6'b11_11_10;
        c_acc = 6'b11_10_00; c_bg  = 6'b01_00_00;
      end
      2'd2: begin  // ocean
        c_low = 6'b00_01_11; c_mid = 6'b00_11_11; c_high = 6'b11_11_11;
        c_acc = 6'b00_10_11; c_bg  = 6'b00_01_01;
      end
      default: begin  // violet
        c_low = 6'b01_00_11; c_mid = 6'b10_00_11; c_high = 6'b11_10_11;
        c_acc = 6'b11_00_11; c_bg  = 6'b01_00_01;
      end
    endcase
  end

  localparam [5:0] WHITE = 6'b11_11_11;
  localparam [5:0] GRAY  = 6'b10_10_10;
  localparam [5:0] DIM   = 6'b01_01_01;

  // Song time: advances 1 per frame while playing (drives animation + progress).
  wire [7:0] t = {step, frame_div};

  // ===========================================================================
  // Title: "CHROMA" in a 5x7 font scaled 4x
  // ===========================================================================
  wire [9:0] gx = pix_x - 10'd230;
  wire [9:0] gy = pix_y - 10'd24;
  wire in_title_box = (pix_x >= 10'd230) && (pix_x < 10'd422) &&
                      (pix_y >= 10'd24)  && (pix_y < 10'd52);
  wire [2:0] glyph = gx[7:5];
  wire [2:0] gcol  = gx[4:2];
  wire [2:0] grow  = gy[4:2];

  reg [34:0] glyph_bits;
  always @(*) begin
    case (glyph)
      3'd0:    glyph_bits = 35'b01110_10001_10000_10000_10000_10001_01110;  // C
      3'd1:    glyph_bits = 35'b10001_10001_10001_11111_10001_10001_10001;  // H
      3'd2:    glyph_bits = 35'b11110_10001_10001_11110_10100_10010_10001;  // R
      3'd3:    glyph_bits = 35'b01110_10001_10001_10001_10001_10001_01110;  // O
      3'd4:    glyph_bits = 35'b10001_11011_10101_10101_10001_10001_10001;  // M
      3'd5:    glyph_bits = 35'b01110_10001_10001_11111_10001_10001_10001;  // A
      default: glyph_bits = 35'd0;
    endcase
  end

  wire [34:0] glyph_shifted = glyph_bits >> (5'd30 - {grow, 2'b00} - {2'b00, grow});  // row 0 = top
  wire [ 4:0] glyph_row     = glyph_shifted[4:0];
  wire title_px = in_title_box && (gcol < 3'd5) && glyph_row[3'd4 - gcol];
  // Diagonal shine that sweeps across the title in time with the song.
  wire [6:0] shine = gx[6:0] + gy[6:0] - {t[5:0], 1'b0};
  wire title_shine = (shine[6:3] == 4'd0);

  // ===========================================================================
  // Visualizer: 16 bars x 16 LED segments
  // ===========================================================================
  wire [9:0] vx = pix_x - 10'd64;          // 0..511 inside the visualizer
  wire [9:0] ry = 10'd335 - pix_y;         // 0..255 going UP from the baseline
  wire in_vis = (pix_x >= 10'd64) && (pix_x < 10'd576) &&
                (pix_y >= 10'd80) && (pix_y < 10'd336);
  wire [3:0] bar      = vx[8:5];
  wire       bar_body = (vx[4:2] != 3'd0) && (vx[4:2] != 3'd7);  // 24 px bar, 8 px gap
  wire [3:0] seg      = ry[7:4];
  wire       seg_body = (ry[3:2] != 2'd0);                        // 12 px segment, 4 px gap

  // The tallest bar sits at a position set by the note's pitch; neighbours fall off
  // with distance, the whole shape shrinks as the envelope decays.
  wire [3:0] center = note[3:0] + 4'd2 + {2'b00, note[4], 1'b0};
  wire [3:0] dist   = (bar > center) ? (bar - center) : (center - bar);
  wire [3:0] level  = (env > dist) ? (env - dist) : 4'd0;
  wire [1:0] jitter = lfsr[1:0] ^ bar[1:0] ^ lfsr[5:4];
  wire [4:0] h_raw  = {1'b0, level} + ((level != 4'd0) ? {3'b000, jitter} : 5'd0);
  wire [3:0] height = h_raw[4] ? 4'd15 : h_raw[3:0];

  wire       seg_lit   = (seg < height);
  wire [5:0] seg_color = (seg >= 4'd12) ? c_high : (seg >= 4'd8) ? c_mid : c_low;
  wire       ghost     = pix_x[0] ^ pix_y[0];  // checkerboard dither for unlit segments
  wire       vis_on    = in_vis && bar_body && seg_body && (seg_lit || ghost);
  wire [5:0] vis_color = seg_lit ? seg_color : DIM;

  // ===========================================================================
  // Progress bar with a playhead knob
  // ===========================================================================
  wire in_prog_x = (pix_x >= 10'd64) && (pix_x < 10'd576);
  wire in_track  = in_prog_x && (pix_y >= 10'd392) && (pix_y < 10'd400);
  wire knob      = in_prog_x && (vx[8:2] == t[7:1]) && (pix_y >= 10'd386) && (pix_y < 10'd406);
  wire played    = (vx[8:1] < t);

  // ===========================================================================
  // Controls row: track dots, play/pause, next
  // ===========================================================================
  wire [9:0] cy = pix_y - 10'd424;
  wire in_ctrl_y = (pix_y >= 10'd424) && (pix_y < 10'd456);
  wire [4:0] dy  = cy[4:0];

  // Play (shown when paused) / pause (shown when playing) icon, x in [304, 336)
  wire [9:0] px_play = pix_x - 10'd304;
  wire [4:0] dxp     = px_play[4:0];
  wire in_play_box   = in_ctrl_y && (pix_x >= 10'd304) && (pix_x < 10'd336);
  wire tri_play      = ({1'b0, dxp[4:1]} <= dy) && ({1'b0, dxp[4:1]} <= (5'd31 - dy));
  wire pause_bars    = ((dxp >= 5'd6) && (dxp < 5'd13)) || ((dxp >= 5'd19) && (dxp < 5'd26));
  wire play_px       = in_play_box && (pause_in ? tri_play : pause_bars);

  // Next-track icon, x in [368, 400)
  wire [9:0] px_next = pix_x - 10'd368;
  wire [4:0] dxn     = px_next[4:0];
  wire in_next_box   = in_ctrl_y && (pix_x >= 10'd368) && (pix_x < 10'd400);
  wire next_px       = in_next_box &&
                       (((dxn < 5'd22) && ({1'b0, dxn[4:1]} <= dy) && ({1'b0, dxn[4:1]} <= (5'd31 - dy))) ||
                        ((dxn >= 5'd24) && (dxn < 5'd30) && (dy >= 5'd6) && (dy < 5'd26)));

  // Four track dots, x in [208, 272)
  wire [9:0] px_dots = pix_x - 10'd208;
  wire in_dots       = in_ctrl_y && (pix_x >= 10'd208) && (pix_x < 10'd272) &&
                       (px_dots[3:0] < 4'd10) && (dy >= 5'd11) && (dy < 5'd21);
  wire dot_sel       = (px_dots[5:4] == track);

  // ===========================================================================
  // Background: dithered vertical gradient in the theme color
  // ===========================================================================
  // 4x4 ordered (Bayer) dither: 16 brightness levels from only 1 bit of color.
  wire [3:0] bayer  = {pix_x[0] ^ pix_y[0], pix_y[0], pix_x[1] ^ pix_y[1], pix_y[1]};
  wire       bg_on  = (pix_y[8:5] > bayer);

  // ===========================================================================
  // Compose the final pixel (front-most layer first)
  // ===========================================================================
  reg [5:0] color;
  always @(*) begin
    if (!video_active)   color = 6'b00_00_00;
    else if (title_px)   color = title_shine ? WHITE : c_acc;
    else if (vis_on)     color = vis_color;
    else if (knob)       color = WHITE;
    else if (in_track)   color = played ? c_acc : DIM;
    else if (play_px)    color = WHITE;
    else if (next_px)    color = next_in ? WHITE : GRAY;
    else if (in_dots)    color = dot_sel ? c_acc : DIM;
    else if (bg_on)      color = c_bg;
    else                 color = 6'b00_00_00;
  end

  // Register the pixel so it lines up with the (registered) sync signals and
  // the output pins never glitch.
  reg [5:0] rgb_q;
  always @(posedge clk) begin
    if (!rst_n) rgb_q <= 6'd0;
    else        rgb_q <= color;
  end

  wire [1:0] R = rgb_q[5:4];
  wire [1:0] G = rgb_q[3:2];
  wire [1:0] B = rgb_q[1:0];

  // TinyVGA PMOD pinout
  assign uo_out  = {hsync, B[0], G[0], R[0], vsync, B[1], G[1], R[1]};
  // Audio PMOD on uio[7]
  assign uio_out = {audio, 7'b0000000};
  assign uio_oe  = 8'b1000_0000;

  wire _unused = &{ena, uio_in, ui_in[7:2], vx[9], ry[9:8], gx[9:8], gy[9:5], cy[9:5],
                   px_play[9:5], px_next[9:5], px_dots[9:6], glyph_shifted[34:5], 1'b0};

endmodule

// =============================================================================
// 32-step melody (original). Entry = {play, octave, semitone}; play=0 is a rest.
// =============================================================================
module melody_rom (
    input  wire [4:0] addr,
    output reg  [5:0] data
);
  localparam [5:0] REST = 6'b0_0_0000;
  localparam [5:0] E4 = 6'b1_0_0100, G4 = 6'b1_0_0111, A4 = 6'b1_0_1001;
  localparam [5:0] C5 = 6'b1_1_0000, D5 = 6'b1_1_0010, E5 = 6'b1_1_0100, G5 = 6'b1_1_0111;

  always @(*) begin
    case (addr)
      5'd0:  data = A4;   5'd1:  data = REST; 5'd2:  data = C5;   5'd3:  data = E5;
      5'd4:  data = D5;   5'd5:  data = REST; 5'd6:  data = C5;   5'd7:  data = A4;
      5'd8:  data = G4;   5'd9:  data = REST; 5'd10: data = A4;   5'd11: data = C5;
      5'd12: data = D5;   5'd13: data = E5;   5'd14: data = REST; 5'd15: data = REST;
      5'd16: data = A4;   5'd17: data = REST; 5'd18: data = C5;   5'd19: data = E5;
      5'd20: data = G5;   5'd21: data = E5;   5'd22: data = D5;   5'd23: data = C5;
      5'd24: data = D5;   5'd25: data = REST; 5'd26: data = C5;   5'd27: data = A4;
      5'd28: data = E4;   5'd29: data = G4;   5'd30: data = A4;   default: data = REST;
    endcase
  end
endmodule
