"""Visualize port logic-cones from a def2graph output directory.

Single cones render cleanly as left-to-right signal-flow graphs via Graphviz
(the `dot` engine). Nodes are styled by role:

    port    light-blue ellipse        comb gate   white box
    flop    salmon box (endpoint)     memory      orange box3d
    net     small grey dot (or collapsed into edge labels with --collapse-nets)

Usage:
    python -m def2graph.viz --out out/picorv32 --port 'mem_addr[10]'
    python -m def2graph.viz --out out/picorv32 --port 'mem_addr[10]' \
        --collapse-nets --format svg --edge-pins
    python -m def2graph.viz --out out/picorv32 --list        # list ports by cone size
"""
from __future__ import annotations
import argparse
import json
import os
import pickle
import sys

_STYLE = {
    "port":   dict(shape="ellipse", style="filled", fillcolor="#9ecae1", fontsize="11"),
    "comb":   dict(shape="box", style="filled", fillcolor="#f7f7f7", fontsize="9"),
    "flop":   dict(shape="box", style="filled,bold", fillcolor="#fb6a4a", fontsize="9"),
    "memory": dict(shape="box3d", style="filled", fillcolor="#fd8d3c", fontsize="10"),
    "unknown": dict(shape="box", style="filled,dashed", fillcolor="#e0e0e0", fontsize="9"),
    "net":    dict(shape="point", width="0.06", color="#999999"),
}


def _load(outdir):
    with open(os.path.join(outdir, "graph.gpickle"), "rb") as fh:
        G = pickle.load(fh)
    with open(os.path.join(outdir, "cones.json")) as fh:
        cones = json.load(fh)["ports"]
    return G, cones


def list_ports(outdir, limit=40):
    _, cones = _load(outdir)
    rows = sorted(cones.items(), key=lambda kv: kv[1]["cone_instances"], reverse=True)
    print(f"{'port':30} {'dir':7} {'insts':>6} {'depth':>6} {'flops':>6} {'mems':>5}")
    for name, c in rows[:limit]:
        print(f"{name:30} {c['direction']:7} {c['cone_instances']:>6} "
              f"{c['max_depth']:>6} {c['n_flop_endpoints']:>6} "
              f"{c['n_memory_endpoints']:>5}")
    print(f"\n({len(cones)} ports total; showing top {min(limit, len(cones))} by cone size)")


def _node_label(G, nid):
    a = G.nodes[nid]
    t = a.get("ntype")
    name = nid[2:]
    if t == "port":
        return f"{name}\\n({a.get('direction')})"
    if t == "net":
        return ""
    cell = str(a.get("cell") or "")
    # shorten long library cell names (sky130_fd_sc_hd__dfxtp_2 -> dfxtp_2)
    short = cell.split("__")[-1] if "__" in cell else cell
    return f"{name}\\n{short}"


def _node_kind(G, nid):
    a = G.nodes[nid]
    if a.get("ntype") == "port":
        return "port"
    if a.get("ntype") == "net":
        return "net"
    return a.get("kind", "comb")


def draw_cone(outdir, port, fmt="png", collapse_nets=False, edge_pins=False,
              max_nodes=600, engine="dot"):
    try:
        from graphviz import Digraph
    except ImportError:
        sys.exit("graphviz python package not installed: pip install graphviz")

    G, cones = _load(outdir)
    if port not in cones:
        sys.exit(f"port '{port}' not found. Try --list.")
    cone = cones[port]
    nodes = set(cone.get("nodes") or [])
    if not nodes:
        sys.exit("cone has no 'nodes' list — regenerate without --no-subgraph-json.")
    if len(nodes) > max_nodes:
        print(f"WARN: cone has {len(nodes)} nodes (> {max_nodes}); "
              f"image may be large. Use --collapse-nets or raise --max-nodes.",
              file=sys.stderr)

    sub = G.subgraph(nodes)

    # Graphviz treats ':' as node:port syntax -> remap ids to colon-free handles.
    def sid(nid):
        return "n_" + nid.replace(":", "__").replace("[", "_").replace("]", "_") \
                          .replace("/", "_").replace(".", "_").replace("\\", "_")

    g = Digraph(name="cone", format=fmt, engine=engine)
    g.attr(rankdir="LR", splines="true", nodesep="0.25", ranksep="0.6",
           label=f"{cones[port]['direction']} port  {port}  "
                 f"(cone: {cone['cone_instances']} gates, depth {cone['max_depth']}, "
                 f"{cone['n_flop_endpoints']} flop / {cone['n_memory_endpoints']} mem endpoints)",
           labelloc="t", fontsize="14")

    if collapse_nets:
        # draw only port/instance nodes; connect driver->sink through each net
        keep = [n for n in sub.nodes if G.nodes[n].get("ntype") != "net"]
        for n in keep:
            g.node(sid(n), label=_node_label(G, n), **_STYLE[_node_kind(G, n)])
        for net in [n for n in sub.nodes if G.nodes[n].get("ntype") == "net"]:
            drivers = [p for p in sub.predecessors(net)]
            sinks = [s for s in sub.successors(net)]
            for d in drivers:
                for s in sinks:
                    lbl = net[2:] if edge_pins else ""
                    g.edge(sid(d), sid(s), label=lbl, fontsize="7", color="#666666")
    else:
        for n in sub.nodes:
            g.node(sid(n), label=_node_label(G, n), **_STYLE[_node_kind(G, n)])
        for u, v, data in sub.edges(data=True):
            lbl = (data.get("pin") or "") if edge_pins else ""
            g.edge(sid(u), sid(v), label=lbl, fontsize="7", color="#666666")

    safe = port.replace("/", "_").replace("[", "_").replace("]", "_")
    outpath = os.path.join(outdir, f"cone_{safe}")
    rendered = g.render(outpath, cleanup=True)
    print(f"wrote {rendered}")
    return rendered


def main(argv=None):
    ap = argparse.ArgumentParser(prog="def2graph.viz",
                                 description="Visualize port logic-cones.")
    ap.add_argument("--out", required=True, help="def2graph output directory")
    ap.add_argument("--port", help="port name to render")
    ap.add_argument("--list", action="store_true", help="list ports by cone size")
    ap.add_argument("--format", default="png", help="png / svg / pdf")
    ap.add_argument("--collapse-nets", action="store_true",
                    help="hide net nodes; connect driver->sink directly")
    ap.add_argument("--edge-pins", action="store_true",
                    help="label edges with pin (or net name when collapsed)")
    ap.add_argument("--max-nodes", type=int, default=600)
    args = ap.parse_args(argv)

    if args.list or not args.port:
        list_ports(args.out)
        return 0
    draw_cone(args.out, args.port, fmt=args.format,
              collapse_nets=args.collapse_nets, edge_pins=args.edge_pins,
              max_nodes=args.max_nodes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
