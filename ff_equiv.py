#!/usr/bin/env python3
"""Prove two yosys JSON netlists equivalent with yosys.

Designed to check the output of ff_split.py:

    ff_split.py design.json                # design_comb/_sync/_split.json
    ff_equiv.py design.json design_split.json

--mode induct (default): flatten both modules, build an $equiv network from
the name-matched wires and prove it by temporal induction (equiv_induct).
--mode bounded: clk2fflogic, miter -equiv and sat -seq N -set-init-zero,
i.e. BMC from equal all-zero power-on states; useful when induction has to
guess too much or to double-check with an initial-state-sensitive miter.

Requires yosys on PATH.  Exit status: 0 equal, 1 not proven, 2 usage errors.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# Temporal induction over the name-matched wires (default proof).
INDUCT_TEMPLATE = "\n".join(
    [
        "read_json {}",
        "flatten {}",
        "read_json {}",
        "flatten {}",
        "equiv_make {} {} equiv",
        "equiv_simple equiv",
        "equiv_induct -seq {} equiv",
        "equiv_status -assert equiv",
    ]
)

# Bounded model check with equal (all-zero) power-on state: clk2fflogic also
# makes latches SAT-importable.  Weaker than induction (only the first N
# cycles), but it pins the initial state, so it catches interface mistakes
# induction can wave through when the design is reset-dominated.
BOUNDED_TEMPLATE = "\n".join(
    [
        "read_json {}",
        "flatten {}",
        "proc",
        "clk2fflogic",
        "read_json {}",
        "flatten {}",
        "proc",
        "clk2fflogic",
        "miter -equiv -make_assert -flatten {} {} miter",
        "sat -verify -seq {} -set-init-zero -prove trigger 0 miter",
    ]
)

# First pass of "cells" mode: build the equiv module and dump every sequential
# cell so Python can pair gold<->gate flip flops by their (name-preserving)
# original cell identity, then equiv_add each pair and run equiv_simple.
CELLS_DISCOVER_TEMPLATE = "\n".join(
    [
        "read_json {}",
        "flatten {}",
        "read_json {}",
        "flatten {}",
        "equiv_make {} {} equiv",
        "tee -o {} select -list equiv/t:$dff equiv/t:$dlatch",
    ]
)


def sync_cell_key(name):
    """Reduce an equiv-module sequential cell path to the stdcell-instance
    identity that ff_split leaves intact across the gold and gate sides.

    equiv_make renames moved cells to <path>_gold / <path>_gate, and flatten
    prefixes them with $flatten\\<inst>.; ff_split keeps the original
    $techmap\\<instance>.$procdff$<n> form on both sides.  Stripping all the
    per-run decoration leaves the instance path, identical for a matched pair.
    """
    name = re.sub(r"^equiv/", "", name)
    name = re.sub(r"_gold$|_gate$", "", name)
    previous = None
    while previous != name:
        previous = name
        name = re.sub(r"^\$[A-Za-z_][A-Za-z_0-9]*\\[^.]*\.", "", name)
    name = re.sub(r"^\$techmap\\", "", name)
    # strip the trailing auto-generated cell name: .$procdff$1175,
    # .$auto$proc_dlatch.cc:409:proc_dlatch$1173, ...
    name = re.sub(r"\.\$.*\$[0-9]+$", "", name)
    return name


# Second pass of "cells" mode: equiv_add every paired flip flop, equiv_simple
# for what falls to direct combinational reasoning, then equiv_induct for the
# rest.  With every state element anchored the induction hypothesis already
# assumes the interesting equalities, so depth 1 closes almost everything.
CELLS_PROOF_TEMPLATE = "\n".join(
    [
        "read_json {}",
        "flatten {}",
        "read_json {}",
        "flatten {}",
        "equiv_make {} {} equiv",
        "select -clear",
        "cd equiv",
        "{}",
        "cd ..",
        "equiv_simple equiv",
        "equiv_induct -seq {} equiv",
        "equiv_status -assert equiv",
    ]
)


def main():
    parser = argparse.ArgumentParser(
        description="Equivalence-check two yosys JSON netlists "
        "(induction / anchored flip-flop cells / bounded).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("gold_json", type=Path, help="reference netlist (JSON)")
    parser.add_argument("gate_json", type=Path, help="netlist to check (JSON)")
    parser.add_argument("--gold-module", default=None,
                        help="gold module name (default: sole non-empty module)")
    parser.add_argument("--gate-module", default=None,
                        help="gate module name (default: sole non-empty module)")
    parser.add_argument("--seq", type=int, default=4,
                        help="time steps (equiv_induct -seq / sat -seq)")
    parser.add_argument("--mode", choices=("induct", "bounded", "cells"),
                        default="induct",
                        help="induct: temporal induction (full proof); "
                        "cells: anchor each flip flop pair via equiv_add -cell "
                        "(needs ff_split-preserved cell names, much cheaper); "
                        "bounded: BMC with equal all-zero init state")
    parser.add_argument("--yosys", default="yosys", help="yosys executable")
    args = parser.parse_args()

    if shutil.which(args.yosys) is None:
        print("ff_equiv: error: yosys not found ({})".format(args.yosys),
              file=sys.stderr)
        return 2

    # Same top-module guess as ff_split uses (a split file holds comb +
    # sync + top; the top is the module nobody instantiates).
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ff_split
    import json

    def pick_module(path, name):
        if name:
            return name
        with open(path) as handle:
            design = json.load(handle)
        try:
            return ff_split.top_module_name(design)
        except KeyError:
            print("ff_equiv: error: cannot guess module in {}; use --*-module"
                  .format(path), file=sys.stderr)
            sys.exit(2)

    gold_module = pick_module(args.gold_json, args.gold_module)
    gate_module = pick_module(args.gate_json, args.gate_module)

    def run_script(script):
        """Write a yosys script to a temp file, run it, report status."""
        with tempfile.NamedTemporaryFile(
            "w", suffix=".ys", delete=False
        ) as handle:
            handle.write(script + "\n")
            script_path = handle.name
        print(" ".join([args.yosys, "-q", "-s", script_path]))
        print(script)
        return subprocess.run([args.yosys, "-q", "-s", script_path]).returncode

    if args.mode == "bounded":
        script = BOUNDED_TEMPLATE.format(
            args.gold_json, gold_module, args.gate_json, gate_module,
            gold_module, gate_module, args.seq,
        )
        ok = run_script(script) == 0
    elif args.mode == "cells":
        ok = cells_mode(run_script, args, gold_module, gate_module)
    else:
        script = INDUCT_TEMPLATE.format(
            args.gold_json, gold_module, args.gate_json, gate_module,
            gold_module, gate_module, args.seq,
        )
        ok = run_script(script) == 0

    if ok:
        print("EQUIVALENT: {} == {} (mode {}, seq {})"
              .format(gold_module, gate_module, args.mode, args.seq))
        return 0
    print("NOT PROVEN: {} vs {}".format(gold_module, gate_module),
          file=sys.stderr)
    return 1


def cells_mode(run_script, args, gold_module, gate_module):
    """Anchor every flip flop with equiv_add -cell, then equiv_simple plus a
    shallow equiv_induct for what direct reasoning leaves over."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cell_list = Path(tmpdir) / "cells.txt"
        discover = CELLS_DISCOVER_TEMPLATE.format(
            args.gold_json, gold_module, args.gate_json, gate_module,
            gold_module, gate_module, cell_list,
        )
        if run_script(discover) != 0:
            return False
        gold, gate = {}, {}
        listed = 0
        with open(cell_list) as handle:
            for line in handle:
                name = line.strip()
                if not name:
                    continue
                listed += 1
                key = sync_cell_key(name)
                bucket = gate if name.endswith("_gate") else gold
                if key in bucket:
                    print("ff_equiv: error: two sequential cells pair to the "
                          "same identity {}; use --mode induct".format(key),
                          file=sys.stderr)
                    return False
                bucket[key] = name
        if listed % 2 != 0:
            print("ff_equiv: error: unbalanced sequential cells between gold "
                  "and gate; use --mode induct", file=sys.stderr)
            return False
        if not gold or not gate or set(gold) != set(gate):
            print("ff_equiv: error: cannot pair flip flops ({} gold vs {} "
                  "gate, {} matched); use --mode induct"
                  .format(len(gold), len(gate), len(set(gold) & set(gate))),
                  file=sys.stderr)
            return False
        # equiv_add runs inside "cd equiv", so names are relative to the
        # module: drop the "equiv/" prefix the select listed with them.
        def relative(name):
            return re.sub(r"^equiv/", "", name)

        adds = "\n".join(
            "equiv_add -cell {} {}".format(relative(gold[k]), relative(gate[k]))
            for k in sorted(gold)
        )
        proof = CELLS_PROOF_TEMPLATE.format(
            args.gold_json, gold_module, args.gate_json, gate_module,
            gold_module, gate_module, adds, args.seq,
        )
        return run_script(proof) == 0


if __name__ == "__main__":
    sys.exit(main())
