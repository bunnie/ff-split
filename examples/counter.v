/* Example test case: 3 flip flops, synchronous up-counter.
 * Written in the same style as flattened_fast_mul_opt.v (plain assign +
 * single-statement always @(posedge) blocks, reset folded into D logic
 * so every FF is a bare D flip flop). */

module counter(clk, resetn, en, count);
  input clk;
  input resetn;
  input en;
  output [2:0] count;
  reg [2:0] q;
  wire [2:0] n;
  wire rst_bar;
  wire g;

  assign rst_bar = ~ resetn;
  assign g = en & ~ rst_bar;
  assign n[0] = q[0] ^ g;
  assign n[1] = q[1] ^ (q[0] & g);
  assign n[2] = q[2] ^ (q[1] & q[0] & g);

  always @(posedge clk)
    q[0] <= rst_bar ? 1'h0 : n[0];
  always @(posedge clk)
    q[1] <= rst_bar ? 1'h0 : n[1];
  always @(posedge clk)
    q[2] <= rst_bar ? 1'h0 : n[2];

  assign count = q;
endmodule
