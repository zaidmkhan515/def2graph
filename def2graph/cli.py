"""Command-line interface.

Example:
    python -m def2graph --def design.def \
        --lef tech.lef --lef stdcell.lef \
        --lib stdcell.lib --lib sram.lib \
        --out out/design --formats parquet gpickle graphml pyg json
"""
from __future__ import annotations
import argparse
import glob
import sys
from typing import List

from .build import build_from_files, DEFAULT_FORMATS


def _expand(paths: List[str]) -> List[str]:
    out: List[str] = []
    for p in paths or []:
        g = glob.glob(p)
        out.extend(g if g else [p])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="def2graph",
        description="Convert DEF/LEF/Liberty into a port-bounded logic-cone graph.")
    ap.add_argument("--def", dest="def_path", required=True, help="DEF file")
    ap.add_argument("--lef", dest="lef", action="append", default=[],
                    help="LEF file (repeatable; globs ok)")
    ap.add_argument("--lib", dest="lib", action="append", default=[],
                    help="Liberty file (repeatable; globs ok)")
    ap.add_argument("--out", dest="out", default="out", help="output directory")
    ap.add_argument("--formats", nargs="+", default=DEFAULT_FORMATS,
                    choices=["parquet", "gpickle", "graphml", "pyg", "json"],
                    help="export formats")
    ap.add_argument("--no-subgraph-json", action="store_true",
                    help="omit full node/edge lists from cones.json (summary only)")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    lefs = _expand(args.lef)
    libs = _expand(args.lib)

    try:
        _, summary = build_from_files(
            def_path=args.def_path, lef_paths=lefs, lib_paths=libs,
            outdir=args.out, formats=args.formats,
            include_subgraph_json=not args.no_subgraph_json,
            verbose=not args.quiet)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if not args.quiet:
        print("\n== summary ==")
        gs = summary["graph_stats"]
        print(f" design           : {summary['design']}")
        print(f" instances        : {gs['instances']}")
        print(f" signal nets      : {gs['signal_nets']}")
        print(f" signal ports     : {gs['ports_signal']} / {gs['ports_total']}")
        print(f" union graph      : {summary['union_graph']['nodes']} nodes, "
              f"{summary['union_graph']['edges']} edges")
        print(f" flop endpoints   : {summary['cell_boundaries']['flop_endpoints_total']}")
        print(f" memory endpoints : {summary['cell_boundaries']['memory_endpoints_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
