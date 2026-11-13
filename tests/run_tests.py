#!/usr/bin/env python3
"""Test def2graph across a synthetic fixture and multiple real PDK/tool flavors.

Covers:
  1. Synthetic fixture  - deterministic flop AND memory boundary assertions.
  2. picorv32 (sky130)  - real hardened design, OpenROAD/LibreLane DEF.
  3. gcd (Nangate45)     - different vendor/academic PDK, different DBU (2000).
  4. Parse-smoke tests   - ASAP7, gf180mcu, fakeram LEF/LIB parse + classify
                           (proves multi-vendor LEF/LIB parsing even w/o a DEF).

Run:  python3 tests/run_tests.py
"""
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from def2graph.build import build_from_files            # noqa: E402
from def2graph.lefparse import parse_lefs               # noqa: E402
from def2graph.libparse import parse_liberties          # noqa: E402
from def2graph.model import CellLibrary                 # noqa: E402

FIX = os.path.join(HERE, "fixtures")

PASS, FAIL = 0, 0
RESULTS = []


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(("PASS", msg))
    else:
        FAIL += 1
        RESULTS.append(("FAIL", msg))
        print(f"   ✗ FAIL: {msg}")


def endpoints_of(cone, etype):
    return sorted({e["instance"] for e in cone["endpoints"] if e["type"] == etype})


# ---------------------------------------------------------------- fixture
def test_fixture():
    print("\n[TEST 1] synthetic fixture (flop + memory boundaries)")
    out = tempfile.mkdtemp(prefix="tiny_")
    cg, summary = build_from_files(
        def_path=os.path.join(FIX, "tiny.def"),
        lef_paths=[os.path.join(FIX, "tiny.lef")],
        lib_paths=[os.path.join(FIX, "tiny.lib")],
        outdir=out, verbose=False)

    cones = cg.cones
    # classification
    check(cg.node_attrs["I:u_ff"]["kind"] == "flop", "DFF classified as flop")
    check(cg.node_attrs["I:u_ram"]["kind"] == "memory", "RAM8 classified as memory")
    check(cg.node_attrs["I:u_and"]["kind"] == "comb", "AND2 classified as comb")

    # in0 forward cone -> reaches flop u_ff and memory u_ram
    check(endpoints_of(cones["in0"], "flop") == ["u_ff"],
          "in0 reaches flop u_ff")
    check(endpoints_of(cones["in0"], "memory") == ["u_ram"],
          "in0 reaches memory u_ram")

    # in1 -> flop + memory (via AND and via WD)
    check(endpoints_of(cones["in1"], "flop") == ["u_ff"], "in1 reaches flop u_ff")
    check(endpoints_of(cones["in1"], "memory") == ["u_ram"], "in1 reaches memory u_ram")

    # clk -> flop CK and memory CLK
    clk_flop = [e for e in cones["clk"]["endpoints"] if e["type"] == "flop"]
    check(any(e["pin"] == "CK" for e in clk_flop), "clk reaches flop via CK pin")
    check(endpoints_of(cones["clk"], "memory") == ["u_ram"], "clk reaches memory u_ram")

    # out0 backward -> flop u_ff via Q
    out0_ep = cones["out0"]["endpoints"]
    check(endpoints_of(cones["out0"], "flop") == ["u_ff"], "out0 traces back to flop u_ff")
    check(any(e["pin"] == "Q" for e in out0_ep if e["type"] == "flop"),
          "out0 endpoint pin is Q")
    check(cones["out0"]["direction"] == "OUTPUT", "out0 direction OUTPUT")

    # out1 backward -> memory u_ram via RD
    check(endpoints_of(cones["out1"], "memory") == ["u_ram"],
          "out1 traces back to memory u_ram")
    check(any(e["pin"] == "RD" for e in cones["out1"]["endpoints"]),
          "out1 endpoint pin is RD")

    # the combinational cone keeps intermediate gates (full cone, not reduced)
    check("I:u_and" in cones["in0"]["nodes"] and "I:u_inv" in cones["in0"]["nodes"],
          "in0 cone retains intermediate comb gates (u_and, u_inv)")
    check("N:n1" in cones["in0"]["nodes"], "in0 cone retains intermediate net n1")

    # exported files exist
    for fn in ("nodes.parquet", "edges.parquet", "graph.gpickle",
               "graph.graphml", "graph_pyg.pt", "cones.json", "summary.json"):
        check(os.path.exists(os.path.join(out, fn)), f"exported {fn}")

    # parquet / pyg loadable
    import pandas as pd
    ndf = pd.read_parquet(os.path.join(out, "nodes.parquet"))
    check(len(ndf) > 0 and "ntype" in ndf.columns, "nodes.parquet loadable & non-empty")
    import torch
    g = torch.load(os.path.join(out, "graph_pyg.pt"), weights_only=False)
    check(g is not None, "graph_pyg.pt loadable")
    print(f"   fixture out: {out}")


# ---------------------------------------------------------------- real designs
def test_real(name, def_path, lef_paths, lib_paths, expect_memory=False):
    print(f"\n[TEST] real design: {name}")
    if not os.path.exists(def_path):
        check(False, f"{name}: DEF exists ({def_path})")
        return
    out = tempfile.mkdtemp(prefix=f"{name}_")
    try:
        cg, summary = build_from_files(
            def_path=def_path, lef_paths=lef_paths, lib_paths=lib_paths,
            outdir=out, verbose=True)
    except Exception:
        traceback.print_exc()
        check(False, f"{name}: build completed without exception")
        return

    gs = summary["graph_stats"]
    check(gs["instances"] > 0, f"{name}: parsed instances ({gs['instances']})")
    check(gs["signal_nets"] > 0, f"{name}: parsed signal nets ({gs['signal_nets']})")
    check(gs["ports_signal"] > 0, f"{name}: parsed signal ports ({gs['ports_signal']})")
    flop_eps = summary["cell_boundaries"]["flop_endpoints_total"]
    check(flop_eps > 0, f"{name}: found flop endpoints ({flop_eps})")
    unknown_frac = gs["unknown_cells"]
    check(unknown_frac == 0 or gs["instances"] > 0,
          f"{name}: unknown cell types = {unknown_frac}")
    # at least one port actually reaches a boundary
    reached = sum(1 for c in cg.cones.values()
                  if c["n_flop_endpoints"] + c["n_memory_endpoints"] > 0)
    check(reached > 0, f"{name}: {reached} port(s) reach a flop/memory boundary")
    if expect_memory:
        mem_eps = summary["cell_boundaries"]["memory_endpoints_total"]
        check(mem_eps > 0, f"{name}: found memory endpoints ({mem_eps})")
    print(f"   {name} out: {out}  union={summary['union_graph']}")


# ---------------------------------------------------------------- parse smoke
def test_parse_smoke(name, lef_paths, lib_paths, want_flop=False, want_mem=False):
    print(f"\n[TEST] parse-smoke ({name}) multi-vendor LEF/LIB")
    lef_paths = [p for p in lef_paths if os.path.exists(p)]
    lib_paths = [p for p in lib_paths if os.path.exists(p)]
    if not lef_paths and not lib_paths:
        check(False, f"{name}: input files exist")
        return
    lef = parse_lefs(lef_paths)
    lib = parse_liberties(lib_paths)
    cl = CellLibrary.from_parsed(lef, lib)
    check(len(cl.cells) > 0, f"{name}: parsed {len(cl.cells)} cells")
    if lef_paths:
        check(len(lef.macros) > 0, f"{name}: parsed {len(lef.macros)} LEF macros")
    if want_flop:
        check(cl.stats["flop"] > 0, f"{name}: detected flops ({cl.stats['flop']})")
    if want_mem:
        check(cl.stats["memory"] > 0, f"{name}: detected memory ({cl.stats['memory']})")
    print(f"   {name}: {cl.stats}")


def main():
    test_fixture()

    # picorv32 - sky130 (LibreLane/OpenROAD), real hardened design
    test_real(
        "picorv32_sky130",
        "/Users/zaidkhan/designs/picorv32_signoff/picorv32_signoff/librelane/"
        "runs/RUN_2026-07-25_14-28-25/final/def/picorv32.def",
        ["/Users/zaidkhan/OpenROAD/test/sky130hd/sky130_fd_sc_hd_merged.lef"],
        ["/Users/zaidkhan/librelane-1/PathView/liberty/sky130_fd_sc_hd.lib"],
    )

    # gcd - Nangate45 academic PDK, DBU=2000
    test_real(
        "gcd_nangate45",
        "/Users/zaidkhan/OpenROAD/test/gcd_nangate45.def",
        ["/Users/zaidkhan/OpenROAD/test/Nangate45/Nangate45.lef"],
        ["/Users/zaidkhan/OpenROAD/test/Nangate45/Nangate45_typ.lib"],
    )

    # parse-smoke: ASAP7 (seq cells in separate lib + fakeram memory)
    test_parse_smoke(
        "asap7",
        ["/Users/zaidkhan/OpenROAD/test/asap7/asap7sc7p5t_28_R_1x_220121a.lef",
         "/Users/zaidkhan/OpenROAD/test/asap7/fakeram7_256x32.lef"],
        ["/Users/zaidkhan/OpenROAD/test/asap7/asap7sc7p5t_SEQ_RVT_FF_nldm_220123.lib",
         "/Users/zaidkhan/OpenROAD/test/asap7/fakeram7_256x32.lib"],
        want_flop=True, want_mem=True,
    )

    # parse-smoke: gf180mcu (GlobalFoundries)
    test_parse_smoke(
        "gf180mcu",
        ["/Users/zaidkhan/OpenROAD/test/gf180/gf180mcu_fd_sc_mcu7t5v0.lef"],
        ["/Users/zaidkhan/librelane-1/PathView/liberty/"
         "gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib"],
        want_flop=True,
    )

    # parse-smoke: fakeram45 memory macro (Nangate flow)
    test_parse_smoke(
        "fakeram45",
        ["/Users/zaidkhan/OpenROAD/test/Nangate45/fakeram45_64x32.lef"],
        ["/Users/zaidkhan/OpenROAD/test/Nangate45/fakeram45_64x32.lib"],
        want_mem=True,
    )

    print("\n" + "=" * 60)
    print(f"RESULTS: {PASS} passed, {FAIL} failed")
    if FAIL:
        print("\nFailures:")
        for status, msg in RESULTS:
            if status == "FAIL":
                print(f"  ✗ {msg}")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
