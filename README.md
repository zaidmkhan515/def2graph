# def2graph — DEF/LEF/Liberty → port-bounded logic-cone graph

Converts a placed-and-routed design (**DEF** connectivity + **LEF** cell abstracts
+ **Liberty** timing/structure) into a directed graph, then traces every
top-level **design port** through the combinational netlist until it reaches a
**flip-flop/latch** or a **memory/macro**. Those sequential/memory pins are the
cone **endpoints**. Output is emitted in several formats for data-science / ML /
exploration.

## What it extracts

| File | Used for |
|------|----------|
| **LEF** | macro `SIZE`, `CLASS` (BLOCK ⇒ hard-macro boundary), pin `DIRECTION`/`USE` |
| **Liberty** | cell `area`, `ff`/`latch` groups ⇒ **flop**, `memory` group ⇒ **memory**, pin direction, `clock` pins, `function` |
| **DEF** | `DESIGN`, `UNITS`, `DIEAREA`, `PINS` (design ports), `COMPONENTS` (instances + placement), `NETS` (connectivity) |

The parsers are keyword/stack driven (not column-based), so they tolerate style
differences across **Nangate45, sky130, ASAP7, gf180mcu, fakeram**, and any DBU
scale. Power/ground nets, pins, and `SPECIALNETS` are filtered out.

## Install deps

```
pip install networkx pandas pyarrow torch         # torch & torch_geometric optional
# torch_geometric optional: if absent, a portable .pt payload is written instead
```

## Usage

```bash
python -m def2graph \
  --def   design.def \
  --lef   tech.lef --lef stdcell.lef --lef sram.lef \
  --lib   stdcell.lib --lib seq.lib --lib sram.lib \
  --out   out/design \
  --formats parquet gpickle graphml pyg json
```

`--lef`/`--lib` are repeatable and accept a **file, a glob (`*`/`**`
recursive), or a directory** (collected recursively). So a multi-corner MMMC
lib dir works directly:

```bash
python -m def2graph --def design.def --lef libs/lef --lib libs/mmmc --out out/design
```

MMMC dirs hold the **same cells at many corners** (ss/tt/ff, V, T). This tool
only reads **corner-invariant** structure (is-flop, is-memory, pin direction,
area), so all corners **dedupe by cell name** — loading the whole dir is safe,
just redundant parsing. One corner is enough. Multiple libs also matter for PDKs
that split sequential cells / memories into separate files (ASAP7).

Python API:

```python
from def2graph.build import build_from_files
cg, summary = build_from_files("design.def", ["a.lef"], ["a.lib"], outdir="out/d")
cone = cg.cones["mem_rdata[29]"]          # per-port traced cone
print(cone["endpoints"])                   # [{instance, cell, pin, type, depth}, ...]
```

## Graph schema

Nodes (`ntype`): `port`, `net`, `instance`. Edges follow signal flow
`driver → net → sink`; the pin name is stored on each edge.

- **INPUT port** → forward BFS, stops at flop/memory (endpoint = e.g. flop `D`/`CK`).
- **OUTPUT port** → backward BFS, stops at flop/memory (endpoint = e.g. flop `Q`).
- **INOUT port** → both.
- Combinational instances are kept **in full** (every intermediate gate + net);
  flop/memory instances are terminal and marked `boundary=True`.

The exported graph is the **union of all port cones** = the design's interface
logic. `cones.json` additionally gives each port's own subgraph + endpoints.

## Outputs (`--out` dir)

| File | Format | Load with |
|------|--------|-----------|
| `nodes.parquet`, `edges.parquet` | columnar tables | `pandas.read_parquet`, DuckDB, Spark |
| `graph.gpickle` | networkx `DiGraph` | `pickle.load` → `nx.descendants(G, "P:clk")` |
| `graph.graphml` | portable XML | Gephi, Cytoscape, Neo4j, `nx.read_graphml` |
| `graph_pyg.pt` | PyG `HeteroData` (or portable tensor dict) | `torch.load(..., weights_only=False)` |
| `cones.json` | per-port endpoints + subgraph | `json.load` |
| `summary.json` | run statistics | `json.load` |

Node features in the PyG export:
`instance = [is_comb, is_flop, is_mem, is_unknown, area, width, height, x, y]`,
`net = [fanin, fanout, degree]`, `port = [is_in, is_out, is_inout]`.
If `torch_geometric` is installed you get a real `HeteroData`; otherwise a dict
with `node_ids`, `x`, `edge_index` (keyed `srctype__rel__dsttype`) to rebuild one.

## Visualization

Single port cones render as left-to-right signal-flow graphs via Graphviz
(`pip install graphviz`, plus the `dot` binary):

```bash
python -m def2graph.viz --out out/picorv32 --list                 # ports by cone size
python -m def2graph.viz --out out/picorv32 --port 'mem_addr[10]' --format svg \
    --collapse-nets --edge-pins                                   # clean logic view
```

Styling: input/output **port** = blue ellipse, **comb** gate = white box,
**flop** endpoint = red box, **memory** endpoint = orange box3d, **net** = grey
dot (hidden with `--collapse-nets`, which draws driver→sink directly and labels
edges with the net name). Output goes to `out/<dir>/cone_<port>.<fmt>`.

The whole **union graph** (~thousands of nodes) is too dense for a static image —
open `graph.graphml` in **Gephi** or **Cytoscape**, or filter `nodes.parquet` /
`edges.parquet` first (e.g. one port's cone, or only `kind=='flop'`).

## Locations

Physical coordinates (DEF database units; divide by `dbu_per_micron`) are stored for:
- **ports** — `x`,`y` on the port node (`nodes.parquet`, GraphML, gpickle) and
  `port_x`,`port_y` per port in `cones.json`.
- **instances incl. flop/memory endpoints** — `x`,`y`,`orient` on the instance
  node, as PyG instance features, and on every record in a cone's `endpoints`
  list in `cones.json` (so endpoint locations are available without a join).

## Tests

```
python3 tests/run_tests.py
```

Covers a deterministic synthetic fixture (asserts exact flop **and** memory
endpoints, both trace directions) plus real designs and multi-vendor parse
checks: **picorv32/sky130**, **gcd/Nangate45** (DBU 2000), **ASAP7**,
**gf180mcu**, **fakeram45**. 47 assertions.
