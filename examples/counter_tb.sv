// Smoke test: drive clk/resetn/en, print count each cycle.
// Compiled against either counter.v (original) or counter_split.v +
// counter_comb.v + counter_sync.v (mated split).  Same output either way.
`default_nettype none
module tb;
  reg clk = 0, resetn = 0, en = 0;
  wire [2:0] count;

  counter dut(.clk(clk), .resetn(resetn), .en(en), .count(count));

  always #5 clk = ~clk;
  integer errors = 0;
  initial begin
    // hold reset, 4 cycles
    repeat (4) @(posedge clk);
    resetn = 1;
    if (count !== 3'd0) begin $display("FAIL reset %0d", count); errors = errors + 1; end
    en = 1;
    repeat (9) begin
      @(posedge clk);
      $display("%0d", count);
    end
    en = 0;
    repeat (2) begin
      @(posedge clk);
      $display("hold %0d", count);
    end
    if (errors != 0) $display("ERRORS");
    $finish;
  end
endmodule
`default_nettype wire
