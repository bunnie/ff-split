// Behavioral differential test: drive clk/resetn and randomized PCPI
// inputs; sample all outputs every clock edge.  Compiled once against the
// original netlist and once against the mated split top (module renamed
// to match via -D or a wrapper).  Identical traces either way.
module picorv32_pcpi_fast_mul_wrapper (
    input wire clk,
    input wire resetn,
    input wire pcpi_valid,
    input wire [31:0] pcpi_insn,
    input wire [31:0] pcpi_rs1,
    input wire [31:0] pcpi_rs2,
    output wire pcpi_wr,
    output wire [31:0] pcpi_rd,
    output wire pcpi_wait,
    output wire pcpi_ready
);
`ifdef GOLD_DUT
  picorv32_pcpi_fast_mul dut (.*);
`else
  picorv32_pcpi_fast_mul_split dut (.*);
`endif
endmodule

module mul_tb;
  reg clk = 0, resetn = 0;
  reg valid = 0;
  reg [31:0] insn = 0, rs1 = 0, rs2 = 0;
  wire wr;
  wire [31:0] rd;
  wire waitb, ready;
  integer i;
  reg [31:0] tmp;
  integer seed = 12345;

  picorv32_pcpi_fast_mul_wrapper dut (
      .clk(clk), .resetn(resetn), .pcpi_valid(valid),
      .pcpi_insn(insn), .pcpi_rs1(rs1), .pcpi_rs2(rs2),
      .pcpi_wr(wr), .pcpi_rd(rd), .pcpi_wait(waitb), .pcpi_ready(ready));

  always #5 clk = ~clk;

  initial begin
    // fix to one hard-coded instruction for multiplication: `mul` (extracts the low word on rd)
    insn = 32'b0000_001_0_0000_0000_0_000_0000_0_011_0011;
    for (i = 0; i < 4; i = i + 1) @(posedge clk);
    resetn = 0;
    for (i = 0; i < 400; i = i + 1) begin
      resetn = 1;
      @(negedge clk);
      tmp = $random(seed);
      valid = tmp[0];
      rs1   = $random(seed);
      rs2   = $random(seed);
      @(posedge clk);
      $display("%0d r=%0b v=%0b rd=%h wr=%0b w=%0b rdy=%0b rs1=%h rs2=%h",
               i, resetn, valid, rd, wr, waitb, ready, rs1, rs2);
    end
    $finish;
  end
endmodule
