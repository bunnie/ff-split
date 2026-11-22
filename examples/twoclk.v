module twoclk(clk_a, clk_b, d, qa, qb);
  input clk_a;
  input clk_b;
  input d;
  output qa;
  output qb;
  reg qa;
  reg qb;
  always @(posedge clk_a)
    qa <= d;
  always @(posedge clk_b)
    qb <= d;
endmodule
