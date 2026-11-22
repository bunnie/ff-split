#!/bin/sh
# Run one example end to end: split, yosys round-trip, prove equivalence,
# and run the checks that apply to this design.
#
#   usage: ./run_example.sh <design>     e.g. ./run_example.sh counter
#
#   counter   3-FF up-counter
#   const_d   constant D input
#   vecmul    4-bit vector DFFs
#   shift     direct flop-to-flop connection
#   twoclk    two independent clock domains
set -e
design="$1"
if [ -z "$design" ] || [ ! -f "$(dirname "$0")/${design}.v" ]; then
  echo "usage: $0 <design>  (a <design>.v next to this script)" >&2
  exit 2
fi
cd "$(dirname "$0")"

yosys -q -p "read_verilog $design.v; proc; opt_clean; write_json $design.json"
python3 ../ff_split.py $design.json
python3 ../ff_structcheck.py $design.json ${design}_split.json >/dev/null
echo "$design: structurally identical"
for mode in induct cells bounded; do
  python3 ../ff_equiv.py $design.json ${design}_split.json --mode $mode >/dev/null
  echo "$design: equivalent ($mode)"
done

# Negative control (counter): cross two interface wires; the checkers must
# reject the tampered split.  This tamper is only meaningful where swapping
# two interface wires changes behavior (counter's D wires carry distinct
# functions); designs whose interface wires carry identical or dead-end
# signals (const_d, vecmul, shift, twoclk) would pass it vacuously.
if [ "$design" = counter ]; then
  python3 - << 'EOF'
import json
d = json.load(open('counter_split.json'))
conns = d['modules']['counter_split']['cells']['comb']['connections']
names = sorted(n for n in conns if n.startswith('i_'))
conns[names[0]], conns[names[1]] = conns[names[1]], conns[names[0]]
json.dump(d, open('counter_split_bad.json', 'w'), indent=1)
EOF
  if python3 ../ff_structcheck.py counter.json counter_split_bad.json >/dev/null 2>&1; then
    echo "counter: structcheck negative control FAILED" >&2; exit 1
  fi
  if python3 ../ff_equiv.py counter.json counter_split_bad.json >/dev/null 2>&1; then
    echo "counter: equivalence negative control FAILED (tampered split accepted)" >&2
    exit 1
  fi
  rm -f counter_split_bad.json
  echo "counter: tampered split rejected"
fi

# Negative control (twoclk): swap the two clock domains; structcheck must
# reject (structurally identical is a bug, not a proof, across domains).
if [ "$design" = twoclk ]; then
  python3 - << 'EOF'
import json
d = json.load(open('twoclk_split.json'))
cc = d['modules']['twoclk_split']['cells']['sync']['connections']
cc['clk1'], cc['clk2'] = list(cc['clk2']), list(cc['clk1'])
json.dump(d, open('twoclk_split_bad.json', 'w'), indent=1)
EOF
  if python3 ../ff_structcheck.py twoclk.json twoclk_split_bad.json >/dev/null 2>&1; then
    echo "twoclk: clock-swap control FAILED" >&2; exit 1
  fi
  rm -f twoclk_split_bad.json
  echo "twoclk: swapped clocks rejected"
fi

# Behavioral check (if verilator and a testbench exist): the mated split top
# and the original netlist must simulate to identical traces, with no
# MULTIDRIVEN / UNOPTFLAT warnings from the generated Verilog.
if command -v verilator >/dev/null && [ -f ${design}_tb.sv ]; then
  rm -rf obj_dir
  verilator --binary -o tb_gold --top-module tb $design.v ${design}_tb.sv --timing >/dev/null
  verilator --binary -o tb_split --top-module tb ${design}_split.v ${design}_tb.sv --timing >/dev/null
  ./obj_dir/tb_gold   | grep -v speed > /tmp/gold_sim.txt
  ./obj_dir/tb_split  | grep -v speed > /tmp/split_sim.txt
  diff -q /tmp/gold_sim.txt /tmp/split_sim.txt >/dev/null
  echo "$design: simulation traces identical"
  rm -rf obj_dir
else
  echo "$design: simulation check skipped (no verilator or no ${design}_tb.sv)"
fi

echo "$design OK"
