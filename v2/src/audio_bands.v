// Real-time 8-band spectrum analyser.
//
// 1. I2S receiver (left channel, top 16 bits of a 32-bit slot).
// 2. Eight state-variable band-pass filters (Chamberlin SVF, Q = 4), one per band:
//        low  += f * band
//        high  = x - low - band/4
//        band += f * high
//    Every filter shares ONE serial shift-and-add multiplier (16 clk per multiply),
//    so the whole bank costs ~300 clk per audio sample out of ~1000 available.
// 3. A peak follower per band, converted to a 0..15 level from its leading one plus
//    the next bit (~3 dB per level), latched and decayed once per video frame.
`default_nettype none
module audio_bands (
    input  wire        clk,
    input  wire        rst,
    input  wire        bclk_in,
    input  wire        lrclk_in,
    input  wire        din_in,
    input  wire        frame_tick,   // latch levels and decay the peaks
    output wire [31:0] levels        // 8 x 4-bit, band 0 (lowest) in [3:0]
);
  // ------------------------------------------------------------------ I2S receiver
  reg [2:0] bclk_s;
  reg [1:0] lr_s, din_s;
  reg       lr_prev;
  reg [30:0] i2s_sh;
  reg signed [15:0] sample;
  reg        sample_valid;
  wire       bclk_rise = bclk_s[1] & ~bclk_s[2];
  wire [31:0] word = {i2s_sh, din_s[1]};

  always @(posedge clk) begin
    sample_valid <= 1'b0;
    if (rst) begin
      bclk_s  <= 3'd0;
      lr_s    <= 2'd0;
      din_s   <= 2'd0;
      lr_prev <= 1'b0;
      i2s_sh  <= 31'd0;
      sample  <= 16'sd0;
    end else begin
      bclk_s <= {bclk_s[1:0], bclk_in};
      lr_s   <= {lr_s[0], lrclk_in};
      din_s  <= {din_s[0], din_in};
      if (bclk_rise) begin
        i2s_sh  <= word[30:0];
        lr_prev <= lr_s[1];
        // LRCLK changed: this bit is the LSB of the previous word (I2S 1-bit delay).
        if (lr_s[1] != lr_prev && lr_prev == 1'b0) begin
          sample       <= word[31:16];
          sample_valid <= 1'b1;
        end
      end
    end
  end

  // ------------------------------------------------------------------ coefficients
  // f = 2*sin(pi*fc/48000) in Q2.14 for fc = 60,150,350,800,1600,3000,5000,8000 Hz
  reg [2:0] b;  // current band
  reg [15:0] fcoef;
  always @(*) begin
    case (b)
      3'd0: fcoef = 16'd129;
      3'd1: fcoef = 16'd322;
      3'd2: fcoef = 16'd751;
      3'd3: fcoef = 16'd1715;
      3'd4: fcoef = 16'd3425;
      3'd5: fcoef = 16'd6393;
      3'd6: fcoef = 16'd10533;
      default: fcoef = 16'd16384;
    endcase
  end

  // ------------------------------------------------------------------ filter bank
  reg signed [25:0] low  [0:7];
  reg signed [25:0] band [0:7];
  reg        [19:0] peak [0:7];
  reg        [ 3:0] lvl  [0:7];

  localparam A_IDLE = 3'd0, A_MUL1 = 3'd1, A_LOW = 3'd2, A_MUL2 = 3'd3, A_BAND = 3'd4, A_PEAK = 3'd5,
             A_HIGH = 3'd6;
  reg [2:0]  ast;
  reg [3:0]  mcnt;
  reg signed [25:0] ma;      // multiplicand
  reg signed [41:0] acc;     // product accumulator
  reg signed [25:0] x;       // current input sample, scaled
  reg        decay_req, decay_now;

  wire signed [25:0] prod = acc[39:14];  // (ma * f) >> 14
  wire signed [25:0] low_b  = low[b];
  wire signed [25:0] band_b = band[b];
  wire signed [25:0] high   = x - low_b - (band_b >>> 2);
  wire        [25:0] mag    = band_b[25] ? -band_b : band_b;
  wire        [19:0] m20    = mag[25:6];

  // leading-one position (+ the bit below it) of peak[b] -> level 0..15, ~3 dB/step
  reg [4:0] pos;
  reg       mbit;
  integer k;
  always @(*) begin
    pos  = 5'd0;
    mbit = 1'b0;
    for (k = 0; k < 20; k = k + 1)
      if (peak[b][k]) begin
        pos  = k[4:0] + 5'd1;
        mbit = (k > 0) ? peak[b][k - 1] : 1'b0;
      end
  end
  wire [5:0] l2      = {pos, 1'b0} + {5'd0, mbit};
  wire [5:0] l2o     = l2 - 6'd19;
  wire [3:0] lvl_now = (l2 > 6'd19) ? ((l2o > 6'd15) ? 4'd15 : l2o[3:0]) : 4'd0;
  wire [19:0] peak_dec = peak[b] - (peak[b] >> 2);
  wire [19:0] peak_base = decay_now ? peak_dec : peak[b];

  integer i;
  always @(posedge clk) begin
    if (rst) begin
      ast       <= A_IDLE;
      b         <= 3'd0;
      mcnt      <= 4'd0;
      ma        <= 26'sd0;
      acc       <= 42'sd0;
      x         <= 26'sd0;
      decay_req <= 1'b0;
      decay_now <= 1'b0;
      for (i = 0; i < 8; i = i + 1) begin
        low[i]  <= 26'sd0;
        band[i] <= 26'sd0;
        peak[i] <= 20'd0;
        lvl[i]  <= 4'd0;
      end
    end else begin
      if (frame_tick) decay_req <= 1'b1;
      case (ast)
        A_IDLE: if (sample_valid) begin
          x         <= {{4{sample[15]}}, sample, 6'd0};
          b         <= 3'd0;
          decay_now <= decay_req;
          if (!frame_tick) decay_req <= 1'b0;
          ma        <= band[0];
          acc       <= 42'sd0;
          mcnt      <= 4'd0;
          ast       <= A_MUL1;
        end
        A_MUL1, A_MUL2: begin  // MSB-first shift-and-add, 16 cycles
          acc  <= (acc <<< 1) + (fcoef[4'd15 - mcnt] ? {{16{ma[25]}}, ma} : 42'sd0);
          mcnt <= mcnt + 4'd1;
          if (mcnt == 4'd15) ast <= (ast == A_MUL1) ? A_LOW : A_BAND;
        end
        A_LOW: begin
          low[b] <= low_b + prod;
          acc    <= 42'sd0;
          mcnt   <= 4'd0;
          ast    <= A_HIGH;          // one cycle for low[b] to update before using it
        end
        A_HIGH: begin                // next multiply: f * high
          ma  <= high;
          ast <= A_MUL2;
        end
        A_BAND: begin
          band[b] <= band_b + prod;
          ast     <= A_PEAK;
        end
        A_PEAK: begin
          if (decay_now) lvl[b] <= lvl_now;
          peak[b] <= (m20 > peak_base) ? m20 : peak_base;
          if (b == 3'd7) begin
            ast <= A_IDLE;
          end else begin
            b    <= b + 3'd1;
            ma   <= band[b + 3'd1];
            acc  <= 42'sd0;
            mcnt <= 4'd0;
            ast  <= A_MUL1;
          end
        end
        default: ast <= A_IDLE;
      endcase
    end
  end

  assign levels = {lvl[7], lvl[6], lvl[5], lvl[4], lvl[3], lvl[2], lvl[1], lvl[0]};
endmodule
