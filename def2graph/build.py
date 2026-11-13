"""End-to-end: parse DEF/LEF/LIB -> classify -> cone graph -> export."""
from __future__ import annotations
import os
import time
from typing import List, Optional

from .lefparse import parse_lefs
from .libparse import parse_liberties
from .defparse import parse_def
from .model import CellLibrary
from .conegraph import ConeGraph
from .export import export_all

DEFAULT_FORMATS = ["parquet", "gpickle", "graphml", "pyg", "json"]


def build_from_files(def_path: str,
                     lef_paths: Optional[List[str]] = None,
                     lib_paths: Optional[List[str]] = None,
                     outdir: str = "out",
                     formats: Optional[List[str]] = None,
                     include_subgraph_json: bool = True,
                     verbose: bool = True):
    formats = formats or DEFAULT_FORMATS
    lef_paths = lef_paths or []
    lib_paths = lib_paths or []
    t0 = time.time()

    def say(msg):
        if verbose:
            print(msg, flush=True)

    # DEF is the one required input; fail clearly rather than with a traceback.
    if not os.path.isfile(def_path):
        raise FileNotFoundError(f"DEF file not found: {def_path}")

    say(f"[1/5] Parsing {len(lef_paths)} LEF file(s) ...")
    lef = parse_lefs(lef_paths)
    for w in lef.warnings:
        say(f"      WARN: {w}")
    say(f"      macros: {len(lef.macros)}  (dbu/micron={lef.dbu_microns})")

    say(f"[2/5] Parsing {len(lib_paths)} Liberty file(s) ...")
    lib_raw = parse_liberties(lib_paths)
    for w in lib_raw.warnings:
        say(f"      WARN: {w}")
    say(f"      lib cells: {len(lib_raw.cells)}")
    if lib_paths and not lib_raw.cells:
        say("      NOTE: no Liberty cells loaded -> flops cannot be detected "
            "(cones will not stop at sequential elements).")

    say("[3/5] Merging cell library + classifying ...")
    celllib = CellLibrary.from_parsed(lef, lib_raw)
    s = celllib.stats
    say(f"      cells={len(celllib.cells)} flop={s['flop']} memory={s['memory']} "
        f"comb={s['comb']} unknown={s['unknown']}")

    say(f"[4/5] Parsing DEF: {def_path}")
    defd = parse_def(def_path)
    say(f"      design={defd.design} dbu={defd.dbu_microns} "
        f"components={len(defd.components)} ports={len(defd.ports)} "
        f"nets={len(defd.nets)}")

    cg = ConeGraph.from_def(defd, celllib, dbu=defd.dbu_microns)
    cg.trace_all_ports()
    for w in cg.warnings:            # ConeGraph's own warnings (e.g. unknown cells)
        say(f"      WARN: {w}")
    # file-level parse warnings were already printed above; prepend them so they
    # also appear in the exported summary.json
    cg.warnings = lef.warnings + lib_raw.warnings + cg.warnings

    say(f"[5/5] Exporting -> {outdir}  formats={formats}")
    n_warn_before = len(cg.warnings)
    written, summary = export_all(cg, outdir, formats, include_subgraph_json)
    for w in cg.warnings[n_warn_before:]:   # warnings raised during export
        say(f"      WARN: {w}")
    dt = time.time() - t0
    say(f"      wrote {len(written)} file(s) in {dt:.1f}s")
    summary["elapsed_sec"] = round(dt, 2)
    summary["cell_library_stats"] = celllib.stats
    return cg, summary
