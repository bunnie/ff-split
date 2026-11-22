module shift(clk, d, q);
  input clk;
  input d;
  output [2:0] q;
  reg q0;
  reg q1;
  reg q2;
  always @(posedge clk)
    q0 <= d;
  always @(posedge clk)
    q1 <= q0;
  always @(posedge clk)
    q2 <= q1;
  assign q = {q2, q1, q0};
endmodule
