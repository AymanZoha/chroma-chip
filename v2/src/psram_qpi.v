// Controller for an APS6404-style 8 MB QSPI PSRAM, used in QPI (4-bit) mode.
//
//   power-up: wait, then send 0x35 (Enter Quad Mode) in 1-bit SPI mode
//   read  16 bits: 0xEB, 24-bit address, 6 wait cycles, 4 data nibbles  (18 SCK)
//   write 16 bits: 0x38, 24-bit address, 4 data nibbles                 (12 SCK)
//
// SCK runs at clk/2. Every SCK period is two clk cycles: "low" (we drive the next
// nibble) then "high" (the memory samples it on the rising edge). The memory drives
// read data after the falling edge; we sample it as we raise SCK.
// Transactions are short (< 1 us), well inside the PSRAM's 8 us CE# low limit.
`default_nettype none
module psram_qpi (
    input  wire        clk,
    input  wire        rst,
    input  wire        req,      // start a transaction (only when !busy)
    input  wire        we,
    input  wire [23:0] addr,
    input  wire [15:0] wdata,
    output wire        busy,
    output reg         ready,    // initialisation finished
    output reg         rvalid,   // 1-cycle pulse, rdata valid
    output reg  [15:0] rdata,
    output reg         cs_n,
    output reg         sck,
    output reg  [ 3:0] io_o,
    output reg  [ 3:0] io_oe,
    input  wire [ 3:0] io_i
);
  localparam S_PWR = 3'd0, S_INIT = 3'd1, S_IDLE = 3'd2, S_XFER = 3'd3, S_END = 3'd4, S_GAP = 3'd5;

  reg [ 2:0] st;
  reg [12:0] dly;
  reg [ 4:0] bt;      // bit-time (SCK period) index
  reg        ph;      // 0 = drive SCK low, 1 = drive SCK high
  reg [47:0] sh;
  reg        is_wr;

  assign busy = (st != S_IDLE);
  wire [4:0] last_bt = (st == S_INIT) ? 5'd7 : (is_wr ? 5'd11 : 5'd17);

  always @(posedge clk) begin
    rvalid <= 1'b0;
    if (rst) begin
      st    <= S_PWR;
      dly   <= 13'd0;
      ready <= 1'b0;
      cs_n  <= 1'b1;
      sck   <= 1'b0;
      io_o  <= 4'd0;
      io_oe <= 4'd0;
      bt    <= 5'd0;
      ph    <= 1'b0;
      sh    <= 48'd0;
      is_wr <= 1'b0;
      rdata <= 16'd0;
    end else begin
      case (st)
        S_PWR: begin  // > 150 us power-up time
          dly <= dly + 13'd1;
          if (&dly) begin
            st   <= S_INIT;
            cs_n <= 1'b0;
            bt   <= 5'd0;
            ph   <= 1'b0;
            sh   <= {8'h35, 40'd0};
          end
        end

        S_INIT, S_XFER: begin
          if (!ph) begin
            sck <= 1'b0;
            if (st == S_INIT) begin
              io_o  <= {3'b000, sh[47]};
              io_oe <= 4'b0001;
            end else begin
              io_o  <= sh[47:44];
              io_oe <= (is_wr || bt < 5'd8) ? 4'b1111 : 4'b0000;  // turnaround for read data
            end
            ph <= 1'b1;
          end else begin
            sck <= 1'b1;
            sh  <= (st == S_INIT) ? {sh[46:0], 1'b0} : {sh[43:0], 4'd0};
            if (st == S_XFER && !is_wr && bt >= 5'd14) rdata <= {rdata[11:0], io_i};
            ph <= 1'b0;
            if (bt == last_bt) begin
              st <= S_END;
            end else begin
              bt <= bt + 5'd1;
            end
          end
        end

        S_END: begin
          sck    <= 1'b0;
          cs_n   <= 1'b1;
          io_oe  <= 4'b0000;
          ready  <= 1'b1;
          rvalid <= ready && !is_wr;  // no rvalid for the init command
          st     <= S_GAP;
        end

        S_GAP: st <= S_IDLE;

        S_IDLE: begin
          if (req) begin
            cs_n  <= 1'b0;
            sh    <= {(we ? 8'h38 : 8'hEB), addr, wdata};
            is_wr <= we;
            bt    <= 5'd0;
            ph    <= 1'b0;
            st    <= S_XFER;
          end
        end

        default: st <= S_IDLE;
      endcase
    end
  end
endmodule
