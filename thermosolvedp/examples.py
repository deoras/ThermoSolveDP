"""Built-in example flowsheets. Run ``python -m thermosolvedp.examples`` to (re)write the .tpf files."""
from __future__ import annotations

from pathlib import Path

from .engine.flowsheet import Flowsheet
from .gui.geometry import place

C = 273.15
BAR = 1e5


def rankine() -> Flowsheet:
    fs = Flowsheet()
    pump = fs.add_block("pump", name="Feed pump")
    pump.values.update(spec="Outlet pressure", P_out=100 * BAR, eta=0.75)
    boil = fs.add_block("heater", name="Boiler")
    boil.values.update(spec="Outlet temperature", T_out=500 + C)
    turb = fs.add_block("expander", name="Turbine")
    turb.values.update(spec="Outlet pressure", P_out=0.1 * BAR, eta=0.85)
    cond = fs.add_block("cooler", name="Condenser")
    cond.values.update(spec="Outlet vapor fraction", Q_out=0.0)
    for i, b in enumerate((pump, boil, turb, cond)):
        place(b, 40 + 170 * i, 100)
    s1 = fs.connect(cond.uid, "out", pump.uid, "in", "1")
    s1.estimate.update(enabled=True, fluid="Water", T=45 + C, P=0.1 * BAR, m=10.0)
    fs.connect(pump.uid, "out", boil.uid, "in", "2")
    fs.connect(boil.uid, "out", turb.uid, "in", "3")
    fs.connect(turb.uid, "out", cond.uid, "in", "4")
    return fs


def refrigeration() -> Flowsheet:
    fs = Flowsheet()
    comp = fs.add_block("compressor", name="Compressor")
    comp.values.update(spec="Outlet pressure", P_out=10 * BAR, eta=0.75)
    cond = fs.add_block("cooler", name="Condenser")
    cond.values.update(spec="Outlet vapor fraction", Q_out=0.0)
    valve = fs.add_block("valve", name="Expansion valve")
    valve.values.update(spec="Outlet pressure", P_out=2 * BAR)
    evap = fs.add_block("heater", name="Evaporator")
    evap.values.update(spec="Outlet temperature", T_out=-5 + C)
    for i, b in enumerate((comp, cond, valve, evap)):
        place(b, 40 + 170 * i, 100)
    fs.connect(comp.uid, "out", cond.uid, "in", "2")
    fs.connect(cond.uid, "out", valve.uid, "in", "3")
    fs.connect(valve.uid, "out", evap.uid, "in", "4")
    s1 = fs.connect(evap.uid, "out", comp.uid, "in", "1")
    s1.estimate.update(enabled=True, fluid="R134a", T=-5 + C, P=2 * BAR, m=0.05)
    return fs


def linde_hampson() -> Flowsheet:
    fs = Flowsheet()
    feed = fs.add_block("feed", name="Make-up N2")
    feed.values.update(fluid="Nitrogen", spec="Temperature & pressure", T=25 + C, P=1.0 * BAR, m=0.1)
    mix = fs.add_block("mixer", name="Mixer")
    k1 = fs.add_block("compressor", name="Stage 1")
    k1.values.update(spec="Outlet pressure", P_out=14 * BAR, eta=0.75)
    ic = fs.add_block("cooler", name="Intercooler")
    ic.values.update(spec="Outlet temperature", T_out=25 + C)
    k2 = fs.add_block("compressor", name="Stage 2")
    k2.values.update(spec="Outlet pressure", P_out=200 * BAR, eta=0.75)
    ac = fs.add_block("cooler", name="Aftercooler")
    ac.values.update(spec="Outlet temperature", T_out=25 + C)
    hx = fs.add_block("hx", name="Recuperator")
    hx.values.update(spec="Minimum approach temperature", dTmin=3.0)
    jt = fs.add_block("valve", name="JT valve")
    jt.values.update(spec="Outlet pressure", P_out=1.0 * BAR)
    fl = fs.add_block("flash", name="Separator")
    prod = fs.add_block("product", name="Liquid N2")

    y = 120
    place(feed, 0, y, "out")
    place(mix, 120, y, "in1")
    place(k1, 240, y)
    place(ic, 380, y)
    place(k2, 510, y)
    place(ac, 650, y)
    place(hx, 790, y, "hot_in")
    place(jt, 960, y)
    place(fl, 1090, y)
    place(prod, 1200, y + 90)

    fs.connect(feed.uid, "out", mix.uid, "in1", "1")
    fs.connect(mix.uid, "out", k1.uid, "in", "2")
    fs.connect(k1.uid, "out", ic.uid, "in", "3")
    fs.connect(ic.uid, "out", k2.uid, "in", "4")
    fs.connect(k2.uid, "out", ac.uid, "in", "5")
    fs.connect(ac.uid, "out", hx.uid, "hot_in", "6")
    fs.connect(hx.uid, "hot_out", jt.uid, "in", "7")
    fs.connect(jt.uid, "out", fl.uid, "in", "8")
    fs.connect(fl.uid, "liquid", prod.uid, "in", "LIN")
    vap = fs.connect(fl.uid, "vapor", hx.uid, "cold_in", "9")
    vap.estimate.update(enabled=True, fluid="Nitrogen", T=-190 + C, P=1.0 * BAR, m=1.0)
    fs.connect(hx.uid, "cold_out", mix.uid, "in2", "10")
    return fs


def gas_flash() -> Flowsheet:
    fs = Flowsheet()
    feed = fs.add_block("feed", name="Natural gas")
    feed.values.update(fluid="Methane[0.85]&Ethane[0.10]&Propane[0.05]", spec="Temperature & pressure",
                       T=25 + C, P=60 * BAR, m=1.0)
    cool = fs.add_block("cooler", name="Chiller")
    cool.values.update(spec="Outlet temperature", T_out=-35 + C)
    jt = fs.add_block("valve", name="JT valve")
    jt.values.update(spec="Outlet pressure", P_out=25 * BAR)
    fl = fs.add_block("flash", name="Cold separator")
    gas = fs.add_block("product", name="Lean gas")
    ngl = fs.add_block("product", name="NGL")
    y = 120
    place(feed, 0, y, "out")
    place(cool, 170, y)
    place(jt, 340, y)
    place(fl, 500, y)
    place(gas, 640, y - 90)
    place(ngl, 640, y + 90)
    fs.connect(feed.uid, "out", cool.uid, "in", "1")
    fs.connect(cool.uid, "out", jt.uid, "in", "2")
    fs.connect(jt.uid, "out", fl.uid, "in", "3")
    fs.connect(fl.uid, "vapor", gas.uid, "in", "Gas")
    fs.connect(fl.uid, "liquid", ngl.uid, "in", "Liquid")
    return fs


def gas_turbine() -> Flowsheet:
    """Simple-cycle gas turbine: compressor, methane combustor (conversion reactor), turbine."""
    fs = Flowsheet()
    air = fs.add_block("feed", name="Ambient air")
    air.values.update(fluid="Air", spec="Temperature & pressure", T=15 + C, P=1.01325 * BAR, m=20.0)
    comp = fs.add_block("compressor", name="Compressor")
    comp.values.update(spec="Pressure ratio", ratio=15.0, eta=0.86)
    fuel = fs.add_block("feed", name="Natural gas (CH4)")
    fuel.values.update(fluid="Methane", spec="Temperature & pressure", T=25 + C, P=20 * BAR, m=0.42)
    comb = fs.add_block("reactor", name="Combustor")
    comb.values.update(mode="Combustion (complete)", conversion=1.0, energy="Adiabatic", dP=0.5 * BAR)
    turb = fs.add_block("expander", name="Turbine")
    turb.values.update(spec="Outlet pressure", P_out=1.05 * BAR, eta=0.88)
    exh = fs.add_block("product", name="Exhaust")
    y = 120
    place(air, 0, y, "out")
    place(comp, 120, y)
    place(comb, 290, y, "in1")
    place(fuel, 150, y + 100, "out")
    place(turb, 440, y)
    place(exh, 590, y)
    fs.connect(air.uid, "out", comp.uid, "in", "1")
    fs.connect(comp.uid, "out", comb.uid, "in1", "2")
    fs.connect(fuel.uid, "out", comb.uid, "in2", "F")
    fs.connect(comb.uid, "out", turb.uid, "in", "3")
    fs.connect(turb.uid, "out", exh.uid, "in", "4")
    return fs


MR_MIX = "R245fa[0.15]&R227ea[0.10]&R125[0.15]&R32[0.10]&R23[0.20]&R14[0.20]&Argon[0.10]"


def mixed_refrigerant() -> Flowsheet:
    """Single-stage mixed-refrigerant JT cryocooler (Klimenko type), Peng-Robinson.

    The composition is illustrative. 13 of the 21 k_ij values have no data and are assumed 0.
    """
    fs = Flowsheet()
    fs.settings["property_method"] = "PR"
    k = fs.add_block("compressor", name="Compressor")
    k.values.update(spec="Outlet pressure", P_out=20 * BAR, eta=0.7)
    ac = fs.add_block("cooler", name="Aftercooler")
    ac.values.update(spec="Outlet temperature", T_out=30 + C)
    hx = fs.add_block("hx", name="Recuperator")
    hx.values.update(spec="Minimum approach temperature", dTmin=2.0)
    v = fs.add_block("valve", name="JT valve")
    v.values.update(spec="Outlet pressure", P_out=2 * BAR)
    ev = fs.add_block("heater", name="Evaporator (load)")
    ev.values.update(spec="Outlet temperature", T_out=-100 + C)
    y = 120
    place(k, 0, y)
    place(ac, 150, y)
    place(hx, 290, y, "hot_in")
    place(v, 470, y)
    place(ev, 590, y)
    s1 = fs.connect(hx.uid, "cold_out", k.uid, "in", "1")
    fs.connect(k.uid, "out", ac.uid, "in", "2")
    fs.connect(ac.uid, "out", hx.uid, "hot_in", "3")
    fs.connect(hx.uid, "hot_out", v.uid, "in", "4")
    fs.connect(v.uid, "out", ev.uid, "in", "5")
    s6 = fs.connect(ev.uid, "out", hx.uid, "cold_in", "6")
    s1.estimate.update(enabled=True, fluid=MR_MIX, T=-15 + C, P=2 * BAR, m=0.01)
    s6.estimate.update(enabled=True, fluid=MR_MIX, T=-100 + C, P=2 * BAR, m=0.01)
    return fs


EXAMPLES = {
    "Rankine steam cycle": ("rankine_cycle.tpf", rankine),
    "Vapor-compression refrigeration (R134a)": ("refrigeration_r134a.tpf", refrigeration),
    "Linde-Hampson nitrogen liquefier (recycle)": ("linde_hampson_n2.tpf", linde_hampson),
    "Natural gas JT + flash (mixture)": ("natural_gas_flash.tpf", gas_flash),
    "Gas turbine with methane combustor (reactor)": ("gas_turbine.tpf", gas_turbine),
    "Mixed-refrigerant JT cryocooler (7 components, Peng-Robinson)": ("mixed_refrigerant_pr.tpf", mixed_refrigerant),
}


def examples_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "examples"


def write_all(folder: Path | None = None) -> list[Path]:
    folder = folder or examples_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for _title, (fname, fn) in EXAMPLES.items():
        p = folder / fname
        fn().save(p)
        out.append(p)
    return out


if __name__ == "__main__":
    for p in write_all():
        print("wrote", p)
