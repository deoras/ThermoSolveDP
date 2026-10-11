"""CSV export of results: stream table, block results and energy summary.

Numbers are written as plain numbers (in the chosen display units) with the unit in its
own column or header, so the files open directly in Excel / Numbers / pandas. Files are
UTF-8 with a byte-order mark so Excel shows symbols such as °C correctly.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

from .flowsheet import Flowsheet
from .params import Result
from .solver import SolveReport
from .units import UnitSystem


def _num(u: UnitSystem, qty: Optional[str], v):
    """Value in display units, as a number where possible."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, str):
        return v
    d = u.to_display(qty, float(v))
    return float(f"{d:.10g}")


def _unit(u: UnitSystem, qty: Optional[str]) -> str:
    lab = u.label(qty)
    return "" if lab == "–" else lab


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)


# ---------------------------------------------------------------------------------------
# stream table
# ---------------------------------------------------------------------------------------

def stream_table_rows(fs: Flowsheet, u: UnitSystem) -> list[list]:
    streams = sorted((s for s in fs.streams.values() if s.material is not None), key=lambda s: _natural(s.name))
    header = ["Property", "Unit"] + [s.name for s in streams]
    spec = [
        ("From", "", lambda s, m: fs.blocks[s.src].name),
        ("To", "", lambda s, m: fs.blocks[s.dst].name),
        ("Temperature", "T", lambda s, m: m.T),
        ("Pressure", "P", lambda s, m: m.P),
        ("Mass flow", "m", lambda s, m: m.m),
        ("Molar flow", "kmol/h", lambda s, m: m.n * 3.6),
        ("Phase", "", lambda s, m: m.state.phase),
        ("Vapor fraction (molar)", "frac", lambda s, m: m.state.Q),
        ("Vapor fraction (mass)", "frac", lambda s, m: m.state.vapor_mass_fraction if m.state.Q is not None else None),
        ("Enthalpy", "h", lambda s, m: m.state.h),
        ("Entropy", "s", lambda s, m: m.state.s),
        ("Density", "rho", lambda s, m: m.state.rho),
        ("Cp", "cp", lambda s, m: m.state.cp),
        ("Molar mass", "M", lambda s, m: m.fluid.molar_mass),
        ("Fluid", "", lambda s, m: m.fluid.label() if m.fluid.is_pure else "mixture"),
    ]
    rows = [header]
    for label, qty, get in spec:
        if qty == "kmol/h":
            rows.append([label, "kmol/h"] + [float(f"{get(s, s.material):.10g}") for s in streams])
        elif qty == "":
            rows.append([label, ""] + [get(s, s.material) for s in streams])
        else:
            rows.append([label, _unit(u, qty)] + [_num(u, qty, get(s, s.material)) for s in streams])
    comps = sorted({c for s in streams for c in s.material.fluid.components})
    if any(not s.material.fluid.is_pure for s in streams):
        for c in comps:
            row = [f"Mole fraction {c}", "mol/mol"]
            for s in streams:
                x = dict(zip(s.material.fluid.components, s.material.fluid.x))
                row.append(float(f"{x.get(c, 0.0):.10g}"))
            rows.append(row)
    return rows


def write_stream_table(fs: Flowsheet, u: UnitSystem, path) -> Path:
    _write(path, stream_table_rows(fs, u))
    return Path(path)


# ---------------------------------------------------------------------------------------
# block results (specifications + results, long format)
# ---------------------------------------------------------------------------------------

def block_rows(fs: Flowsheet, u: UnitSystem) -> list[list]:
    rows = [["Block", "Type", "Section", "Item", "Value", "Unit"]]
    for b in fs.blocks.values():
        for p in b.visible_params():
            v = b.values[p.key]
            if p.kind in ("float",):
                rows.append([b.name, b.LABEL, "Specification", p.label, _num(u, p.qty, v), _unit(u, p.qty)])
            else:
                label = "Specification type" if p.label == "Specification" else p.label
                rows.append([b.name, b.LABEL, "Specification", label, v, ""])
        for r in b.results:
            rows.append([b.name, b.LABEL, "Result", r.label, _num(u, r.qty, r.value), _unit(u, r.qty)])
        if b.energy["work"]:
            rows.append([b.name, b.LABEL, "Result", "Work into fluid", _num(u, "power", b.energy["work"]),
                         _unit(u, "power")])
        if b.energy["heat"] and abs(b.energy["heat"]) > 1e-3:
            rows.append([b.name, b.LABEL, "Result", "Heat into fluid", _num(u, "power", b.energy["heat"]),
                         _unit(u, "power")])
        for w in b.warnings:
            rows.append([b.name, b.LABEL, "Warning", w, "", ""])
        if b.error:
            rows.append([b.name, b.LABEL, "Error", b.error, "", ""])
    return rows


def write_block_results(fs: Flowsheet, u: UnitSystem, path) -> Path:
    _write(path, block_rows(fs, u))
    return Path(path)


# ---------------------------------------------------------------------------------------
# energy summary
# ---------------------------------------------------------------------------------------

def energy_rows(fs: Flowsheet, report: Optional[SolveReport], u: UnitSystem) -> list[list]:
    pu = _unit(u, "power")
    rows = [["Block", "Type", f"Work into fluid [{pu}]", f"Heat into fluid [{pu}]"]]
    for b in fs.blocks.values():
        w = b.energy["work"] if abs(b.energy["work"]) > 1e-3 else 0.0
        q = b.energy["heat"] if abs(b.energy["heat"]) > 1e-3 else 0.0
        if w or q:
            rows.append([b.name, b.LABEL, _num(u, "power", w), _num(u, "power", q)])
    rows.append([])
    rows.append(["Flowsheet totals", "Value", "Unit"])
    summary: list[Result] = report.summary if report is not None else []
    for r in summary:
        rows.append([r.label, _num(u, r.qty, r.value), _unit(u, r.qty)])
    from .thermo import METHODS
    rows.append([])
    rows.append(["Property method", METHODS.get(fs.settings.get("property_method", "HEOS"), ""), ""])
    if report is not None:
        rows.append(["Converged", "yes" if report.converged else "no", ""])
        rows.append(["Recycle iterations", report.iterations, ""])
    return rows


def write_energy_summary(fs: Flowsheet, report: Optional[SolveReport], u: UnitSystem, path) -> Path:
    _write(path, energy_rows(fs, report, u))
    return Path(path)


def write_all(fs: Flowsheet, report: Optional[SolveReport], u: UnitSystem, base) -> list[Path]:
    """Write <base>_streams.csv, <base>_blocks.csv and <base>_energy.csv."""
    base = Path(base)
    if base.suffix.lower() == ".csv":
        base = base.with_suffix("")
    stem = base.name
    for tail in ("_streams", "_blocks", "_energy"):
        if stem.endswith(tail):
            stem = stem[: -len(tail)]
    folder = base.parent
    return [
        write_stream_table(fs, u, folder / f"{stem}_streams.csv"),
        write_block_results(fs, u, folder / f"{stem}_blocks.csv"),
        write_energy_summary(fs, report, u, folder / f"{stem}_energy.csv"),
    ]
