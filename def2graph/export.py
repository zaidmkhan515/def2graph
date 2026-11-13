"""Export a ConeGraph (union of all port cones) to multiple formats."""
from __future__ import annotations
import json
import os
import pickle
from typing import Dict, List, Set, Tuple

from .conegraph import ConeGraph


def _scalar(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, str)):
        return v
    return str(v)


def build_networkx(cg: ConeGraph, nodes: Set[str], edges: Set[Tuple[str, str]]):
    import networkx as nx
    G = nx.DiGraph(name=cg.design)
    for nid in nodes:
        attrs = {k: _scalar(v) for k, v in cg.node_attrs.get(nid, {}).items()}
        G.add_node(nid, **attrs)
    for (s, d) in edges:
        ea = {k: _scalar(v) for k, v in cg.edge_attrs.get((s, d), {}).items()}
        G.add_edge(s, d, **ea)
    return G


def export_parquet(cg: ConeGraph, nodes, edges, outdir: str) -> List[str]:
    import pandas as pd
    # how many cones each node belongs to
    membership: Dict[str, int] = {}
    for c in cg.cones.values():
        for n in c["nodes"]:
            membership[n] = membership.get(n, 0) + 1

    nrows = []
    for nid in nodes:
        a = cg.node_attrs.get(nid, {})
        nrows.append({
            "node_id": nid,
            "ntype": a.get("ntype"),
            "name": nid[2:],
            "cell": a.get("cell"),
            "kind": a.get("kind"),
            "boundary": a.get("boundary"),
            "direction": a.get("direction"),
            "use": a.get("use"),
            "cell_class": a.get("cell_class"),
            "area": a.get("area"),
            "width": a.get("width"),
            "height": a.get("height"),
            "x": a.get("x"),
            "y": a.get("y"),
            "orient": a.get("orient"),
            "fanin": a.get("fanin"),
            "fanout": a.get("fanout"),
            "degree": a.get("degree"),
            "n_cones": membership.get(nid, 0),
        })
    erows = []
    for (s, d) in edges:
        ea = cg.edge_attrs.get((s, d), {})
        erows.append({
            "src": s, "dst": d,
            "src_type": cg.node_attrs.get(s, {}).get("ntype"),
            "dst_type": cg.node_attrs.get(d, {}).get("ntype"),
            "relation": ea.get("rel"), "pin": ea.get("pin"),
        })
    ndf = pd.DataFrame(nrows)
    edf = pd.DataFrame(erows)
    npath = os.path.join(outdir, "nodes.parquet")
    epath = os.path.join(outdir, "edges.parquet")
    ndf.to_parquet(npath, index=False)
    edf.to_parquet(epath, index=False)
    return [npath, epath]


def export_graphml(G, outdir: str) -> str:
    import networkx as nx
    path = os.path.join(outdir, "graph.graphml")
    nx.write_graphml(G, path)
    return path


def export_gpickle(G, outdir: str) -> str:
    path = os.path.join(outdir, "graph.gpickle")
    with open(path, "wb") as fh:
        pickle.dump(G, fh)
    return path


def export_pyg(cg: ConeGraph, nodes, edges, outdir: str) -> str:
    """PyG HeteroData if torch_geometric is available, else a portable dict."""
    import torch

    # group nodes by type, build per-type index
    by_type: Dict[str, List[str]] = {"port": [], "net": [], "instance": []}
    for nid in nodes:
        t = cg.node_attrs.get(nid, {}).get("ntype")
        if t in by_type:
            by_type[t].append(nid)
    idx: Dict[str, Dict[str, int]] = {
        t: {nid: i for i, nid in enumerate(lst)} for t, lst in by_type.items()}

    def f(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    feats: Dict[str, "torch.Tensor"] = {}
    inst_feats = []
    for nid in by_type["instance"]:
        a = cg.node_attrs[nid]
        k = a.get("kind")
        inst_feats.append([
            1.0 if k == "comb" else 0.0,
            1.0 if k == "flop" else 0.0,
            1.0 if k == "memory" else 0.0,
            1.0 if k == "unknown" else 0.0,
            f(a.get("area")), f(a.get("width")), f(a.get("height")),
            f(a.get("x")), f(a.get("y")),
        ])
    feats["instance"] = torch.tensor(inst_feats or [[0] * 9, ], dtype=torch.float)

    net_feats = []
    for nid in by_type["net"]:
        a = cg.node_attrs[nid]
        net_feats.append([f(a.get("fanin")), f(a.get("fanout")), f(a.get("degree"))])
    feats["net"] = torch.tensor(net_feats or [[0, 0, 0]], dtype=torch.float)

    port_feats = []
    for nid in by_type["port"]:
        d = cg.node_attrs[nid].get("direction")
        port_feats.append([
            1.0 if d == "INPUT" else 0.0,
            1.0 if d == "OUTPUT" else 0.0,
            1.0 if d == "INOUT" else 0.0,
        ])
    feats["port"] = torch.tensor(port_feats or [[0, 0, 0]], dtype=torch.float)

    # edges grouped by (src_type, relation, dst_type)
    rel_edges: Dict[Tuple[str, str, str], List[List[int]]] = {}
    for (s, d) in edges:
        st = cg.node_attrs.get(s, {}).get("ntype")
        dt = cg.node_attrs.get(d, {}).get("ntype")
        if st not in idx or dt not in idx:
            continue
        rel = cg.edge_attrs.get((s, d), {}).get("rel", "to")
        key = (st, rel, dt)
        rel_edges.setdefault(key, []).append([idx[st][s], idx[dt][d]])

    edge_index = {}
    for key, pairs in rel_edges.items():
        t = torch.tensor(pairs, dtype=torch.long).t().contiguous()
        edge_index[key] = t

    path = os.path.join(outdir, "graph_pyg.pt")
    try:
        from torch_geometric.data import HeteroData
        data = HeteroData()
        for t, x in feats.items():
            data[t].x = x
            data[t].node_ids = by_type[t]
        for (st, rel, dt), ei in edge_index.items():
            data[st, rel, dt].edge_index = ei
        torch.save(data, path)
    except Exception:
        # portable fallback: load with torch.load, rebuild HeteroData easily
        payload = {
            "node_ids": by_type,
            "x": feats,
            "edge_index": {f"{s}__{r}__{d}": ei
                           for (s, r, d), ei in edge_index.items()},
            "feature_names": {
                "instance": ["is_comb", "is_flop", "is_mem", "is_unknown",
                             "area", "width", "height", "x", "y"],
                "net": ["fanin", "fanout", "degree"],
                "port": ["is_in", "is_out", "is_inout"],
            },
            "note": "torch_geometric not installed; rebuild HeteroData from these tensors.",
        }
        torch.save(payload, path)
    return path


def export_cones_json(cg: ConeGraph, outdir: str, include_subgraph: bool) -> str:
    out = {"design": cg.design, "dbu_per_micron": cg.dbu,
           "coord_units": "DEF database units (divide by dbu_per_micron for microns)",
           "ports": {}}
    for name, c in cg.cones.items():
        rec = {
            "direction": c["direction"],
            "port_x": c.get("port_x"), "port_y": c.get("port_y"),
            "n_flop_endpoints": c["n_flop_endpoints"],
            "n_memory_endpoints": c["n_memory_endpoints"],
            "n_port_endpoints": c["n_port_endpoints"],
            "cone_instances": c["cone_instances"],
            "cone_nodes": len(c["nodes"]),
            "cone_edges": len(c["edges"]),
            "max_depth": c["max_depth"],
            "endpoints": c["endpoints"],
        }
        if include_subgraph:
            rec["nodes"] = sorted(c["nodes"])
            rec["edges"] = [[s, d] for (s, d) in sorted(c["edges"])]
        out["ports"][name] = rec
    path = os.path.join(outdir, "cones.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1)
    return path


def export_all(cg: ConeGraph, outdir: str, formats, include_subgraph_json=True):
    os.makedirs(outdir, exist_ok=True)
    nodes, edges = cg.union_nodes_edges()
    written: List[str] = []
    fmts = set(formats)

    # Each format degrades independently: a missing optional dependency skips
    # just that output (with a warning) rather than aborting the whole export.
    if "parquet" in fmts:
        try:
            written += export_parquet(cg, nodes, edges, outdir)
        except ImportError:
            cg.warnings.append(
                "parquet export skipped: pandas/pyarrow not installed "
                "(pip install pandas pyarrow)")

    need_nx = {"graphml", "gpickle"} & fmts
    if need_nx:
        try:
            G = build_networkx(cg, nodes, edges)
            if "gpickle" in fmts:
                written.append(export_gpickle(G, outdir))
            if "graphml" in fmts:
                written.append(export_graphml(G, outdir))
        except ImportError:
            cg.warnings.append(
                "graphml/gpickle export skipped: networkx not installed "
                "(pip install networkx)")

    if "pyg" in fmts:
        try:
            written.append(export_pyg(cg, nodes, edges, outdir))
        except ImportError:
            cg.warnings.append(
                "pyg export skipped: torch not installed "
                "(pip install torch; torch_geometric optional)")

    if "json" in fmts:
        written.append(export_cones_json(cg, outdir, include_subgraph_json))

    # always write a compact summary
    summary = {
        "design": cg.design,
        "dbu_per_micron": cg.dbu,
        "graph_stats": cg.stats,
        "union_graph": {"nodes": len(nodes), "edges": len(edges)},
        "cell_boundaries": {
            "flop_endpoints_total": sum(c["n_flop_endpoints"] for c in cg.cones.values()),
            "memory_endpoints_total": sum(c["n_memory_endpoints"] for c in cg.cones.values()),
        },
        "n_ports_traced": len(cg.cones),
        "warnings": cg.warnings,
        "files": [os.path.basename(p) for p in written],
    }
    spath = os.path.join(outdir, "summary.json")
    with open(spath, "w") as fh:
        json.dump(summary, fh, indent=1)
    written.append(spath)
    return written, summary
