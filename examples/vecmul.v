module vecmul(clk, a, b, p);
  input clk;
  input [3:0] a;
  input [3:0] b;
  output [7:0] p;
  reg [3:0] ra;
  reg [3:0] rb;
  wire [7:0] mul;
  always @(posedge clk)
    ra <= a;
  always @(posedge clk)
    rb <= b;
  assign mul = ra * rb;
  assign p = mul;
endmodule
