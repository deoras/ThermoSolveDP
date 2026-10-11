"""Conversion reactor / combustor."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thermosolvedp import examples  # noqa: E402
from thermosolvedp.engine import Fluid, Material, solve  # noqa: E402
from thermosolvedp.engine.thermo import elements, set_property_package  # noqa: E402
from thermosolvedp.engine.unitops import Reactor, UnitOpError, parse_reactions  # noqa: E402

C = 273.15


@pytest.fixture(autouse=True)
def reset_package():
    yield
    set_property_package("HEOS")


def run(inlets, **values):
    r = Reactor("r1", "R")
    r.values.update(values)
    out = r.run(inlets)["out"]
    return r, out


def air(m=20.0, T=669.28, P=15.2e5):
    return Material.from_PT(Fluid.parse("Nitrogen[0.7812]&Oxygen[0.2096]&Argon[0.0092]"), m, P, T)


def ch4(m=0.42, T=298.15, P=15.2e5):
    return Material.from_PT(Fluid.parse("Methane"), m, P, T)


def res(r):
    return {x.label: x.value for x in r.results}


def test_methane_lhv_and_element_balance():
    r, out = run({"in1": air(), "in2": ch4()})
    assert math.isclose(res(r)["Fuel LHV"], 50.01e6, rel_tol=1e-3)          # 50.0 MJ/kg textbook
    assert math.isclose(out.m, 20.42, rel_tol=1e-12)
    # atoms in = atoms out
    def atoms(mats):
        tot = {}
        for m in mats:
            for c, x in zip(m.fluid.components, m.fluid.x):
                for e, k in (elements(c) or {}).items():
                    tot[e] = tot.get(e, 0.0) + m.n * x * k
        return tot
    a_in, a_out = atoms([air(), ch4()]), atoms([out])
    for e in a_in:   # CoolProp molar masses are consistent to ~1e-6, and mass is conserved exactly
        assert math.isclose(a_in[e], a_out[e], rel_tol=1e-6)
    assert dict(zip(out.fluid.components, out.fluid.x)).get("Methane", 0.0) == 0.0


def test_adiabatic_flame_temperature_matches_cantera():
    # Same inlets in Cantera (gri30 NASA polynomials, frozen complete combustion): 1222.7 C
    r, out = run({"in1": air(), "in2": ch4()}, dP=0.5e5)
    assert abs(out.T - C - 1222.7) < 3.0


def test_outlet_temperature_mode_is_consistent_with_adiabatic():
    r, out = run({"in1": air(), "in2": ch4()})
    r2, out2 = run({"in1": air(), "in2": ch4()}, energy="Outlet temperature", T_out=out.T)
    assert abs(r2.energy["heat"]) < 1e-4 * res(r)["Heat released (LHV, 298 K)"]
    r3, out3 = run({"in1": air(), "in2": ch4()}, energy="Outlet temperature", T_out=out.T - 100)
    assert r3.energy["heat"] < 0                                              # heat must be removed


def test_fuel_rich_feed_is_limited_by_oxygen():
    r, out = run({"in1": air(m=1.0), "in2": ch4(m=0.2)})
    assert any("Fuel-rich" in w for w in r.warnings)
    x = dict(zip(out.fluid.components, out.fluid.x))
    assert x.get("Oxygen", 0.0) < 1e-12 and x["Methane"] > 0


def test_custom_two_step_equals_single_step():
    _, one = run({"in1": air(), "in2": ch4()}, mode="Custom reactions",
                 reactions="CH4 + 2 O2 -> CO2 + 2 H2O")
    _, two = run({"in1": air(), "in2": ch4()}, mode="Custom reactions",
                 reactions="CH4 + 1.5 O2 -> CO + 2 H2O ; X=1 ; key=CH4\nCO + 0.5 O2 -> CO2 ; X=1")
    assert abs(one.T - two.T) < 1e-3
    _, comb = run({"in1": air(), "in2": ch4()})
    assert abs(one.T - comb.T) < 1e-3


def test_reaction_parser_errors():
    with pytest.raises(UnitOpError, match="not balanced"):
        r = Reactor("r", "R")
        r.values.update(mode="Custom reactions", reactions="CH4 + O2 -> CO2 + 2 H2O")
        r.run({"in1": air(), "in2": ch4()})
    with pytest.raises(UnitOpError, match="unknown species"):
        parse_reactions("CH4 + 2 Unobtanium -> CO2")
    with pytest.raises(UnitOpError, match="key species"):
        parse_reactions("CH4 + 2 O2 -> CO2 + 2 H2O ; key=CO2")
    rx = parse_reactions("# comment\n C3H8 + 5 O2 -> 3 CO2 + 4 H2O ; X=0.5")
    assert rx[0]["X"] == 0.5 and rx[0]["key"] == "n-Propane"


def test_gas_turbine_example_balances():
    fs = examples.gas_turbine()
    rep = solve(fs)
    assert rep.converged, rep.error
    s = {r.label: r.value for r in rep.summary}
    assert abs(s["Energy balance error"]) < 1e-6
    assert abs(s["Mass balance error"]) < 1e-12
    assert 0.33 < s["Thermal efficiency (net work / fuel LHV)"] < 0.40
    assert not [m for lvl, m in rep.messages if lvl == "warning"]


def test_reactor_works_with_peng_robinson():
    fs = examples.gas_turbine()
    fs.settings["property_method"] = "PR"
    rep = solve(fs)
    assert rep.converged, rep.error
    r = next(b for b in fs.blocks.values() if b.TYPE == "reactor")
    assert abs(res(r)["Outlet temperature"] - C - 1221) < 10
