"""Engine tests: each block is checked against a direct CoolProp hand calculation."""
import math
import sys
from pathlib import Path

import CoolProp.CoolProp as CP
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thermosolvedp import examples  # noqa: E402
from thermosolvedp.engine import Flowsheet, Fluid, Material, flash, solve  # noqa: E402
from thermosolvedp.engine.unitops import (Compressor, Expander, Flash, HeatExchanger, Heater, Mixer,  # noqa: E402
                                       Splitter, UnitOpError, Valve)

C = 273.15
BAR = 1e5
WATER = Fluid.parse("Water")
N2 = Fluid.parse("N2")


def run(block_cls, inlets, **values):
    b = block_cls("b1", "test")
    b.values.update(values)
    return b, b.run(inlets)


def test_fluid_parsing_and_aliases():
    assert Fluid.parse("water").components == ("Water",)
    f = Fluid.parse("Methane[0.9]&Ethane[0.1]")
    assert f.components == ("Ethane", "Methane") and math.isclose(sum(f.x), 1.0)
    assert math.isclose(dict(zip(f.components, f.x))["Methane"], 0.9)
    with pytest.raises(Exception):
        Fluid.parse("NotAFluid")


def test_compressor_matches_isentropic_definition():
    inp = Material.from_PT(N2, 2.0, 1 * BAR, 300)
    b, out = run(Compressor, {"in": inp}, spec="Outlet pressure", P_out=5 * BAR, eta=0.8)
    h1, s1 = CP.PropsSI("H", "P", 1e5, "T", 300, "N2"), CP.PropsSI("S", "P", 1e5, "T", 300, "N2")
    h2s = CP.PropsSI("H", "P", 5e5, "S", s1, "N2")
    h2 = h1 + (h2s - h1) / 0.8
    assert math.isclose(out["out"].h, h2, rel_tol=1e-6)
    assert math.isclose(b.energy["work"], 2.0 * (h2 - h1), rel_tol=1e-6)


def test_expander_and_pressure_ratio():
    inp = Material.from_PT(WATER, 1.0, 50 * BAR, 773.15)
    b, out = run(Expander, {"in": inp}, spec="Pressure ratio", ratio=10.0, eta=0.9)
    assert math.isclose(out["out"].P, 5 * BAR)
    assert b.energy["work"] < 0


def test_valve_is_isenthalpic_and_cools_nitrogen():
    inp = Material.from_PT(N2, 1.0, 200 * BAR, 300)
    b, out = run(Valve, {"in": inp}, spec="Outlet pressure", P_out=1 * BAR)
    assert math.isclose(out["out"].h, inp.h, rel_tol=1e-9)
    assert out["out"].T < inp.T - 20       # Joule-Thomson cooling


def test_heater_duty_and_vapor_fraction_specs():
    inp = Material.from_PQ(WATER, 1.0, 1 * BAR, 0.0)
    b, out = run(Heater, {"in": inp}, spec="Outlet vapor fraction", Q_out=1.0)
    hfg = CP.PropsSI("H", "P", 1e5, "Q", 1, "Water") - CP.PropsSI("H", "P", 1e5, "Q", 0, "Water")
    assert math.isclose(b.energy["heat"], hfg, rel_tol=1e-6)
    b, out = run(Heater, {"in": inp}, spec="Duty", duty=hfg / 2)
    assert math.isclose(out["out"].state.Q, 0.5, abs_tol=1e-6)


def test_heat_exchanger_balance_and_min_approach():
    hot = Material.from_PT(WATER, 1.0, 5 * BAR, 420)
    cold = Material.from_PT(WATER, 1.5, 5 * BAR, 300)
    b, out = run(HeatExchanger, {"hot_in": hot, "cold_in": cold}, spec="Minimum approach temperature", dTmin=10.0)
    q_hot = hot.m * (hot.h - out["hot_out"].h)
    q_cold = cold.m * (out["cold_out"].h - cold.h)
    assert math.isclose(q_hot, q_cold, rel_tol=1e-9)
    dts = [a - c for a, c in zip(b.profile["Th"], b.profile["Tc"])]
    assert math.isclose(min(dts), 10.0, abs_tol=1e-3)


def test_heat_exchanger_detects_temperature_cross():
    hot = Material.from_PT(WATER, 0.5, 5 * BAR, 350)    # too little hot flow to heat the cold side by 45 K
    cold = Material.from_PT(WATER, 1.0, 5 * BAR, 300)
    with pytest.raises(UnitOpError, match="cross"):
        run(HeatExchanger, {"hot_in": hot, "cold_in": cold}, spec="Cold outlet temperature", T_cold_out=345)


def test_flash_pure_fluid_split_by_quality():
    inp = Material.from_PQ(WATER, 2.0, 1 * BAR, 0.25)
    b, out = run(Flash, {"in": inp})
    assert math.isclose(out["vapor"].m, 0.5, rel_tol=1e-6)
    assert math.isclose(out["liquid"].m, 1.5, rel_tol=1e-6)
    assert abs(b.energy["heat"]) < 1e-3


def test_flash_mixture_component_balance():
    f = Fluid.parse("Methane[0.8]&Ethane[0.2]")
    inp = Material.from_PT(f, 1.0, 20 * BAR, 200)
    b, out = run(Flash, {"in": inp})
    v, l = out["vapor"], out["liquid"]
    assert math.isclose(v.m + l.m, 1.0, rel_tol=1e-9)
    for i, c in enumerate(f.components):        # moles of each component are conserved
        n_in = inp.n * f.x[i]
        n_out = v.n * v.fluid.x[v.fluid.components.index(c)] + l.n * l.fluid.x[l.fluid.components.index(c)]
        assert math.isclose(n_in, n_out, rel_tol=1e-6)
    assert dict(zip(v.fluid.components, v.fluid.x))["Methane"] > 0.8 > dict(zip(l.fluid.components, l.fluid.x))["Methane"]


def test_mixer_energy_balance_and_composition():
    a = Material.from_PT(N2, 1.0, 2 * BAR, 300)
    o2 = Fluid.parse("Oxygen")
    b_ = Material.from_PT(o2, 0.3, 2 * BAR, 350)
    b, out = run(Mixer, {"in1": a, "in2": b_})
    o = out["out"]
    assert math.isclose(o.m, 1.3)
    assert math.isclose(o.H, a.H + b_.H, rel_tol=1e-9)
    assert set(o.fluid.components) == {"Nitrogen", "Oxygen"}


def test_splitter():
    inp = Material.from_PT(N2, 2.0, 2 * BAR, 300)
    b, out = run(Splitter, {"in": inp}, n_out=3, frac1=0.2, frac2=0.3)
    assert [round(out[f"out{i}"].m, 9) for i in (1, 2, 3)] == [0.4, 0.6, 1.0]


def test_rankine_efficiency_against_hand_calculation():
    fs = examples.rankine()
    rep = solve(fs)
    assert rep.converged
    P_lo, P_hi, T3, f = 0.1e5, 100e5, 773.15, "Water"
    h1 = CP.PropsSI("H", "P", P_lo, "Q", 0, f); s1 = CP.PropsSI("S", "P", P_lo, "Q", 0, f)
    h2 = h1 + (CP.PropsSI("H", "P", P_hi, "S", s1, f) - h1) / 0.75
    h3 = CP.PropsSI("H", "P", P_hi, "T", T3, f); s3 = CP.PropsSI("S", "P", P_hi, "T", T3, f)
    h4 = h3 - 0.85 * (h3 - CP.PropsSI("H", "P", P_lo, "S", s3, f))
    eta = ((h3 - h4) - (h2 - h1)) / (h3 - h2)
    got = {r.label: r.value for r in rep.summary}["Thermal efficiency (net work / heat added)"]
    assert math.isclose(got, eta, rel_tol=1e-6)


def test_linde_recycle_converges_with_mass_balance():
    fs = examples.linde_hampson()
    rep = solve(fs)
    assert rep.converged, rep.error
    s = {r.label: r.value for r in rep.summary}
    assert abs(s["Mass balance error"]) < 1e-5
    assert abs(s["Energy balance error"]) < 1e-5
    lin = next(x for x in fs.streams.values() if x.name == "LIN").material
    assert math.isclose(lin.m, 0.1, rel_tol=1e-4)


@pytest.mark.parametrize("method", ["Broyden", "Wegstein"])
def test_linde_converges_with_each_accelerator(method):
    fs = examples.linde_hampson()
    fs.settings.update(method=method, max_iter=400)
    assert solve(fs).converged


def test_closed_loop_without_estimate_reports_clear_error():
    fs = examples.rankine()
    for s in fs.streams.values():
        s.estimate["enabled"] = False
    rep = solve(fs)
    assert not rep.converged and "estimate" in rep.error


def test_unconnected_inlet_is_reported():
    fs = Flowsheet()
    fs.add_block("compressor")
    rep = solve(fs)
    assert not rep.converged and "not connected" in rep.error


def test_save_load_roundtrip(tmp_path):
    fs = examples.linde_hampson()
    p = tmp_path / "x.tpf"
    fs.save(p)
    fs2 = Flowsheet.load(p)
    assert solve(fs2).converged
    new = fs2.add_block("heater")
    assert new.uid not in {b.uid for b in fs.blocks.values()}


def test_air_is_expanded_to_components_when_mixed_with_co2():
    f = Fluid.parse("Air[0.92]&CarbonDioxide[0.08]")
    x = dict(zip(f.components, f.x))
    assert set(x) == {"Nitrogen", "Oxygen", "Argon", "CarbonDioxide"}
    assert math.isclose(x["CarbonDioxide"], 0.08) and math.isclose(x["Nitrogen"], 0.92 * 0.7812)
    st = Material.from_PT(f, 1.0, 1e5, 300.0)
    assert math.isclose(st.T, 300.0)
    assert Fluid.parse("Air").components == ("Air",)          # pure air stays the fast pseudo-pure model


def test_mixing_pure_air_stream_with_co2_conserves_energy():
    air = Material.from_PT(Fluid.parse("Air"), 0.9, 2e5, 300.0)
    co2 = Material.from_PT(Fluid.parse("CO2"), 0.1, 2e5, 300.0)
    b, out = run(Mixer, {"in1": air, "in2": co2})
    # equal inlet temperatures -> outlet within a few tenths of a K (small real-gas mixing effect),
    # which fails by ~100 K if the two enthalpy reference states were not reconciled
    assert abs(out["out"].T - 300.0) < 0.5
    # outlet enthalpy equals the component-basis enthalpies of the inlets
    h_air = flash(Fluid.parse("Air").expanded(), "PT", 2e5, 300.0).h
    assert math.isclose(out["out"].H, 0.9 * h_air + co2.H, rel_tol=1e-9)


def test_pseudo_pure_refrigerant_blend_gives_clear_message():
    with pytest.raises(Exception, match="pseudo-pure"):
        Material.from_PT(Fluid.parse("R410A[0.5]&CO2[0.5]"), 1.0, 1e5, 300.0)


def test_air_co2_flowsheet_energy_balance():
    fs = Flowsheet()
    a = fs.add_block("feed"); a.values.update(fluid="Air", T=300.0, P=2e5, m=0.9)
    c = fs.add_block("feed"); c.values.update(fluid="CarbonDioxide", T=350.0, P=2e5, m=0.1)
    mx = fs.add_block("mixer")
    k = fs.add_block("compressor"); k.values.update(P_out=6e5)
    pr = fs.add_block("product")
    fs.connect(a.uid, "out", mx.uid, "in1"); fs.connect(c.uid, "out", mx.uid, "in2")
    fs.connect(mx.uid, "out", k.uid, "in"); fs.connect(k.uid, "out", pr.uid, "in")
    rep = solve(fs)
    assert rep.converged, rep.error
    s = {r.label: r.value for r in rep.summary}
    assert abs(s["Energy balance error"]) < 1e-6
