# sparse_mem_core — a multi-memory example for def2graph

A synthetic **48-bank SRAM core** (loosely an AI-inference scratchpad array) used
to exercise def2graph's **memory-boundary** handling at scale. It is *not* a
faithful accelerator model — it's a connectivity scaffold that produces the right
graph structure: many memory macros reached from ports both combinationally and
through flops, with read data flowing back to output ports.

![placement](placement.png)

48 tall `fakeram45_1024x32` SRAM banks (orange, 104×318 µm each) split into a
**left group and a right group with a vertical channel** between them, on a
2450×2200 µm die. The ~4,000 Nangate45 std cells (grey/red) fall into the channel
and the inter-bank gaps; the 115 I/O pins sit on the bottom edge.

## Files

| File | What it is |
|---|---|
| `gen.py` | Generator — emits the structural Verilog. Edit `NBANKS` / `MASTER` / `AW` / `DW` to change count, memory style, address and data widths |
| `sparse_mem_core.v` | Generated gate-level netlist (48 banks + ~4,000 cells) |
| `place.tcl` | OpenROAD placement script (floorplan → macro grid → pins → global/detailed place → `write_def`) |
| `sparse_mem_core_placed.def` | The placed DEF — ready-to-use def2graph input |
| `placement.png` | Floorplan figure above |

## Structure

- Per-bank registered address (`DFF`) and shared registered write-data → port→flop→mem paths.
- Combinational per-bank select/enable (`bank_sel` decode → `we`/`ce`) → port→mem paths.
- Masked OR-reduction read path to a **combinational** output (`rdata_c`, so output
  ports reach `rd_out`) and a **registered** output (`rdata_r`).

## Reproduce

```bash
python3 gen.py        # regenerate the netlist (sparse_mem_core.v is included)

# place with OpenROAD (needs the Nangate45 PDK + fakeram45_1024x32 LEF/LIB and an
# openroad binary). Run place.tcl where Nangate45/*.lef|lib are visible, writing to /work.
openroad -exit place.tcl
```

## Run def2graph on it

```bash
python -m def2graph \
  --def sparse_mem_core_placed.def \
  --lef /path/to/Nangate45_tech.lef /path/to/Nangate45_stdcell.lef /path/to/fakeram45_1024x32.lef \
  --lib /path/to/Nangate45_typ.lib /path/to/fakeram45_1024x32.lib \
  --out out/sparse_mem
```

Expected: `fakeram45_1024x32` classified as **memory** (LEF `CLASS BLOCK` + Liberty
`memory()`), **48/48 banks** detected as boundaries, and **1,968 memory endpoints**
(input ports → `we_in`/`ce_in`/`clk`; output ports ← `rd_out`), each carrying its
placement coordinate. Union graph ≈ 9.2k nodes / 13k edges.
