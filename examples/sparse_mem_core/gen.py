# Sparse multi-memory core, structural Nangate45. Clean version: all 32-bit
# groups are declared buses (no escaped identifiers, no replication operator).
NBANKS = 16
BW = 4
insts = []
scalar_wires = set()
bus_wires = {}   # name -> width


def sw(n):
    scalar_wires.add(n); return n


def dff(name, d, q):
    insts.append(("DFF_X1", name, [("D", d), ("CK", "clk"), ("Q", q)]))


def g2(cell, name, a1, a2, zn):
    insts.append((cell, name, [("A1", a1), ("A2", a2), ("ZN", zn)]))


def inv(name, a, zn):
    insts.append(("INV_X1", name, [("A", a), ("ZN", zn)]))


def buf(name, a, z):
    insts.append(("BUF_X1", name, [("A", a), ("Z", z)]))


def and_tree(prefix, inputs):
    level = list(inputs); k = 0
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level) - 1, 2):
            o = sw(f"{prefix}_a{k}")
            g2("AND2_X1", f"{prefix}_and{k}", level[i], level[i + 1], o); nxt.append(o); k += 1
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0]


def or_tree(prefix, inputs):
    level = list(inputs); k = 0
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level) - 1, 2):
            o = sw(f"{prefix}_o{k}")
            g2("OR2_X1", f"{prefix}_or{k}", level[i], level[i + 1], o); nxt.append(o); k += 1
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0]


bus_wires["aq"] = 6
bus_wires["wq"] = 32
bus_wires["rq"] = 32
for i in range(6):
    dff(f"areg_{i}", f"addr[{i}]", f"aq[{i}]")
for i in range(32):
    dff(f"wreg_{i}", f"wdata[{i}]", f"wq[{i}]")
for j in range(BW):
    inv(f"bsinv_{j}", f"bank_sel[{j}]", sw(f"bsn_{j}"))

rmask = [[None] * 32 for _ in range(NBANKS)]
for b in range(NBANKS):
    sel_inputs = [f"bank_sel[{j}]" if (b >> j) & 1 else f"bsn_{j}" for j in range(BW)]
    sel = and_tree(f"seldec{b}", sel_inputs)
    we_b, ce_b = sw(f"we_{b}"), sw(f"ce_{b}")
    g2("AND2_X1", f"weg_{b}", "we", sel, we_b)
    g2("AND2_X1", f"ceg_{b}", "ce", sel, ce_b)
    bus_wires[f"rd{b}"] = 32
    bus_wires[f"rm{b}"] = 32
    insts.append(("fakeram45_64x32", f"bank{b}", [
        ("clk", "clk"), ("ce_in", ce_b), ("we_in", we_b),
        ("addr_in", "aq"), ("wd_in", "wq"),
        ("w_mask_in", "{" + ", ".join([we_b] * 32) + "}"),
        ("rd_out", f"rd{b}"),
    ]))
    for i in range(32):
        g2("AND2_X1", f"rmg_{b}_{i}", f"rd{b}[{i}]", sel, f"rm{b}[{i}]")
        rmask[b][i] = f"rm{b}[{i}]"

for i in range(32):
    rc = or_tree(f"rtree{i}", [rmask[b][i] for b in range(NBANKS)])
    buf(f"rcbuf_{i}", rc, f"rdata_c[{i}]")
    dff(f"rreg_{i}", rc, f"rq[{i}]")
    buf(f"rrbuf_{i}", f"rq[{i}]", f"rdata_r[{i}]")

# ---- emit ----
busports = {"addr": 6, "wdata": 32, "bank_sel": BW, "rdata_c": 32, "rdata_r": 32}
lines = ["module sparse_mem_core(clk, ce, we, addr, wdata, bank_sel, rdata_c, rdata_r);"]
for s in ("clk", "ce", "we"):
    lines.append(f"  input {s};")
for nm, bw in busports.items():
    d = "output" if nm.startswith("rdata") else "input"
    lines.append(f"  {d} [{bw-1}:0] {nm};")
for nm, bw in bus_wires.items():
    lines.append(f"  wire [{bw-1}:0] {nm};")
for n in sorted(scalar_wires):
    lines.append(f"  wire {n};")
for celltype, name, conns in insts:
    cs = ", ".join(f".{p}({e})" for p, e in conns)
    lines.append(f"  {celltype} {name} ( {cs} );")
lines.append("endmodule")
import os as _os
open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "sparse_mem_core.v"), "w").write("\n".join(lines) + "\n")
print("banks:", NBANKS, "| std cells:", sum(1 for c,_,_ in insts if c != "fakeram45_64x32"),
      "| memories:", sum(1 for c,_,_ in insts if c == "fakeram45_64x32"))
