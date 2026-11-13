"""Build a directed signal-flow graph and trace port-bounded logic cones.

Graph nodes (string ids):
    P:<name>   top-level design port
    N:<name>   net
    I:<name>   instance (combinational, unless it is a boundary)

Edges follow signal flow:  driver -> net -> sink
    driver = output pin of an instance, or an INPUT design port
    sink   = input  pin of an instance, or an OUTPUT design port
INOUT pins/ports contribute both directions.

Cone tracing:
    INPUT  port -> forward BFS, stop at flop/memory instances (endpoints)
    OUTPUT port -> backward BFS, stop at flop/memory instances (endpoints)
    INOUT  port -> both
"""
from __future__ import annotations
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set, Tuple

from .defparse import DefData
from .model import CellLibrary

_POWER_USES = {"POWER", "GROUND"}
_POWER_NAMES = {"VPWR", "VGND", "VDD", "VSS", "VCC", "GND", "VNB", "VPB",
                "VDDA", "VSSA", "VBN", "VBP"}


def _pid(name: str) -> str:  # port id
    return "P:" + name


def _nid(name: str) -> str:  # net id
    return "N:" + name


def _iid(name: str) -> str:  # instance id
    return "I:" + name


class ConeGraph:
    def __init__(self, design: str) -> None:
        self.design = design
        self.dbu = 1000
        self.node_attrs: Dict[str, dict] = {}
        self.fwd: Dict[str, List[str]] = defaultdict(list)
        self.rev: Dict[str, List[str]] = defaultdict(list)
        self.edge_attrs: Dict[Tuple[str, str], dict] = {}
        self.ports: List[str] = []          # port node ids (signal only)
        self.cones: Dict[str, dict] = {}
        self.warnings: List[str] = []
        self.stats: Dict[str, int] = {}

    # ---- construction -------------------------------------------------
    @classmethod
    def from_def(cls, defd: DefData, lib: CellLibrary,
                 dbu: Optional[int] = None) -> "ConeGraph":
        self = cls(defd.design or "design")
        dbu = dbu or defd.dbu_microns or 1000
        self.dbu = dbu
        unknown_cells: Set[str] = set()

        def add_edge(src: str, dst: str, pin: str, rel: str) -> None:
            if dst not in self.fwd[src]:
                self.fwd[src].append(dst)
                self.rev[dst].append(src)
            self.edge_attrs.setdefault((src, dst), {"pin": pin, "rel": rel})

        # --- instance nodes ---
        for inst, comp in defd.components.items():
            ci = lib.get(comp.cell)
            kind = ci.kind if ci else "unknown"
            if ci is None:
                unknown_cells.add(comp.cell)
            self.node_attrs[_iid(inst)] = {
                "ntype": "instance", "cell": comp.cell, "kind": kind,
                "boundary": bool(ci and ci.is_boundary),
                "x": comp.x, "y": comp.y, "orient": comp.orient,
                "area": (ci.area if ci else None),
                "width": (ci.width if ci else None),
                "height": (ci.height if ci else None),
                "cell_class": (ci.cls if ci else None),
            }

        # --- port nodes (signal only) ---
        for pname, port in defd.ports.items():
            if port.use in _POWER_USES or pname in _POWER_NAMES:
                continue
            self.node_attrs[_pid(pname)] = {
                "ntype": "port", "direction": (port.direction or "INOUT"),
                "use": port.use, "x": port.x, "y": port.y,
                "net": port.net,
            }
            self.ports.append(_pid(pname))

        # --- nets -> edges ---
        n_signal_nets = 0
        for nname, net in defd.nets.items():
            if net.use in _POWER_USES or nname in _POWER_NAMES:
                continue

            drivers: List[Tuple[str, str]] = []   # (node_id, pin)
            sinks: List[Tuple[str, str]] = []

            for a, b in net.conns:
                if a.upper() == "PIN":
                    # top-level port connection; b is the port name
                    if _pid(b) not in self.node_attrs:
                        continue
                    d = self.node_attrs[_pid(b)]["direction"]
                    node = _pid(b)
                    if d == "INPUT":
                        drivers.append((node, b))
                    elif d == "OUTPUT":
                        sinks.append((node, b))
                    else:  # INOUT
                        drivers.append((node, b))
                        sinks.append((node, b))
                else:
                    inst, pin = a, b
                    if _iid(inst) not in self.node_attrs:
                        continue
                    ci = lib.get(defd.components[inst].cell) if inst in defd.components else None
                    if ci is not None:
                        pinfo = ci.pins.get(pin)
                        if pinfo and pinfo.is_power:
                            continue
                        d = ci.pin_dir(pin)
                    else:
                        d = "INOUT"
                    node = _iid(inst)
                    if d == "OUTPUT":
                        drivers.append((node, pin))
                    elif d == "INPUT":
                        sinks.append((node, pin))
                    elif d == "INTERNAL":
                        continue
                    else:  # INOUT / unknown
                        drivers.append((node, pin))
                        sinks.append((node, pin))

            if not drivers and not sinks:
                continue
            n_signal_nets += 1
            netnode = _nid(nname)
            self.node_attrs[netnode] = {
                "ntype": "net", "use": net.use,
                "fanin": len(drivers), "fanout": len(sinks),
                "degree": len(net.conns),
            }
            for dn, dp in drivers:
                add_edge(dn, netnode, dp, "drive")
            for sn, sp in sinks:
                add_edge(netnode, sn, sp, "sink")

        self.stats = {
            "instances": len(defd.components),
            "signal_nets": n_signal_nets,
            "ports_total": len(defd.ports),
            "ports_signal": len(self.ports),
            "unknown_cells": len(unknown_cells),
        }
        if unknown_cells:
            sample = ", ".join(sorted(unknown_cells)[:5])
            self.warnings.append(
                f"{len(unknown_cells)} cell type(s) not found in LEF/LIB "
                f"(treated as pass-through): {sample}"
                + (" ..." if len(unknown_cells) > 5 else ""))
        return self

    # ---- cone tracing -------------------------------------------------
    def _is_boundary(self, node: str) -> bool:
        a = self.node_attrs.get(node)
        return bool(a and a.get("ntype") == "instance" and a.get("boundary"))

    def _trace(self, start: str, adj: Dict[str, List[str]],
               edge_dir_forward: bool) -> dict:
        """BFS from `start` over adjacency `adj`, stopping at boundary instances."""
        visited: Set[str] = {start}
        cone_nodes: Set[str] = {start}
        cone_edges: List[Tuple[str, str]] = []
        endpoints: List[dict] = []
        depth_of = {start: 0}
        q = deque([start])
        max_depth = 0

        while q:
            node = q.popleft()
            d = depth_of[node]
            for nb in adj.get(node, ()):  # neighbours in traversal direction
                # edge key is always stored src->dst in signal direction
                ekey = (node, nb) if edge_dir_forward else (nb, node)
                cone_edges.append(ekey)
                if nb in visited:
                    continue
                visited.add(nb)
                cone_nodes.add(nb)
                nb_is_inst = self.node_attrs.get(nb, {}).get("ntype") == "instance"
                nd = d + (1 if nb_is_inst else 0)
                depth_of[nb] = nd
                max_depth = max(max_depth, nd)
                if self._is_boundary(nb):
                    attrs = self.node_attrs[nb]
                    pin = self.edge_attrs.get(ekey, {}).get("pin")
                    endpoints.append({
                        "instance": nb[2:], "cell": attrs.get("cell"),
                        "pin": pin, "type": attrs.get("kind"),
                        "depth": nd,
                        "x": attrs.get("x"), "y": attrs.get("y"),
                        "orient": attrs.get("orient"),
                    })
                    # boundary: do not expand further
                    continue
                # output port reached in forward trace -> terminal sink
                if self.node_attrs.get(nb, {}).get("ntype") == "port":
                    attrs = self.node_attrs[nb]
                    endpoints.append({
                        "instance": nb[2:], "cell": None, "pin": None,
                        "type": "port", "depth": nd,
                        "x": attrs.get("x"), "y": attrs.get("y"),
                        "orient": None,
                    })
                    continue
                q.append(nb)

        return {
            "nodes": cone_nodes, "edges": cone_edges,
            "endpoints": endpoints, "max_depth": max_depth,
        }

    def trace_all_ports(self) -> None:
        for pnode in self.ports:
            attrs = self.node_attrs[pnode]
            direction = attrs.get("direction", "INOUT")
            name = pnode[2:]
            nodes: Set[str] = set()
            edges: List[Tuple[str, str]] = []
            endpoints: List[dict] = []
            max_depth = 0

            if direction in ("INPUT", "INOUT"):
                r = self._trace(pnode, self.fwd, edge_dir_forward=True)
                nodes |= r["nodes"]; edges += r["edges"]
                endpoints += r["endpoints"]; max_depth = max(max_depth, r["max_depth"])
            if direction in ("OUTPUT", "INOUT"):
                r = self._trace(pnode, self.rev, edge_dir_forward=False)
                nodes |= r["nodes"]; edges += r["edges"]
                endpoints += r["endpoints"]; max_depth = max(max_depth, r["max_depth"])

            # dedupe endpoints by (instance, pin, type)
            seen = set()
            uniq = []
            for e in endpoints:
                key = (e["instance"], e["pin"], e["type"])
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(e)
            n_flop = sum(1 for e in uniq if e["type"] == "flop")
            n_mem = sum(1 for e in uniq if e["type"] == "memory")
            n_port = sum(1 for e in uniq if e["type"] == "port")

            self.cones[name] = {
                "direction": direction,
                "port_x": attrs.get("x"), "port_y": attrs.get("y"),
                "nodes": nodes,
                "edges": set(edges),
                "endpoints": uniq,
                "n_flop_endpoints": n_flop,
                "n_memory_endpoints": n_mem,
                "n_port_endpoints": n_port,
                "cone_instances": sum(
                    1 for n in nodes
                    if self.node_attrs.get(n, {}).get("ntype") == "instance"),
                "max_depth": max_depth,
            }

    # ---- union graph --------------------------------------------------
    def union_nodes_edges(self) -> Tuple[Set[str], Set[Tuple[str, str]]]:
        nodes: Set[str] = set()
        edges: Set[Tuple[str, str]] = set()
        for c in self.cones.values():
            nodes |= c["nodes"]
            edges |= c["edges"]
        return nodes, edges
