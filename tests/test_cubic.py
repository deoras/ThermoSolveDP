"""Peng-Robinson / SRK property package: k_ij handling, fast flashes, tabulated exchangers."""
import math
import sys
from pathlib import Path

import CoolProp
import pytest
from CoolProp.CoolProp import AbstractState

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thermosolvedp import examples  # noqa: E402
from thermosolvedp.engine import Fluid, Material, solve  # noqa: E402
from thermosolvedp.engine.thermo import (current_package, flash, kij_for, pair_key,  # noqa: E402
                                         set_property_package)
from thermosolvedp.engine.unitops import HeatExchanger  # noqa: E402

MIX = examples.MR_MIX


@pytest.fixture(autouse=True)
def reset_package():
    yield
    set_property_package("HEOS")


def test_kij_fitted_to_reference_reproduces_bubble_pressure():
    set_property_package("PR")
    k, src = kij_for("R23", "R14")
    assert src == "fitted" and 0.08 < k < 0.15          # strongly non-ideal pair
    T = 170.5
    ref = AbstractState("HEOS", "R23&R14"); ref.set_mole_fractions([0.5, 0.5]); ref.update(CoolProp.QT_INPUTS, 0, T)
    f = Fluid.parse("R23[0.5]&R14[0.5]")
    st = flash(f, "TQ", T, 0.0)
    assert abs(st.P / ref.p() - 1) < 0.02


def test_missing_pairs_are_estimated_and_user_values_win():
    set_property_package("PR")
    assert kij_for("Argon", "R14")[1] == "estimated"
    set_property_package("PR", {pair_key("R14", "Argon"): 0.07})
    assert kij_for("Argon", "R14") == (0.07, "user")


def test_fast_PH_matches_coolprop_cubic_flash():
    set_property_package("PR")
    f = Fluid.parse(MIX)
    s1 = flash(f, "PT", 20e5, 303.15)
    s2 = flash(f, "PH", 2e5, s1.h)
    A = AbstractState("PR", "&".join(f.components))
    A.set_mole_fractions(list(f.x))
    for i in range(len(f.components)):
        for j in range(i + 1, len(f.components)):
            k, _ = kij_for(f.components[i], f.components[j])
            if k:
                A.set_binary_interaction_double(i, j, "kij", k)
    A.update(CoolProp.HmassP_INPUTS, s1.h, 2e5)
    assert abs(s2.T - A.T()) < 1e-3
    assert abs(s2.h - s1.h) < 1e-2


def test_tabulated_exchanger_profile_matches_exact_flashes():
    set_property_package("PR")
    f = Fluid.parse(MIX)
    hot = Material.from_PT(f, 0.01, 20e5, 303.15)
    cold = Material.from_PT(f, 0.01, 2e5, 173.15)
    b = HeatExchanger("b1", "hx")
    b.values.update(spec="Minimum approach temperature", dTmin=3.0)
    out = b.run({"hot_in": hot, "cold_in": cold})
    q_hot = hot.m * (hot.h - out["hot_out"].h)
    q_cold = cold.m * (out["cold_out"].h - cold.h)
    assert math.isclose(q_hot, q_cold, rel_tol=1e-9)
    pr = b.profile
    Q = pr["q"][-1]
    for k in (0, len(pr["q"]) // 3, 2 * len(pr["q"]) // 3, len(pr["q"]) - 1):   # spot-check against exact flashes
        q = pr["q"][k]
        Th = flash(f, "PH", hot.P, hot.h - q / hot.m).T
        Tc = flash(f, "PH", cold.P, cold.h + (Q - q) / cold.m).T
        assert abs(Th - pr["Th"][k]) < 0.15 and abs(Tc - pr["Tc"][k]) < 0.15
    assert abs(min(a - c for a, c in zip(pr["Th"], pr["Tc"])) - 3.0) < 1e-3


def test_mixed_refrigerant_cycle_converges_and_flags_estimates():
    fs = examples.mixed_refrigerant()
    rep = solve(fs)
    assert rep.converged, rep.error
    assert current_package().method == "PR"
    warn = " ".join(t for lvl, t in rep.messages if lvl == "warning")
    assert "k_ij = 0 was assumed for 13" in warn
    ev = next(b for b in fs.blocks.values() if b.TYPE == "heater")
    assert ev.energy["heat"] > 0                       # net refrigeration at -100 C
    s = {r.label: r.value for r in rep.summary}
    assert math.isclose(s["Heat removed (coolers, flashes)"],
                        s["Work input (compressors, pumps)"] + s["Heat added (heaters, flashes)"], rel_tol=1e-6)


def test_reference_model_error_points_to_cubic_option():
    set_property_package("HEOS")
    with pytest.raises(Exception, match="Peng-Robinson"):
        Material.from_PT(Fluid.parse("Argon[0.5]&R14[0.5]"), 1.0, 1e5, 300.0)


def test_air_is_expanded_for_cubic_feeds():
    from thermosolvedp.engine import Flowsheet
    fs = Flowsheet()
    fs.settings["property_method"] = "SRK"
    f = fs.add_block("feed"); f.values.update(fluid="Air", T=300.0, P=1e5, m=1.0)
    p = fs.add_block("product")
    fs.connect(f.uid, "out", p.uid, "in")
    rep = solve(fs)
    assert rep.converged, rep.error
    assert "Nitrogen" in next(iter(fs.streams.values())).material.fluid.components
