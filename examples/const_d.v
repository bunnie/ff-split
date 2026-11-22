module const_d(clk, d, q0, q1);
  input clk;
  input d;
  output q0;
  output q1;
  reg q0;
  reg q1;
  always @(posedge clk)
    q0 <= d;
  always @(posedge clk)
    q1 <= 1'b1;
endmodule
