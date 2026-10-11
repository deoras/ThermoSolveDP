"""CSV export of streams, block results and energy summary."""
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thermosolvedp import examples  # noqa: E402
from thermosolvedp.engine import UnitSystem, solve  # noqa: E402
from thermosolvedp.engine import export as ex  # noqa: E402


def read(p):
    raw = Path(p).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")              # BOM so Excel shows °C correctly
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def test_write_all_three_files(tmp_path):
    fs = examples.linde_hampson()
    rep = solve(fs)
    u = UnitSystem(T="°C", P="bar", m="kg/s", power="kW")
    files = ex.write_all(fs, rep, u, tmp_path / "linde.csv")
    assert [f.name for f in files] == ["linde_streams.csv", "linde_blocks.csv", "linde_energy.csv"]

    streams = read(files[0])
    assert streams[0][:2] == ["Property", "Unit"] and len(streams[0]) == 2 + len(fs.streams)
    row = {r[0]: r for r in streams}
    col = streams[0].index("LIN")
    lin = next(s for s in fs.streams.values() if s.name == "LIN").material
    assert row["Temperature"][1] == "°C"
    assert math.isclose(float(row["Temperature"][col]), lin.T - 273.15, abs_tol=1e-6)
    assert math.isclose(float(row["Mass flow"][col]), lin.m, rel_tol=1e-8)
    assert row["Pressure"][1] == "bar" and math.isclose(float(row["Pressure"][col]), lin.P / 1e5)

    blocks = read(files[1])
    assert blocks[0] == ["Block", "Type", "Section", "Item", "Value", "Unit"]
    hx = [r for r in blocks if r[0] == "Recuperator"]
    assert any(r[2] == "Specification" and r[3] == "Minimum approach ΔT" and float(r[4]) == 3.0 for r in hx)
    duty = next(r for r in hx if r[2] == "Result" and r[3] == "Duty")
    assert duty[5] == "kW" and float(duty[4]) > 0

    energy = read(files[2])
    assert energy[0][2] == "Work into fluid [kW]"
    totals = {r[0]: r for r in energy if r}
    assert math.isclose(float(totals["Total feed flow"][1]), 0.1, rel_tol=1e-9)
    assert totals["Converged"][1] == "yes"


def test_mixture_compositions_and_units(tmp_path):
    fs = examples.gas_flash()
    rep = solve(fs)
    u = UnitSystem(T="K", P="MPa", m="kg/h", power="W")
    p = ex.write_stream_table(fs, u, tmp_path / "s.csv")
    rows = {r[0]: r for r in read(p)}
    assert rows["Temperature"][1] == "K" and rows["Mass flow"][1] == "kg/h"
    assert "Mole fraction Methane" in rows
    gas_col = read(p)[0].index("Gas")
    assert float(rows["Mole fraction Methane"][gas_col]) > 0.85
    assert rep.converged
