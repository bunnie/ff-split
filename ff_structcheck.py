#!/usr/bin/env python3
"""Structural equivalence check: original netlist vs split (mated) netlist.

This is the equivalence criterion of the task: "read the original top level
and this top level into yosys and derive functionally equivalent json
netlists".  Rather than a SAT search (that is ff_equiv.py), this builds a
constructive isomorphism between the two netlists, cut at the flip flops:

  1. Both designs are flattened with yosys (hierarchy -top; flatten;
     opt_clean -purge -- unused wires/pruned ports of the mated halves
     disappear, cell names survive).
  2. Identity cells ($pos/$buf, used by ff_split to re-drive constants and
     to feed interface ports) are transparent: each side's nets are merged
     into union-find classes so a buffer re-drive of a net is the same net.
  3. Seeds: every primary input bit (by port name + bit position) and every
     sequential cell (ff_split preserves the original cell name, so
     gold <--> sync cell pairing is exact, not guessed) share a class id
     across the two designs.
  4. Combinational cells are matched by congruence: two unmatched cells of
     equal type/parameters match once their input bits belong to pairwise
     equal classes; their outputs then join classes pairwise.  This iterates
     to a fixpoint.
  5. Proof obligations at the fixpoint: every sequential cell has its clock
     and D classes equal across sides; every output port bit is paired.
     Any cell left unmatched on either side means INCONCLUSIVE.

A completed check is a witness: a bijection between the netlists' nets
preserving constants, primary inputs, the flip-flop state correspondence,
and every combinational cell -- i.e. the mated split is structurally the
original netlist, hence functionally equivalent.  (For the general
functional-equivalence question between unrelated designs, use ff_equiv.py.)

Requires yosys on PATH.  Exit status: 0 PASS, 1 FAIL, 2 error/INCONCLUSIVE.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

FLATTEN_TEMPLATE = "\n".join(
    [
        "read_json {}",
        "hierarchy -top {}",
        "flatten",
        "opt_clean -purge",
        "write_json {}",
    ]
)

IS_FF = re.compile(r"dff|latch|ffr", re.IGNORECASE)

# Yosys identity cells (Y always equals A): transparent for equivalence.
TRANSPARENT = {"$pos", "$buf", "$buffer"}


def top_module(path):
    """Top module of a JSON file, same guess ff_split uses."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ff_split
    with open(path) as handle:
        design = json.load(handle)
    try:
        return ff_split.top_module_name(design)
    except KeyError:
        print("ff_structcheck: error: cannot guess module in {}".format(path),
              file=sys.stderr)
        sys.exit(2)


def flatten(yosys, json_path, module, out_path, workdir):
    script = FLATTEN_TEMPLATE.format(json_path, module, out_path)
    script_path = workdir / "flatten.ys"
    with open(script_path, "w") as handle:
        handle.write(script + "\n")
    result = subprocess.run(
        [yosys, "-q", "-s", str(script_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        return False
    return True


def bit_key(bit):
    """Stable net identity for one bit vector entry."""
    if bit is None or bit in ("x", "z", "X", "Z"):
        return "x"
    if bit == 0 or bit == "0":
        return "c0"
    if bit == 1 or bit == "1":
        return "c1"
    return ("net", bit)


class Side:
    """One flattened module: cells plus union-find over its net keys."""

    def __init__(self, module):
        self.cells = module["cells"]
        self.ports = module["ports"]
        self.parent = {}

    def find(self, key):
        self.parent.setdefault(key, key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:   # path compression
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def collapse_transparent(self):
        for cell in self.cells.values():
            if cell["type"] not in TRANSPARENT:
                continue
            outs = [bit_key(bit)
                    for port, bits in cell["connections"].items()
                    if cell["port_directions"].get(port) == "output"
                    for bit in bits]
            ins = [bit_key(bit)
                   for port, bits in cell["connections"].items()
                   if cell["port_directions"].get(port) == "input"
                   for bit in bits]
            for out in outs:
                for inp in ins:
                    self.union(out, inp)


def cell_signature(cell):
    return (cell["type"], json.dumps(cell.get("parameters", {}),
                                     sort_keys=True))


def input_bits(cell):
    keys = []
    for port, bits in sorted(cell["connections"].items()):
        if cell["port_directions"].get(port) == "input":
            keys.extend(bit_key(b) for b in bits)
    return keys


def output_bits(cell):
    keys = []
    for port, bits in sorted(cell["connections"].items()):
        if cell["port_directions"].get(port) == "output":
            keys.extend(bit_key(b) for b in bits)
    return keys


def ff_key(name):
    """Cell identity that survives the sync partition + mating round trip:
    strip the $flatten decoration, compare the original name."""
    return re.sub(r"^\$flatten\\[^.]*\.", "", name)


def structural_sanity(design, name):
    """Validate a (pre-flatten) design against the rules yosys silently
    tolerates but that make a netlist unsimulatable: an instance port
    connected with the wrong number of bits, or a net with two drivers
    (module input ports and cell/instance outputs both count; $pos/$buf
    re-drives are excluded since they exist to create legal aliases).
    """
    modules = design["modules"]
    for module_name, module in modules.items():
        driven = set()
        for cell_name, cell in module["cells"].items():
            submodule = modules.get(cell["type"])
            if submodule is not None:
                for port, bits in cell["connections"].items():
                    if port not in submodule["ports"]:
                        return "{}: {}.{} not a port of {}".format(
                            name, module_name, port, cell["type"])
                    if len(bits) != len(submodule["ports"][port]["bits"]):
                        return "{}: {}.{} connected with {} bits, {} expects {}" \
                            .format(name, module_name, port, len(bits),
                                    cell["type"],
                                    len(submodule["ports"][port]["bits"]))
                    if submodule["ports"][port]["direction"] == "output":
                        for bit in bits:
                            if bit in driven:
                                return "{}: {}.{} net {} driven twice" \
                                    .format(name, module_name, cell_name, bit)
                            driven.add(bit)
                continue
            if cell["type"] in TRANSPARENT:
                continue
            for port, bits in cell["connections"].items():
                if cell["port_directions"].get(port) == "output":
                    for bit in bits:
                        if bit in driven:
                            return "{}: {} net {} driven twice".format(
                                name, module_name, bit)
                        driven.add(bit)
    return None


def match(gold_module, gate_module):
    gold, gate = Side(gold_module), Side(gate_module)
    gold.collapse_transparent()
    gate.collapse_transparent()

    ids = {}          # (side, root) -> class id
    counter = [0]

    def share(g_root, t_root):
        gi = ids.get(("g", g_root))
        ti = ids.get(("t", t_root))
        if gi is not None and ti is not None:
            return gi == ti
        if gi is None and ti is None:
            counter[0] += 1
            gi = ti = counter[0]
        the_id = gi if gi is not None else ti
        ids[("g", g_root)] = the_id
        ids[("t", t_root)] = the_id
        return True

    def sid(side, root):
        # Class ids live on the union-find root that was current when the
        # class was created; a later union may have re-rooted the class, so
        # walk the parent chain looking for the entry.
        side_obj = gold if side == "g" else gate
        key = root
        while key is not None:
            if (side, key) in ids:
                return ids[(side, key)]
            parent = side_obj.parent.get(key)
            if parent is None or parent == key:
                return None
            key = parent
        return None

    # --- seeds: constants and primary inputs --------------------------------
    # Constants match themselves (both sides share one class per constant);
    # without this, any cell with a tied-off input never reaches a match.
    for constant in ("c0", "c1", "x"):
        gold.find(constant)
        gate.find(constant)
        share(gold.find(constant), gate.find(constant))
    for name, details in gold.ports.items():
        if details["direction"] not in ("input", "inout"):
            continue
        gate_details = gate.ports.get(name)
        if gate_details is None or len(gate_details["bits"]) != len(details["bits"]):
            return "FAIL", "input port {} missing on gate side".format(name)
        for a, b in zip(details["bits"], gate_details["bits"]):
            if not share(gold.find(bit_key(a)), gate.find(bit_key(b))):
                return "FAIL", "input port {} bit conflict".format(name)

    # --- seeds: sequential cells by preserved identity --------------------
    gold_ff = {ff_key(n): n for n, c in gold.cells.items() if IS_FF.search(c["type"])}
    gate_ff = {ff_key(n): n for n, c in gate.cells.items() if IS_FF.search(c["type"])}
    if set(gold_ff) != set(gate_ff):
        sample = sorted(set(gold_ff) ^ set(gate_ff))[:3]
        return "FAIL", "sequential cell sets differ, e.g. {}".format(sample)
    for key, gname in gold_ff.items():
        gcell, tcell = gold.cells[gname], gate.cells[gate_ff[key]]
        gq = [gold.find(k) for k in output_bits(gcell)]
        tq = [gate.find(k) for k in output_bits(tcell)]
        if len(gq) != len(tq):
            return "FAIL", "FF {} Q width differs".format(key)
        for a, b in zip(gq, tq):
            if not share(a, b):
                return "FAIL", "FF {} Q class conflict".format(key)
    matched_gold_ff = set(gold_ff.values())
    matched_gate_ff = set(gate_ff.values())

    # --- congruence closure over combinational cells ----------------------
    gold_rest = {n for n in gold.cells if n not in matched_gold_ff}
    gate_rest = {n for n in gate.cells if n not in matched_gate_ff}
    # gate cells indexed by signature for candidate lookup
    by_signature = defaultdict(list)
    for n in gate_rest:
        by_signature[cell_signature(gate.cells[n])].append(n)

    changed = True
    while changed:
        changed = False
        for gname in sorted(gold_rest):
            gcell = gold.cells[gname]
            gins = [gold.find(k) for k in input_bits(gcell)]
            if any(sid("g", r) is None for r in gins):
                continue
            candidates = by_signature[cell_signature(gcell)]
            for tname in list(candidates):
                tcell = gate.cells[tname]
                tins = [gate.find(k) for k in input_bits(tcell)]
                if len(tins) != len(gins) or any(
                        sid("t", tr) != sid("g", gr) or sid("g", gr) is None
                        for gr, tr in zip(gins, tins)):
                    continue
                gouts = [gold.find(k) for k in output_bits(gcell)]
                touts = [gate.find(k) for k in output_bits(tcell)]
                if len(gouts) != len(touts):
                    continue
                if not all(share(gr, tr) for gr, tr in zip(gouts, touts)):
                    continue
                candidates.remove(tname)
                gold_rest.discard(gname)
                gate_rest.discard(tname)
                changed = True
                break

    # --- proof obligations --------------------------------------------------
    for key, gname in sorted(gold_ff.items()):
        gcell, tcell = gold.cells[gname], gate.cells[gate_ff[key]]
        for port in ("CLK", "EN", "D"):
            gbits = gcell["connections"].get(port)
            tbits = tcell["connections"].get(port)
            if gbits is None and tbits is None:
                continue
            if gbits is None or tbits is None or len(gbits) != len(tbits):
                return "FAIL", "FF {} port {} mismatch".format(key, port)
            for a, b in zip(gbits, tbits):
                gr, tr = gold.find(bit_key(a)), gate.find(bit_key(b))
                if sid("g", gr) is None or sid("g", gr) != sid("t", tr):
                    return "FAIL", "FF {} port {} cone does not match".format(
                        key, port)
    if set(gold.ports) != set(gate.ports):
        return "FAIL", "port sets differ: {}".format(
            sorted(set(gold.ports) ^ set(gate.ports))[:4])
    for name, details in gold.ports.items():
        if details["direction"] == "input":
            continue
        gate_bits = gate.ports.get(name, {}).get("bits", [])
        if len(gate_bits) != len(details["bits"]):
            return "FAIL", "output port {} missing on gate side".format(name)
        for index, (a, b) in enumerate(zip(details["bits"], gate_bits)):
            gr, tr = gold.find(bit_key(a)), gate.find(bit_key(b))
            if sid("g", gr) is None or sid("g", gr) != sid("t", tr):
                return "FAIL", "output port {} bit {} unmatched".format(
                    name, index)
    if gold_rest or gate_rest:
        return "INCONCLUSIVE", (
            "{} gold, {} gate combinational cells left unmatched (e.g. {}); "
            "use ff_equiv.py".format(len(gold_rest), len(gate_rest),
                                     sorted(gold_rest)[:2]))
    return "PASS", "{} nets, {} state elements anchored, {} combinational " \
        "cells matched".format(counter[0], len(gold_ff),
                               len(gold.cells) - len(gold_ff))


def main():
    parser = argparse.ArgumentParser(
        description="Prove the mated split netlist is structurally identical "
        "(isomorphic) to the original netlist, cut at the flip flops.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("gold_json", type=Path, help="original netlist (JSON)")
    parser.add_argument("gate_json", type=Path, help="split top netlist (JSON)")
    parser.add_argument("--gold-module", default=None)
    parser.add_argument("--gate-module", default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--yosys", default="yosys")
    args = parser.parse_args()

    if shutil.which(args.yosys) is None:
        print("ff_structcheck: error: yosys not found", file=sys.stderr)
        return 2

    gold_name = args.gold_module or top_module(args.gold_json)
    gate_name = args.gate_module or top_module(args.gate_json)
    for path, name in ((args.gold_json, "gold"), (args.gate_json, "gate")):
        with open(path) as handle:
            design = json.load(handle)
        problem = structural_sanity(design, name)
        if problem:
            print("FAIL: " + problem)
            return 1
    workdir = Path(tempfile.mkdtemp(prefix="ff_structcheck."))
    try:
        gold_norm = str(workdir / "gold.json")
        gate_norm = str(workdir / "gate.json")
        if not flatten(args.yosys, args.gold_json, gold_name, gold_norm, workdir):
            return 2
        if not flatten(args.yosys, args.gate_json, gate_name, gate_norm, workdir):
            return 2
        verdict, message = match(
            json.load(open(gold_norm))["modules"][gold_name],
            json.load(open(gate_norm))["modules"][gate_name],
        )
    finally:
        shutil.rmtree(str(workdir), ignore_errors=True)

    print("{}: {} vs {}".format(verdict, gold_name, gate_name))
    if verdict != "PASS" or args.verbose:
        print("  " + message)
    return {"PASS": 0, "INCONCLUSIVE": 2}.get(verdict, 1)


if __name__ == "__main__":
    sys.exit(main())
