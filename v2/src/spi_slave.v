// SPI slave, mode 0 (sample MOSI on SCK rising edge), MSB first.
// Inputs are asynchronous to clk, so they go through 2-flop synchronizers;
// SCK must stay high and low for at least 2 clk cycles each.
`default_nettype none
module spi_slave (
    input  wire       clk,
    input  wire       rst,
    input  wire       sck_in,
    input  wire       mosi_in,
    input  wire       csn_in,
    output reg        byte_valid,  // 1-cycle pulse
    output reg  [7:0] byte_data,
    output reg        cs_start,    // 1-cycle pulse when CS goes active
    output reg        cs_end       // 1-cycle pulse when CS goes inactive
);
  reg [2:0] sck_s;
  reg [2:0] cs_s;
  reg [1:0] mosi_s;
  reg [2:0] bitcnt;
  reg [6:0] sh;

  wire sck_rise  = sck_s[1] & ~sck_s[2];
  wire cs_active = ~cs_s[1];

  always @(posedge clk) begin
    if (rst) begin
      sck_s  <= 3'b000;
      cs_s   <= 3'b111;
      mosi_s <= 2'b00;
    end else begin
      sck_s  <= {sck_s[1:0], sck_in};
      cs_s   <= {cs_s[1:0], csn_in};
      mosi_s <= {mosi_s[0], mosi_in};
    end
  end

  always @(posedge clk) begin
    byte_valid <= 1'b0;
    cs_start   <= 1'b0;
    cs_end     <= 1'b0;
    if (rst) begin
      bitcnt    <= 3'd0;
      sh        <= 7'd0;
      byte_data <= 8'd0;
    end else begin
      if (cs_s[2] & ~cs_s[1]) begin
        cs_start <= 1'b1;
        bitcnt   <= 3'd0;
      end else if (~cs_s[2] & cs_s[1]) begin
        cs_end <= 1'b1;
      end else if (cs_active && sck_rise) begin
        sh     <= {sh[5:0], mosi_s[1]};
        bitcnt <= bitcnt + 3'd1;
        if (bitcnt == 3'd7) begin
          byte_valid <= 1'b1;
          byte_data  <= {sh, mosi_s[1]};
        end
      end
    end
  end
endmodule
