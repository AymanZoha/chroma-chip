// CHROMA v2 renderer: drives an ST7789-class 240x320 LCD over an 8-bit 8080 bus.
//
// Like v1 it has no framebuffer: every frame it walks the screen in raster order,
// computes each pixel's colour, and streams it to the LCD's own frame memory.
// New in v2:
//   * a vinyl record whose label is the album cover, ROTATED in real time by a
//     texture-mapping unit (incremental DDA: two adds per pixel, no multipliers),
//     with texels fetched from external PSRAM
//   * song title + artist text from an on-chip font ROM
//   * an 8-band spectrum fed by the hardware filter bank, with falling peaks
//   * beat-reactive background, progress bar, pause icon
// All circle tests use r^2 updated incrementally ((d+1)^2 = d^2 + 2d + 1).
`default_nettype none
module renderer #(
    parameter SLP_WAIT_BITS = 23        // ~168 ms at 50 MHz after SLPOUT (datasheet: 120 ms)
) (
    input  wire        clk,
    input  wire        rst,
    // state from the host
    input  wire        playing,
    input  wire [ 7:0] progress,
    input  wire [15:0] accent,
    input  wire        cover_ok,
    // text buffer read port
    output wire        tline,
    output wire [ 4:0] tidx,
    input  wire [ 5:0] tchar,
    // audio
    input  wire [31:0] levels,
    output reg         frame_tick,
    // PSRAM read port
    input  wire        rd_ok,
    output wire        rd_req,
    output wire [23:0] rd_addr,
    input  wire        rvalid,
    input  wire [15:0] rdata,
    // LCD 8080 bus
    output reg  [ 7:0] lcd_d,
    output reg         lcd_wr_n,
    output reg         lcd_dc
);
  // ------------------------------------------------------------------ command ROM
  // 0: SLPOUT | 1-5: COLMOD=565, MADCTL, DISPON | 6-16: CASET 0..239, RASET 0..319, RAMWR
  reg [4:0] ridx;
  reg [8:0] rom;  // {dc, byte}
  always @(*) begin
    case (ridx)
      5'd0:  rom = {1'b0, 8'h11};
      5'd1:  rom = {1'b0, 8'h3A};
      5'd2:  rom = {1'b1, 8'h55};
      5'd3:  rom = {1'b0, 8'h36};
      5'd4:  rom = {1'b1, 8'h00};
      5'd5:  rom = {1'b0, 8'h29};
      5'd6:  rom = {1'b0, 8'h2A};
      5'd7:  rom = {1'b1, 8'h00};
      5'd8:  rom = {1'b1, 8'h00};
      5'd9:  rom = {1'b1, 8'h00};
      5'd10: rom = {1'b1, 8'hEF};
      5'd11: rom = {1'b0, 8'h2B};
      5'd12: rom = {1'b1, 8'h00};
      5'd13: rom = {1'b1, 8'h00};
      5'd14: rom = {1'b1, 8'h01};
      5'd15: rom = {1'b1, 8'h3F};
      default: rom = {1'b0, 8'h2C};
    endcase
  end

  // ------------------------------------------------------------------ state
  localparam R_PWR = 4'd0, R_CMD0 = 4'd1, R_SLP = 4'd2, R_SEND = 4'd3, R_PIX = 4'd4, R_TEX = 4'd5,
             R_WB = 4'd6, R_NEXT = 4'd7, R_FEND = 4'd8, R_FSIN = 4'd9, R_FCOS = 4'd10, R_FUV = 4'd11;
  reg [3:0]  st;
  reg [23:0] wcnt;
  reg [7:0]  x;
  reg [8:0]  y;
  reg signed [18:0] dx2, dy2;
  reg [4:0]  cc;
  reg [3:0]  sx;
  reg [2:0]  bx;
  reg [4:0]  bs;
  reg signed [23:0] u, v, urow, vrow;   // texture coordinates, Q9.14
  reg signed [15:0] cs_c, cs_s;          // cos / sin of the rotation angle, Q1.14
  reg [7:0]  angle, lut_angle;
  reg [3:0]  disp [0:7];
  reg [3:0]  bass_prev;
  reg [2:0]  beat;
  reg [15:0] pix;
  reg        hi_sent;
  reg        wbusy;
  reg [1:0]  wph;

  wire signed [15:0] lut_val;
  sin_lut lut (.angle(lut_angle), .value(lut_val));

  // ------------------------------------------------------------------ helpers
  function [15:0] scale565(input [15:0] c, input [4:0] k);  // c * k / 32
    reg [9:0] r; reg [10:0] g; reg [9:0] b;
    begin
      r = c[15:11] * k;
      g = c[10:5] * k;
      b = c[4:0] * k;
      scale565 = {r[9:5], g[10:5], b[9:5]};
    end
  endfunction

  function [15:0] blend_white(input [15:0] c);  // 50% mix with white
    blend_white = ((c >> 1) & 16'h7BEF) + 16'h7BEF;
  endfunction

  // ------------------------------------------------------------------ geometry
  wire signed [10:0] dx = $signed({3'b000, x}) - 11'sd120;   // record centre (120,118)
  wire signed [10:0] dy = $signed({2'b00, y}) - 11'sd118;
  wire [18:0] r2 = dx2 + dy2;
  wire in_disc  = r2 < 19'd10000;   // r < 100
  wire in_hole  = r2 < 19'd25;      // spindle hole
  wire in_label = r2 < 19'd3844;    // r < 62: album cover
  wire in_lrim  = r2 < 19'd4096;    // r < 64: accent ring
  wire in_rim   = r2 >= 19'd9604;   // r > 98: outer edge
  wire in_box   = (x >= 8'd56) && (x < 8'd184) && (y >= 9'd54) && (y < 9'd182);

  wire signed [10:0] sd  = dx + dy;                    // diagonal sheen
  wire [10:0] sda = sd[10] ? -sd : sd;
  wire [3:0]  sheen = (sda < 11'd10) ? (4'd10 - sda[3:0]) : 4'd0;
  wire [4:0]  vin = in_rim ? 5'd12 : (5'd3 + {4'd0, r2[7]} + {1'b0, sheen});
  wire [15:0] vinyl = {vin, vin, 1'b0, vin};

  wire [10:0] adx = dx[10] ? -dx : dx;
  wire pause_icon = !playing && (dy > -11'sd13) && (dy < 11'sd13) && (adx >= 11'd4) && (adx <= 11'd10);

  wire need_tex = in_label && in_box && !in_hole && !pause_icon && cover_ok;
  wire [15:0] label_ph = r2[8] ? scale565(accent, 5'd6) : scale565(accent, 5'd10);

  assign rd_req  = (st == R_PIX) && need_tex && rd_ok;
  assign rd_addr = {9'd0, v[20:14], u[20:14], 1'b0};

  // text
  wire in_t0 = (y >= 9'd228) && (y < 9'd242);
  wire in_t1 = (y >= 9'd248) && (y < 9'd262);
  wire [8:0] ty = in_t1 ? (y - 9'd248) : (y - 9'd228);
  wire [4:0] fbits;
  assign tline = in_t1;
  assign tidx  = cc;
  font_rom font (.code(tchar), .row(ty[3:1]), .pixels(fbits));
  wire [2:0] gcol = sx[3:1];
  wire text_px = (in_t0 || in_t1) && (x >= 8'd12) && (x < 8'd228) && (gcol < 3'd5) && fbits[3'd4 - gcol];

  // spectrum
  wire in_spec  = (y >= 9'd272) && (y < 9'd304);
  wire [8:0] hyw = 9'd303 - y;
  wire [4:0] hy  = hyw[4:0];
  wire bar_body = (bs >= 5'd3) && (bs < 5'd27);
  wire [3:0] lvlb = disp[bx];
  wire seg_gap  = (hy[1:0] == 2'd3);
  wire lit      = hy < {lvlb, 1'b0};
  wire [15:0] bar_col = (hy >= 5'd22) ? 16'hFFFF : (hy >= 5'd12) ? blend_white(accent) : accent;

  // progress bar
  wire in_prog = (y >= 9'd310) && (y < 9'd314) && (x >= 8'd8) && (x < 8'd232);
  wire [7:0] prog_px = progress - (progress >> 3);
  wire filled = (x - 8'd8) < prog_px;

  // background
  wire [4:0] bgk = {2'b00, y[8:6]} + {2'b00, beat};

  reg [15:0] col;
  always @(*) begin
    if (in_disc) begin
      if (in_hole)         col = 16'h0000;
      else if (pause_icon) col = 16'hFFFF;
      else if (in_label)   col = label_ph;
      else if (in_lrim)    col = accent;
      else                 col = vinyl;
    end else if (text_px)  col = in_t1 ? 16'hAD55 : 16'hFFFF;
    else if (in_spec && bar_body && !seg_gap) col = lit ? bar_col : scale565(accent, 5'd3);
    else if (in_prog)      col = filled ? accent : 16'h2945;
    else                   col = scale565(accent, bgk);
  end

  // ------------------------------------------------------------------ sequencer
  wire [7:0] angle_next = angle + (playing ? 8'd3 : 8'd0);   // ~33 rpm at ~45 fps
  wire [3:0] lv0 = levels[3:0], lv1 = levels[7:4];
  wire [3:0] bass = (lv0 > lv1) ? lv0 : lv1;

  integer i;
  always @(posedge clk) begin
    frame_tick <= 1'b0;
    if (rst) begin
      st        <= R_PWR;
      wcnt      <= 24'd0;
      ridx      <= 5'd0;
      lcd_d     <= 8'd0;
      lcd_wr_n  <= 1'b1;
      lcd_dc    <= 1'b0;
      wbusy     <= 1'b0;
      wph       <= 2'd0;
      x <= 8'd0; y <= 9'd0; dx2 <= 19'sd14400; dy2 <= 19'sd13924;
      cc <= 5'd0; sx <= 4'd0; bx <= 3'd0; bs <= 5'd0;
      u <= 24'sd0; v <= 24'sd0; urow <= 24'sd0; vrow <= 24'sd0;
      cs_c <= 16'sd16384; cs_s <= 16'sd0;
      angle <= 8'd0; lut_angle <= 8'd0;
      bass_prev <= 4'd0; beat <= 3'd0;
      pix <= 16'd0; hi_sent <= 1'b0;
      for (i = 0; i < 8; i = i + 1) disp[i] <= 4'd0;
    end else begin
      // byte writer: WR low 2 clk, high 2 clk (80 ns/byte at 50 MHz)
      if (wbusy) begin
        wph <= wph + 2'd1;
        if (wph == 2'd1) lcd_wr_n <= 1'b1;
        if (wph == 2'd2) wbusy <= 1'b0;
      end

      case (st)
        R_PWR: begin
          wcnt <= wcnt + 24'd1;
          if (wcnt[12]) st <= R_CMD0;
        end
        R_CMD0: if (!wbusy) begin                       // SLPOUT
          {lcd_dc, lcd_d} <= rom; lcd_wr_n <= 1'b0; wbusy <= 1'b1; wph <= 2'd0;
          ridx <= 5'd1; wcnt <= 24'd0; st <= R_SLP;
        end
        R_SLP: begin
          wcnt <= wcnt + 24'd1;
          if (wcnt[SLP_WAIT_BITS]) st <= R_SEND;
        end
        R_SEND: if (!wbusy) begin
          {lcd_dc, lcd_d} <= rom; lcd_wr_n <= 1'b0; wbusy <= 1'b1; wph <= 2'd0;
          ridx <= ridx + 5'd1;
          if (ridx == 5'd5) st <= R_FEND;
          else if (ridx == 5'd16) st <= R_PIX;
        end
        R_PIX: begin
          if (need_tex) begin
            if (rd_ok) st <= R_TEX;                    // rd_req is asserted this cycle
          end else begin
            pix <= col; hi_sent <= 1'b0; st <= R_WB;
          end
        end
        R_TEX: if (rvalid) begin
          pix <= rdata; hi_sent <= 1'b0; st <= R_WB;
        end
        R_WB: if (!wbusy) begin
          lcd_dc <= 1'b1; lcd_wr_n <= 1'b0; wbusy <= 1'b1; wph <= 2'd0;
          if (!hi_sent) begin
            lcd_d <= pix[15:8]; hi_sent <= 1'b1;
          end else begin
            lcd_d <= pix[7:0]; st <= R_NEXT;
          end
        end
        R_NEXT: begin
          if (x == 8'd239) begin
            x <= 8'd0;
            dx2 <= 19'sd14400;
            dy2 <= dy2 + (dy <<< 1) + 19'sd1;
            cc <= 5'd0; sx <= 4'd0; bx <= 3'd0; bs <= 5'd0;
            if (y >= 9'd54 && y < 9'd182) begin
              urow <= urow + cs_s;
              vrow <= vrow + cs_c;
            end
            if (y == 9'd319) begin
              y  <= 9'd0;
              st <= R_FEND;
            end else begin
              y  <= y + 9'd1;
              st <= R_PIX;
            end
          end else begin
            x   <= x + 8'd1;
            dx2 <= dx2 + (dx <<< 1) + 19'sd1;
            if (x == 8'd11) begin cc <= 5'd0; sx <= 4'd0; end
            else if (sx == 4'd11) begin sx <= 4'd0; cc <= cc + 5'd1; end
            else sx <= sx + 4'd1;
            if (bs == 5'd29) begin bs <= 5'd0; bx <= bx + 3'd1; end
            else bs <= bs + 5'd1;
            if (x == 8'd55) begin u <= urow; v <= vrow; end
            else begin u <= u + cs_c; v <= v - cs_s; end
            st <= R_PIX;
          end
        end
        R_FEND: begin                                   // once per frame
          frame_tick <= 1'b1;
          angle      <= angle_next;
          lut_angle  <= angle_next;
          for (i = 0; i < 8; i = i + 1)
            disp[i] <= (levels[4*i +: 4] > disp[i]) ? levels[4*i +: 4]
                     : ((disp[i] != 4'd0) ? disp[i] - 4'd1 : 4'd0);
          if ({1'b0, bass} >= {1'b0, bass_prev} + 5'd2 && bass >= 4'd7) beat <= 3'd7;
          else if (beat != 3'd0) beat <= beat - 3'd1;
          bass_prev <= bass;
          st <= R_FSIN;
        end
        R_FSIN: begin
          cs_s      <= lut_val;
          lut_angle <= lut_angle + 8'd64;               // cos = sin(angle + 90 deg)
          st        <= R_FCOS;
        end
        R_FCOS: begin
          cs_c <= lut_val;
          st   <= R_FUV;
        end
        R_FUV: begin
          // texel at the box's top-left (offset -64,-64 from centre), rotated
          urow <= 24'sd1048576 - (({{8{cs_c[15]}}, cs_c} + {{8{cs_s[15]}}, cs_s}) <<< 6);
          vrow <= 24'sd1048576 + (({{8{cs_s[15]}}, cs_s} - {{8{cs_c[15]}}, cs_c}) <<< 6);
          x <= 8'd0; y <= 9'd0; dx2 <= 19'sd14400; dy2 <= 19'sd13924;
          cc <= 5'd0; sx <= 4'd0; bx <= 3'd0; bs <= 5'd0;
          ridx <= 5'd6;
          st   <= R_SEND;
        end
        default: st <= R_PWR;
      endcase
    end
  end

  wire _unused = &{u[23:21], u[13:0], v[23:21], v[13:0], ty[8:4], ty[0], hyw[8:5], 1'b0};
endmodule
