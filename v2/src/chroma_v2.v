/*
 * CHROMA v2 - display + audio-visualiser chip for a Spotify music player
 *
 * The host (a Raspberry Pi running a Spotify Connect client) sends the song's
 * metadata and cover art over SPI, and a copy of the audio over I2S. CHROMA v2
 * renders the whole "now playing" screen on a 240x320 LCD: the cover spinning
 * as a vinyl record (texture-mapped from external PSRAM), title + artist text,
 * an 8-band hardware spectrum analyser, beat-reactive colour and a progress bar.
 *
 * Pinout (Tiny Tapeout style):
 *   ui[0] SPI_SCK   ui[1] SPI_MOSI  ui[2] SPI_CS_N
 *   ui[3] I2S_BCLK  ui[4] I2S_LRCLK ui[5] I2S_DIN
 *   uo[7:0]  LCD D[7:0]
 *   uio[0] LCD_WR_N  uio[1] LCD_DC  uio[2] PSRAM_CS_N  uio[3] PSRAM_SCK
 *   uio[7:4] PSRAM IO[3:0] (bidirectional)
 *
 * SPI commands (first byte after CS goes low):
 *   0x01 STATE  flags(bit0 = playing), progress(0-255), accent_hi, accent_lo (RGB565)
 *   0x02 TEXT   line(0 = title, 1 = artist), 18 x 6-bit character codes
 *   0x03 COVER  128x128 RGB565 pixels, big-endian, row-major (32768 bytes)
 *
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none
module tt_um_chroma_v2 #(
    parameter SLP_WAIT_BITS = 23
) (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,      // 50 MHz
    input  wire       rst_n
);
  wire rst = ~rst_n;

  // ------------------------------------------------------------------ SPI command input
  wire       sb_valid, cs_start, cs_end;
  wire [7:0] sb;
  spi_slave spi (
      .clk(clk), .rst(rst), .sck_in(ui_in[0]), .mosi_in(ui_in[1]), .csn_in(ui_in[2]),
      .byte_valid(sb_valid), .byte_data(sb), .cs_start(cs_start), .cs_end(cs_end)
  );

  reg        playing;
  reg [7:0]  progress;
  reg [15:0] accent;
  reg [7:0]  acc_hi;
  reg [7:0]  cmd;
  reg        have_cmd;
  reg [14:0] bidx;        // byte index within the command payload
  reg        tline_w;
  reg [5:0]  text0 [0:17];
  reg [5:0]  text1 [0:17];
  reg        cover_loading, cover_valid;
  reg [7:0]  cov_hi;
  reg        wpend;
  reg [23:0] waddr;
  reg [15:0] wdat;

  wire ps_busy, ps_ready, ps_rvalid;
  wire [15:0] ps_rdata;
  wire ps_idle  = ps_ready && !ps_busy;
  wire wr_issue = wpend && ps_idle;
  wire rd_ok    = ps_idle && !wpend;

  integer i;
  always @(posedge clk) begin
    if (rst) begin
      playing <= 1'b0; progress <= 8'd0; accent <= 16'h07FF; acc_hi <= 8'd0;
      cmd <= 8'd0; have_cmd <= 1'b0; bidx <= 15'd0; tline_w <= 1'b0;
      cover_loading <= 1'b0; cover_valid <= 1'b0; cov_hi <= 8'd0;
      wpend <= 1'b0; waddr <= 24'd0; wdat <= 16'd0;
      for (i = 0; i < 18; i = i + 1) begin
        text0[i] <= 6'd0;
        text1[i] <= 6'd0;
      end
    end else begin
      if (wr_issue) wpend <= 1'b0;
      if (cs_start) begin
        have_cmd <= 1'b0;
        bidx     <= 15'd0;
      end
      if (cs_end && cmd == 8'h03 && have_cmd) begin
        cover_loading <= 1'b0;
        cover_valid   <= 1'b1;
      end
      if (sb_valid) begin
        if (!have_cmd) begin
          cmd      <= sb;
          have_cmd <= 1'b1;
          bidx     <= 15'd0;
          if (sb == 8'h03) begin
            cover_loading <= 1'b1;
            cover_valid   <= 1'b0;
          end
        end else begin
          bidx <= bidx + 15'd1;
          case (cmd)
            8'h01: begin
              if (bidx == 15'd0) playing  <= sb[0];
              if (bidx == 15'd1) progress <= sb;
              if (bidx == 15'd2) acc_hi   <= sb;
              if (bidx == 15'd3) accent   <= {acc_hi, sb};
            end
            8'h02: begin
              if (bidx == 15'd0) tline_w <= sb[0];
              else if (bidx <= 15'd18) begin
                if (tline_w) text1[bidx - 15'd1] <= sb[5:0];
                else         text0[bidx - 15'd1] <= sb[5:0];
              end
            end
            8'h03: begin
              if (!bidx[0]) cov_hi <= sb;
              else begin
                wpend <= 1'b1;
                waddr <= {9'd0, bidx[14:1], 1'b0};
                wdat  <= {cov_hi, sb};
              end
            end
            default: ;
          endcase
        end
      end
    end
  end

  // ------------------------------------------------------------------ PSRAM
  wire        rd_req;
  wire [23:0] rd_addr;
  wire        ps_cs_n, ps_sck;
  wire [3:0]  ps_io_o, ps_io_oe;
  psram_qpi psram (
      .clk(clk), .rst(rst),
      .req(wr_issue | rd_req), .we(wr_issue), .addr(wr_issue ? waddr : rd_addr), .wdata(wdat),
      .busy(ps_busy), .ready(ps_ready), .rvalid(ps_rvalid), .rdata(ps_rdata),
      .cs_n(ps_cs_n), .sck(ps_sck), .io_o(ps_io_o), .io_oe(ps_io_oe), .io_i(uio_in[7:4])
  );

  // ------------------------------------------------------------------ audio analyser
  wire        frame_tick;
  wire [31:0] levels;
  audio_bands audio (
      .clk(clk), .rst(rst), .bclk_in(ui_in[3]), .lrclk_in(ui_in[4]), .din_in(ui_in[5]),
      .frame_tick(frame_tick), .levels(levels)
  );

  // ------------------------------------------------------------------ renderer
  wire       tline;
  wire [4:0] tidx;
  wire [4:0] tsafe = (tidx > 5'd17) ? 5'd0 : tidx;          // keep the read in range
  wire [5:0] tchar = (tidx > 5'd17) ? 6'd0 : (tline ? text1[tsafe] : text0[tsafe]);
  wire [7:0] lcd_d;
  wire       lcd_wr_n, lcd_dc;
  renderer #(.SLP_WAIT_BITS(SLP_WAIT_BITS)) render (
      .clk(clk), .rst(rst),
      .playing(playing), .progress(progress), .accent(accent),
      .cover_ok(cover_valid && !cover_loading),
      .tline(tline), .tidx(tidx), .tchar(tchar),
      .levels(levels), .frame_tick(frame_tick),
      .rd_ok(rd_ok), .rd_req(rd_req), .rd_addr(rd_addr), .rvalid(ps_rvalid), .rdata(ps_rdata),
      .lcd_d(lcd_d), .lcd_wr_n(lcd_wr_n), .lcd_dc(lcd_dc)
  );

  assign uo_out  = lcd_d;
  assign uio_out = {ps_io_o, ps_sck, ps_cs_n, lcd_dc, lcd_wr_n};
  assign uio_oe  = {ps_io_oe, 4'b1111};

  wire _unused = &{ena, ui_in[7:6], uio_in[3:0], 1'b0};
endmodule
