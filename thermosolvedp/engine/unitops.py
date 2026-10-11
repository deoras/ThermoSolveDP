"""Unit operation models.

Every block declares its parameters (``PARAMS``), its ports, and a
``calculate(inlets) -> outlets`` method working on :class:`Material` objects
in SI units. Results for display are stored in ``self.results``; energy
exchanged with the surroundings in ``self.energy`` (positive = into the fluid).
"""
from __future__ import annotations

import bisect
import math
from typing import Optional

from .material import Material
from .params import Param, Result, when
from .thermo import (FORMATION_ENTHALPY, Fluid, ThermoError, component_molar_mass, elements, flash, for_package,
                     formation_enthalpy_flow, formation_offset, species_name)


class UnitOpError(Exception):
    """A block could not be calculated with its current inputs/specifications."""


T_REF = 298.15
P_REF = 101325.0


class UnitOp:
    TYPE = "base"
    LABEL = "Block"
    PREFIX = "B"
    CATEGORY = "Other"
    DESCRIPTION = ""
    PARAMS: list[Param] = []

    def __init__(self, uid: str, name: str):
        self.uid = uid
        self.name = name
        self.values = {p.key: p.default for p in self.PARAMS}
        self.layout = {"x": 0.0, "y": 0.0, "flip": False}
        self.reset()

    # -- bookkeeping ---------------------------------------------------------------
    def reset(self) -> None:
        self.results: list[Result] = []
        self.warnings: list[str] = []
        self.error: Optional[str] = None
        self.energy = {"work": 0.0, "heat": 0.0}
        self.paths: list[dict] = []
        self.profile: Optional[dict] = None
        self.dangling: dict = {}          # outputs of unconnected outlet ports
        self.status = "none"          # none | ok | warning | error

    def param(self, key: str) -> Param:
        for p in self.PARAMS:
            if p.key == key:
                return p
        raise KeyError(key)

    def visible_params(self) -> list[Param]:
        return [p for p in self.PARAMS if p.is_visible(self.values)]

    def num(self, key: str) -> float:
        """Read a numeric parameter and validate it."""
        p = self.param(key)
        try:
            v = float(self.values[key])
        except (TypeError, ValueError):
            raise UnitOpError(f"'{p.label}' is not a number.")
        if not math.isfinite(v):
            raise UnitOpError(f"'{p.label}' must be a finite number.")
        if p.minv is not None and v < p.minv:
            raise UnitOpError(f"'{p.label}' must be ≥ {p.minv:g} (SI units).")
        if p.maxv is not None and v > p.maxv:
            raise UnitOpError(f"'{p.label}' must be ≤ {p.maxv:g} (SI units).")
        return v

    def set_value(self, key: str, value) -> None:
        p = self.param(key)
        if p.kind == "int":
            value = int(value)
        elif p.kind == "float":
            value = float(value)
        elif p.kind == "bool":
            value = bool(value)
        elif p.kind == "text":
            value = str(value)
        elif p.kind == "choice" and value not in p.choices:
            raise ValueError(f"{value!r} is not a valid choice for {p.label}")
        self.values[key] = value

    # -- ports ------------------------------------------------------------------------
    def inlet_ports(self) -> list[tuple[str, str]]:
        return [("in", "Inlet")]

    def outlet_ports(self) -> list[tuple[str, str]]:
        return [("out", "Outlet")]

    def optional_inlets(self) -> set[str]:
        return set()

    # -- calculation -------------------------------------------------------------------
    def calculate(self, inlets: dict[str, Material]) -> dict[str, Material]:
        raise NotImplementedError

    def run(self, inlets: dict[str, Material]) -> dict[str, Material]:
        self.reset()
        try:
            outs = self.calculate(inlets)
        except ThermoError as exc:
            self.status, self.error = "error", str(exc)
            raise UnitOpError(str(exc)) from exc
        except UnitOpError as exc:
            self.status, self.error = "error", str(exc)
            raise
        self.status = "warning" if self.warnings else "ok"
        return outs

    def add_path(self, a: Material, b: Material, kind: str) -> None:
        """Record a process path for plotting (kind: isobaric | compression | isenthalpic)."""
        self.paths.append({"fluid": a.fluid, "kind": kind, "P1": a.P, "h1": a.h, "P2": b.P, "h2": b.h,
                           "start": a.state, "end": b.state})

    # -- persistence ----------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"uid": self.uid, "type": self.TYPE, "name": self.name, "values": dict(self.values),
                "layout": dict(self.layout)}

    def load_values(self, values: dict) -> None:
        for p in self.PARAMS:
            if p.key in values:
                try:
                    self.set_value(p.key, values[p.key])
                except (TypeError, ValueError):
                    pass


def _outlet_P(block: UnitOp, P_in: float, dP_key: str = "dP") -> float:
    P = P_in - block.num(dP_key)
    if P <= 0:
        raise UnitOpError("Pressure drop is larger than the inlet pressure.")
    return P


# ===================================================================================
# Sources and sinks
# ===================================================================================

class Feed(UnitOp):
    TYPE, LABEL, PREFIX, CATEGORY = "feed", "Feed", "FEED", "Streams"
    DESCRIPTION = "Material source. Define the fluid, flow and two state variables."
    SPECS = ("Temperature & pressure", "Pressure & vapor fraction", "Temperature & vapor fraction")
    PARAMS = [
        Param("fluid", "Fluid", "fluid", "Water",
              tip="A CoolProp fluid name (e.g. Water, R134a, Nitrogen) or a mixture "
                  "with mole fractions, e.g. Methane[0.9]&Ethane[0.1]"),
        Param("spec", "Specification", "choice", SPECS[0], choices=SPECS),
        Param("T", "Temperature", "float", 298.15, qty="T", minv=1e-6,
              visible=when("spec", SPECS[0], SPECS[2])),
        Param("P", "Pressure", "float", 101325.0, qty="P", minv=1e-6, visible=when("spec", SPECS[0], SPECS[1])),
        Param("Q", "Vapor fraction", "float", 1.0, qty="frac", minv=0.0, maxv=1.0,
              visible=when("spec", SPECS[1], SPECS[2]), tip="0 = saturated liquid, 1 = saturated vapor (molar for mixtures)"),
        Param("m", "Mass flow", "float", 1.0, qty="m", minv=0.0),
    ]

    def inlet_ports(self):
        return []

    def fluid(self) -> Fluid:
        return for_package(Fluid.parse(self.values["fluid"]))

    def calculate(self, inlets):
        fluid = self.fluid()
        m = self.num("m")
        spec = self.values["spec"]
        if spec == self.SPECS[0]:
            mat = Material.from_PT(fluid, m, self.num("P"), self.num("T"))
        elif spec == self.SPECS[1]:
            mat = Material.from_PQ(fluid, m, self.num("P"), self.num("Q"))
        else:
            mat = Material.from_TQ(fluid, m, self.num("T"), self.num("Q"))
        self.results = [
            Result("Fluid", fluid.label()), Result("Temperature", mat.T, "T"),
            Result("Pressure", mat.P, "P"), Result("Phase", mat.state.phase),
            Result("Molar mass", fluid.molar_mass, "M"),
        ]
        return {"out": mat}


class Product(UnitOp):
    TYPE, LABEL, PREFIX, CATEGORY = "product", "Product", "PROD", "Streams"
    DESCRIPTION = "Material sink. Marks the end of a stream."
    PARAMS: list[Param] = []

    def outlet_ports(self):
        return []

    def calculate(self, inlets):
        mat = inlets["in"]
        self.results = [Result("Mass flow", mat.m, "m"), Result("Temperature", mat.T, "T"),
                        Result("Pressure", mat.P, "P"), Result("Phase", mat.state.phase)]
        return {}


# ===================================================================================
# Heater / cooler
# ===================================================================================

_HC_SPECS = ("Outlet temperature", "Duty", "Outlet vapor fraction", "Temperature change")


def _heat_params(sign: int) -> list[Param]:
    word = "added" if sign > 0 else "removed"
    return [
        Param("spec", "Specification", "choice", _HC_SPECS[0], choices=_HC_SPECS),
        Param("T_out", "Outlet temperature", "float", 373.15 if sign > 0 else 298.15, qty="T", minv=1e-6,
              visible=when("spec", _HC_SPECS[0])),
        Param("duty", f"Heat {word}", "float", 1e5, qty="power", visible=when("spec", _HC_SPECS[1])),
        Param("Q_out", "Outlet vapor fraction", "float", 1.0 if sign > 0 else 0.0, qty="frac", minv=0.0, maxv=1.0,
              visible=when("spec", _HC_SPECS[2]), tip="0 = saturated liquid, 1 = saturated vapor"),
        Param("dT", "Temperature rise" if sign > 0 else "Temperature drop", "float", 10.0, qty="dT",
              visible=when("spec", _HC_SPECS[3])),
        Param("dP", "Pressure drop", "float", 0.0, qty="dP", minv=0.0),
    ]


class _HeatTransfer(UnitOp):
    SIGN = 1
    CATEGORY = "Heat transfer"

    def calculate(self, inlets):
        inp = inlets["in"]
        fluid, m = inp.fluid, inp.m
        P = _outlet_P(self, inp.P)
        spec = self.values["spec"]
        if spec == _HC_SPECS[0]:
            out = Material.from_PT(fluid, m, P, self.num("T_out"))
        elif spec == _HC_SPECS[1]:
            q = self.SIGN * self.num("duty")
            if m <= 0:
                if abs(q) > 0:
                    raise UnitOpError("Cannot apply a duty to a stream with zero flow.")
                out = Material.from_PH(fluid, 0.0, P, inp.h)
            else:
                out = Material.from_PH(fluid, m, P, inp.h + q / m)
        elif spec == _HC_SPECS[2]:
            out = Material.from_PQ(fluid, m, P, self.num("Q_out"))
        else:
            out = Material.from_PT(fluid, m, P, inp.T + self.SIGN * self.num("dT"))
        heat = m * (out.h - inp.h)
        self.energy["heat"] = heat
        shown = self.SIGN * heat
        if shown < -1e-9 * max(1.0, abs(heat)):
            self.warnings.append(
                f"Computed duty is negative: this {self.LABEL.lower()} is "
                f"{'cooling' if self.SIGN > 0 else 'heating'} the stream.")
        self.results = [
            Result(f"Heat {'added' if self.SIGN > 0 else 'removed'}", shown, "power"),
            Result("Inlet temperature", inp.T, "T"), Result("Outlet temperature", out.T, "T"),
            Result("Outlet pressure", out.P, "P"), Result("Outlet vapor fraction", out.state.Q, "frac"),
            Result("Outlet phase", out.state.phase),
        ]
        self.add_path(inp, out, "isobaric")
        return {"out": out}


class Heater(_HeatTransfer):
    TYPE, LABEL, PREFIX, SIGN = "heater", "Heater", "H", 1
    DESCRIPTION = "Adds heat to a stream (boiler, evaporator, fired heater)."
    PARAMS = _heat_params(1)


class Cooler(_HeatTransfer):
    TYPE, LABEL, PREFIX, SIGN = "cooler", "Cooler", "C", -1
    DESCRIPTION = "Removes heat from a stream (condenser, aftercooler)."
    PARAMS = _heat_params(-1)


# ===================================================================================
# Pressure changers
# ===================================================================================

class _PressureChanger(UnitOp):
    COMPRESS = True
    CATEGORY = "Pressure change"
    SPECS = ("Outlet pressure", "Pressure ratio")

    @classmethod
    def make_params(cls, P_out: float, ratio: float, eta: float) -> list[Param]:
        rlabel = "Pressure ratio (out/in)" if cls.COMPRESS else "Pressure ratio (in/out)"
        return [
            Param("spec", "Specification", "choice", cls.SPECS[0], choices=cls.SPECS),
            Param("P_out", "Outlet pressure", "float", P_out, qty="P", minv=1e-6, visible=when("spec", cls.SPECS[0])),
            Param("ratio", rlabel, "float", ratio, qty="frac", minv=1.0, visible=when("spec", cls.SPECS[1])),
            Param("eta", "Isentropic efficiency", "float", eta, qty="frac", minv=1e-3, maxv=1.0),
        ]

    def calculate(self, inlets):
        inp = inlets["in"]
        fluid, m = inp.fluid, inp.m
        if self.values["spec"] == self.SPECS[0]:
            P2 = self.num("P_out")
        else:
            r = self.num("ratio")
            P2 = inp.P * r if self.COMPRESS else inp.P / r
        if self.COMPRESS and P2 < inp.P:
            raise UnitOpError("Outlet pressure is below the inlet pressure; use an expander or valve instead.")
        if not self.COMPRESS and P2 > inp.P:
            raise UnitOpError("Outlet pressure is above the inlet pressure; use a compressor or pump instead.")
        eta = self.num("eta")
        iso = flash(fluid, "PS", P2, inp.s)
        if self.COMPRESS:
            h2 = inp.h + (iso.h - inp.h) / eta
        else:
            h2 = inp.h - eta * (inp.h - iso.h)
        out = Material.from_PH(fluid, m, P2, h2)
        work = m * (out.h - inp.h)
        self.energy["work"] = work
        self.check(inp, out)
        self.results = [
            Result("Shaft power" if self.COMPRESS else "Power produced", work if self.COMPRESS else -work, "power"),
            Result("Outlet temperature", out.T, "T"),
            Result("Isentropic outlet temperature", iso.T, "T"),
            Result("Outlet pressure", P2, "P"),
            Result("Pressure ratio", P2 / inp.P if self.COMPRESS else inp.P / P2, "frac"),
            Result("Isentropic enthalpy change", iso.h - inp.h, "h"),
            Result("Outlet vapor fraction", out.state.Q, "frac"),
            Result("Outlet phase", out.state.phase),
        ]
        self.add_path(inp, out, "compression")
        return {"out": out}

    def check(self, inp: Material, out: Material) -> None:
        pass


class Compressor(_PressureChanger):
    TYPE, LABEL, PREFIX = "compressor", "Compressor", "K"
    DESCRIPTION = "Raises gas pressure. Adiabatic, with an isentropic efficiency."
    PARAMS = _PressureChanger.make_params(10e5, 3.0, 0.80)

    def check(self, inp, out):
        if inp.state.is_liquid_like() or inp.state.is_two_phase:
            self.warnings.append("Compressor inlet contains liquid. Use a pump for liquids or superheat the feed.")


class Pump(_PressureChanger):
    TYPE, LABEL, PREFIX = "pump", "Pump", "P"
    DESCRIPTION = "Raises liquid pressure. Adiabatic, with an isentropic efficiency."
    PARAMS = _PressureChanger.make_params(10e5, 10.0, 0.75)

    def check(self, inp, out):
        if not inp.state.is_liquid_like():
            self.warnings.append("Pump inlet is not fully liquid (cavitation risk).")


class Expander(_PressureChanger):
    TYPE, LABEL, PREFIX, COMPRESS = "expander", "Expander / turbine", "EXP", False
    DESCRIPTION = "Produces work by expanding a stream. Adiabatic, with an isentropic efficiency."
    PARAMS = _PressureChanger.make_params(1e5, 5.0, 0.85)

    def check(self, inp, out):
        if out.state.Q is not None and out.state.vapor_mass_fraction < 0.88 and not inp.state.is_liquid_like():
            self.warnings.append(
                f"Wet expansion: outlet vapor fraction {out.state.vapor_mass_fraction:.3f} (< 0.88 risks blade erosion).")


class Valve(UnitOp):
    TYPE, LABEL, PREFIX, CATEGORY = "valve", "Joule-Thomson valve", "V", "Pressure change"
    DESCRIPTION = "Isenthalpic throttling (Joule-Thomson expansion)."
    SPECS = ("Outlet pressure", "Pressure drop")
    PARAMS = [
        Param("spec", "Specification", "choice", SPECS[0], choices=SPECS),
        Param("P_out", "Outlet pressure", "float", 1e5, qty="P", minv=1e-6, visible=when("spec", SPECS[0])),
        Param("dP", "Pressure drop", "float", 1e5, qty="dP", minv=0.0, visible=when("spec", SPECS[1])),
    ]

    def calculate(self, inlets):
        inp = inlets["in"]
        P2 = self.num("P_out") if self.values["spec"] == self.SPECS[0] else inp.P - self.num("dP")
        if P2 <= 0:
            raise UnitOpError("Outlet pressure must be positive.")
        if P2 > inp.P:
            raise UnitOpError("A valve cannot raise the pressure (outlet pressure > inlet pressure).")
        out = Material.from_PH(inp.fluid, inp.m, P2, inp.h)
        dP = inp.P - P2
        self.results = [
            Result("Outlet temperature", out.T, "T"),
            Result("Temperature change", out.T - inp.T, "dT"),
            Result("Pressure drop", dP, "dP"),
            Result("Mean Joule-Thomson coefficient", (inp.T - out.T) / dP if dP > 0 else None, "JT"),
            Result("Outlet vapor fraction", out.state.Q, "frac"),
            Result("Outlet phase", out.state.phase),
        ]
        self.add_path(inp, out, "isenthalpic")
        return {"out": out}


# ===================================================================================
# Heat exchanger (two-stream, counter-current)
# ===================================================================================

class _EnthalpyTable:
    """h(T) curves of a mixture at the inlet and outlet pressure of one exchanger side.

    Built from PT flashes (fast and reliable for mixtures) on a temperature grid, with the
    exact bubble and dew points added as nodes so the kinks of the curve are captured.
    T(h, frac) interpolates linearly in h, and linearly in pressure along the exchanger.
    """
    N = 48

    def __init__(self, fluid: Fluid, P_in: float, P_out: float, T_lo: float, T_hi: float):
        self.curves = [self._curve(fluid, P_in, T_lo, T_hi)]
        self.curves.append(self.curves[0] if abs(P_out - P_in) < 1e-6 * P_in
                           else self._curve(fluid, P_out, T_lo, T_hi))

    def _curve(self, fluid, P, T_lo, T_hi):
        nodes = {}
        for k in range(self.N + 1):
            T = T_lo + (T_hi - T_lo) * k / self.N
            try:
                nodes[T] = flash(fluid, "PT", P, T).h
            except ThermoError:
                pass
        for Q in (0.0, 1.0):            # bubble and dew points
            try:
                st = flash(fluid, "PQ", P, Q)
                if T_lo < st.T < T_hi:
                    nodes[st.T] = st.h
            except ThermoError:
                pass
        pts = sorted(nodes.items())
        Ts, hs = [], []
        for T, h in pts:                # keep h strictly increasing
            if not hs or h > hs[-1]:
                Ts.append(T)
                hs.append(h)
        if len(hs) < 2:
            raise UnitOpError(f"Could not tabulate the enthalpy of {fluid.label()} at {P / 1e5:.4g} bar.")
        return hs, Ts

    @staticmethod
    def _interp(h, hs, Ts):
        if h <= hs[0]:
            i = 1
        elif h >= hs[-1]:
            i = len(hs) - 1
        else:
            i = bisect.bisect_right(hs, h)
        h0, h1, T0, T1 = hs[i - 1], hs[i], Ts[i - 1], Ts[i]
        return T0 + (T1 - T0) * (h - h0) / (h1 - h0)

    def T(self, h: float, frac: float) -> float:
        a = self._interp(h, *self.curves[0])
        if self.curves[1] is self.curves[0]:
            return a
        return a + (self._interp(h, *self.curves[1]) - a) * frac


class HeatExchanger(UnitOp):
    TYPE, LABEL, PREFIX, CATEGORY = "hx", "Heat exchanger", "HX", "Heat transfer"
    DESCRIPTION = ("Two-stream counter-current exchanger. The temperature profile is checked "
                   "segment by segment, so internal pinches and temperature crosses are detected.")
    SPECS = ("Minimum approach temperature", "Hot outlet temperature", "Cold outlet temperature", "Duty", "UA")
    PARAMS = [
        Param("spec", "Specification", "choice", SPECS[0], choices=SPECS),
        Param("dTmin", "Minimum approach ΔT", "float", 5.0, qty="dT", minv=1e-6, visible=when("spec", SPECS[0])),
        Param("T_hot_out", "Hot outlet temperature", "float", 313.15, qty="T", minv=1e-6, visible=when("spec", SPECS[1])),
        Param("T_cold_out", "Cold outlet temperature", "float", 333.15, qty="T", minv=1e-6, visible=when("spec", SPECS[2])),
        Param("duty", "Duty", "float", 1e5, qty="power", minv=0.0, visible=when("spec", SPECS[3])),
        Param("UA", "UA", "float", 1e4, qty="UA", minv=1e-9, visible=when("spec", SPECS[4])),
        Param("dP_hot", "Hot side pressure drop", "float", 0.0, qty="dP", minv=0.0),
        Param("dP_cold", "Cold side pressure drop", "float", 0.0, qty="dP", minv=0.0),
        Param("n_seg", "Profile segments", "int", 30, minv=2, maxv=400,
              tip="Number of segments used to check the temperature profile for pinches. For mixtures the "
                  "profile is interpolated from a tabulated h(T) curve that includes the exact bubble and dew "
                  "points, so more segments cost almost nothing."),
    ]

    def inlet_ports(self):
        return [("hot_in", "Hot inlet"), ("cold_in", "Cold inlet")]

    def outlet_ports(self):
        return [("hot_out", "Hot outlet"), ("cold_out", "Cold outlet")]

    # -- profile helpers ---------------------------------------------------------------
    def _setup(self, hot: Material, cold: Material):
        self._hot, self._cold = hot, cold
        self._Pho = hot.P - self.num("dP_hot")
        self._Pco = cold.P - self.num("dP_cold")
        if self._Pho <= 0 or self._Pco <= 0:
            raise UnitOpError("Pressure drop is larger than the inlet pressure.")
        self._n = max(int(self.num("n_seg")), 2)
        # Mixtures: tabulate h(T) once per side instead of flashing at every profile point
        T_lo = min(hot.T, cold.T) - 2.0
        T_hi = max(hot.T, cold.T) + 2.0
        self._tab_hot = None if hot.fluid.is_pure else _EnthalpyTable(hot.fluid, hot.P, self._Pho, T_lo, T_hi)
        self._tab_cold = None if cold.fluid.is_pure else _EnthalpyTable(cold.fluid, cold.P, self._Pco, T_lo, T_hi)

    def _profile(self, Q: float):
        hot, cold, n = self._hot, self._cold, self._n
        qs, Th, Tc = [], [], []
        for k in range(n + 1):
            q = Q * k / n                       # heat given up by the hot stream from its inlet
            frac = k / n
            ph = hot.P - (hot.P - self._Pho) * frac
            hh = hot.h - q / hot.m
            rem = Q - q                         # heat taken up by the cold stream from its inlet
            pc = cold.P - (cold.P - self._Pco) * (1 - frac)
            hc = cold.h + rem / cold.m
            Th.append(self._tab_hot.T(hh, frac) if self._tab_hot else flash(hot.fluid, "PH", ph, hh).T)
            Tc.append(self._tab_cold.T(hc, 1 - frac) if self._tab_cold else flash(cold.fluid, "PH", pc, hc).T)
            qs.append(q)
        return qs, Th, Tc

    def _min_dT(self, Q: float) -> tuple[float, int]:
        _, Th, Tc = self._profile(Q)
        dts = [a - b for a, b in zip(Th, Tc)]
        i = min(range(len(dts)), key=dts.__getitem__)
        return dts[i], i

    def _UA(self, Q: float) -> float:
        qs, Th, Tc = self._profile(Q)
        ua = 0.0
        for k in range(len(qs) - 1):
            a, b = Th[k] - Tc[k], Th[k + 1] - Tc[k + 1]
            if a <= 0 or b <= 0:
                return math.inf
            lm = a if abs(a - b) < 1e-9 * max(a, b) else (a - b) / math.log(a / b)
            ua += (qs[k + 1] - qs[k]) / lm
        return ua

    def _h_at(self, fluid: Fluid, P: float, T: float) -> float:
        try:
            return flash(fluid, "PT", P, T).h
        except ThermoError:
            return flash(fluid, "PT", P, T + 1e-4).h

    # -- main ----------------------------------------------------------------------------
    def calculate(self, inlets):
        hot, cold = inlets["hot_in"], inlets["cold_in"]
        self._setup(hot, cold)
        spec = self.values["spec"]

        if hot.m <= 0 or cold.m <= 0:
            self.warnings.append("One side has zero flow; no heat is exchanged.")
            ho = Material.from_PH(hot.fluid, hot.m, self._Pho, hot.h)
            co = Material.from_PH(cold.fluid, cold.m, self._Pco, cold.h)
            self.results = [Result("Duty", 0.0, "power")]
            return {"hot_out": ho, "cold_out": co}

        if hot.T <= cold.T:
            raise UnitOpError(
                f"Hot inlet ({hot.T - 273.15:.2f} °C) is not hotter than cold inlet ({cold.T - 273.15:.2f} °C). "
                "Check that the streams are connected to the correct ports.")

        Qmax = min(hot.m * (hot.h - self._h_at(hot.fluid, self._Pho, cold.T)),
                   cold.m * (self._h_at(cold.fluid, self._Pco, hot.T) - cold.h))
        Qmax = max(Qmax, 0.0)

        if spec == "Hot outlet temperature":
            Q = hot.m * (hot.h - self._h_at(hot.fluid, self._Pho, self.num("T_hot_out")))
        elif spec == "Cold outlet temperature":
            Q = cold.m * (self._h_at(cold.fluid, self._Pco, self.num("T_cold_out")) - cold.h)
        elif spec == "Duty":
            Q = self.num("duty")
        elif spec == "Minimum approach temperature":
            Q = self._solve_approach(self.num("dTmin"), Qmax)
        else:
            Q = self._solve_UA(self.num("UA"), Qmax)

        if Q < -1e-9:
            raise UnitOpError("The specification would make heat flow from the cold side to the hot side.")
        Q = max(Q, 0.0)
        if Q > Qmax * (1 + 1e-9) + 1e-9:
            raise UnitOpError(
                f"Temperature cross: the specified duty ({Q / 1e3:.4g} kW) exceeds the maximum possible "
                f"({Qmax / 1e3:.4g} kW, when one outlet reaches the other side's inlet temperature).")

        try:
            qs, Th, Tc = self._profile(Q)
        except ThermoError as exc:
            raise UnitOpError(f"Temperature profile could not be computed: {exc}") from exc
        dts = [a - b for a, b in zip(Th, Tc)]
        imin = min(range(len(dts)), key=dts.__getitem__)
        if dts[imin] < -1e-6:
            raise UnitOpError(
                f"Temperature cross: the minimum approach is {dts[imin]:.2f} K. "
                "Reduce the duty or relax the outlet temperature specification.")

        ho = Material.from_PH(hot.fluid, hot.m, self._Pho, hot.h - Q / hot.m)
        co = Material.from_PH(cold.fluid, cold.m, self._Pco, cold.h + Q / cold.m)
        UA = self._UA(Q) if Q > 0 else 0.0
        if imin == 0:
            where = "hot end"
        elif imin == len(dts) - 1:
            where = "cold end"
        else:
            where = f"internal ({100 * imin / (len(dts) - 1):.0f}% of duty from hot end)"
        self.profile = {"q": qs, "Th": Th, "Tc": Tc}
        self.results = [
            Result("Duty", Q, "power"),
            Result("Hot outlet temperature", ho.T, "T"),
            Result("Cold outlet temperature", co.T, "T"),
            Result("Minimum approach ΔT", dts[imin], "dT"),
            Result("Pinch location", where),
            Result("UA", UA, "UA"),
            Result("Effective LMTD", Q / UA if UA and math.isfinite(UA) and UA > 0 else None, "dT"),
            Result("Effectiveness (Q / Qmax)", Q / Qmax if Qmax > 0 else None, "frac"),
            Result("Hot outlet vapor fraction", ho.state.Q, "frac"),
            Result("Cold outlet vapor fraction", co.state.Q, "frac"),
        ]
        if dts[imin] < 0.5 and spec != "Minimum approach temperature":
            self.warnings.append(f"Very small approach temperature ({dts[imin]:.2f} K): the exchanger would be very large.")
        self.add_path(hot, ho, "isobaric")
        self.add_path(cold, co, "isobaric")
        return {"hot_out": ho, "cold_out": co}

    def _solve_approach(self, dTmin: float, Qmax: float) -> float:
        g0 = self._min_dT(0.0)[0] - dTmin
        if g0 <= 0:
            raise UnitOpError(
                f"The inlet temperature difference ({self._hot.T - self._cold.T:.2f} K) is smaller than the "
                f"minimum approach ({dTmin:.2f} K).")
        a, fa = 0.0, g0
        b = Qmax
        fb = self._min_dT(b)[0] - dTmin
        if fb >= 0:
            return b
        for _ in range(80):
            c = b - fb * (b - a) / (fb - fa)
            fc = self._min_dT(c)[0] - dTmin
            if abs(fc) < 1e-5 or abs(b - a) < 1e-12 * max(Qmax, 1.0):
                return c
            if fc * fb < 0:
                a, fa = b, fb
            else:
                fa *= 0.5
            b, fb = c, fc
        return b

    def _solve_UA(self, UA: float, Qmax: float) -> float:
        lo, hi = 0.0, Qmax
        if self._UA(hi) <= UA:
            self.warnings.append("UA is larger than needed to reach the thermodynamic limit; duty is at its maximum.")
            return hi
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if self._UA(mid) < UA:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-7 * max(Qmax, 1.0):
                break
        return 0.5 * (lo + hi)


# ===================================================================================
# Flash / phase separator
# ===================================================================================

class Flash(UnitOp):
    TYPE, LABEL, PREFIX, CATEGORY = "flash", "Flash / separator", "FL", "Separation"
    DESCRIPTION = "Splits a stream into equilibrium vapor (top) and liquid (bottom)."
    SPECS = ("Adiabatic", "Specified temperature")
    PARAMS = [
        Param("spec", "Operation", "choice", SPECS[0], choices=SPECS,
              tip="Adiabatic: flash at the inlet enthalpy. Specified temperature: heat is added/removed as needed."),
        Param("T", "Temperature", "float", 298.15, qty="T", minv=1e-6, visible=when("spec", SPECS[1])),
        Param("dP", "Pressure drop", "float", 0.0, qty="dP", minv=0.0),
    ]

    def outlet_ports(self):
        return [("vapor", "Vapor"), ("liquid", "Liquid")]

    def calculate(self, inlets):
        inp = inlets["in"]
        fluid, m = inp.fluid, inp.m
        P = _outlet_P(self, inp.P)
        st = flash(fluid, "PH", P, inp.h) if self.values["spec"] == self.SPECS[0] else flash(fluid, "PT", P, self.num("T"))
        mix = Material(m, st)

        if st.is_two_phase:
            if fluid.is_pure:
                mv = m * st.Q
                vap = Material.from_PQ(fluid, mv, P, 1.0)
                liq = Material.from_PQ(fluid, m - mv, P, 0.0)
            else:
                if st.x_liq is None or st.y_vap is None:
                    raise UnitOpError("CoolProp did not return phase compositions for this mixture.")
                fv = Fluid.from_moles(dict(zip(fluid.components, st.y_vap)))
                fl = Fluid.from_moles(dict(zip(fluid.components, st.x_liq)))
                n = m / fluid.molar_mass
                mv = st.Q * n * fv.molar_mass
                ml = m - mv
                vap = Material(mv, self._phase_state(fv, P, st.T, 1.0))
                liq = Material(ml, self._phase_state(fl, P, st.T, 0.0))
        else:
            vapor_like = (st.Q == 1.0) or (st.Q is None and "liquid" not in st.phase)
            if vapor_like:
                vap, liq = mix, Material(0.0, st)
            else:
                vap, liq = Material(0.0, st), mix
            self.warnings.append(f"Inlet is single phase ({st.phase}) at flash conditions; "
                                 f"all flow leaves through the {'vapor' if vapor_like else 'liquid'} outlet.")

        duty = vap.H + liq.H - inp.H
        self.energy["heat"] = duty
        self.results = [
            Result("Temperature", st.T, "T"), Result("Pressure", P, "P"),
            Result("Vapor fraction (molar)", st.vapor_fraction, "frac"),
            Result("Vapor fraction (mass)", vap.m / m if m > 0 else None, "frac"),
            Result("Vapor flow", vap.m, "m"), Result("Liquid flow", liq.m, "m"),
            Result("Heat duty", duty, "power"),
        ]
        if not fluid.is_pure and st.is_two_phase:
            for c, x, y in zip(fluid.components, st.x_liq, st.y_vap):
                self.results.append(Result(f"{c}: y (vapor) / x (liquid)", f"{y:.4f} / {x:.4f}"))
        self.add_path(inp, mix, "isobaric")
        return {"vapor": vap, "liquid": liq}

    @staticmethod
    def _phase_state(fluid: Fluid, P: float, T: float, Q: float):
        """State of an equilibrium phase, at its own bubble/dew point at P."""
        try:
            return flash(fluid, "PQ", P, Q)
        except ThermoError:
            # fall back to a PT flash marginally inside the single-phase region
            return flash(fluid, "PT", P, T + (1e-3 if Q == 1.0 else -1e-3))


# ===================================================================================
# Mixer and splitter
# ===================================================================================

class Mixer(UnitOp):
    TYPE, LABEL, PREFIX, CATEGORY = "mixer", "Mixer", "MIX", "Mixing & splitting"
    DESCRIPTION = "Adiabatically combines streams. Different fluids are mixed by composition."
    SPECS = ("Lowest inlet pressure", "Specified pressure")
    PARAMS = [
        Param("n_in", "Number of inlets", "int", 2, minv=2, maxv=6, rebuild_ports=True),
        Param("spec", "Outlet pressure", "choice", SPECS[0], choices=SPECS),
        Param("P_out", "Pressure", "float", 101325.0, qty="P", minv=1e-6, visible=when("spec", SPECS[1])),
    ]

    def inlet_ports(self):
        return [(f"in{i + 1}", f"Inlet {i + 1}") for i in range(int(self.values["n_in"]))]

    def optional_inlets(self):
        return {p for p, _ in self.inlet_ports()}

    def calculate(self, inlets):
        mats = [inlets[p] for p, _ in self.inlet_ports() if p in inlets]
        if not mats:
            raise UnitOpError("No inlet streams are connected.")
        flowing = [x for x in mats if x.m > 0]
        P = min(x.P for x in (flowing or mats)) if self.values["spec"] == self.SPECS[0] else self.num("P_out")
        if self.values["spec"] == self.SPECS[1] and any(x.P < P * (1 - 1e-9) for x in flowing):
            self.warnings.append("Outlet pressure is above the lowest inlet pressure.")
        if flowing and max(x.P for x in flowing) > 1.01 * min(x.P for x in flowing):
            self.warnings.append("Inlet pressures differ by more than 1 %; the higher-pressure streams are throttled.")
        M = sum(x.m for x in mats)
        if M <= 0:
            out = Material.from_PH(mats[0].fluid, 0.0, P, mats[0].h)
        else:
            fluids = {x.fluid for x in flowing}
            if len(fluids) == 1:
                fluid = flowing[0].fluid
                H = sum(x.H for x in mats)
            else:
                # Pseudo-pure inlets (e.g. Air) are converted to their real components, and their
                # enthalpy is re-evaluated in that model so every inlet shares one reference state.
                H = 0.0
                parts = []
                for x in flowing:
                    f = x.fluid.expanded()
                    if f != x.fluid:
                        try:
                            h_i = flash(f, "PT", x.P, x.T).h
                        except ThermoError as exc:
                            raise UnitOpError(
                                f"Could not convert the {x.fluid.label()} inlet to its components for mixing "
                                f"(two-phase {x.fluid.label()} cannot be mixed): {exc}")
                        H += x.m * h_i
                    else:
                        H += x.H
                    parts.append((f, x.m))
                fluid = Fluid.mix(parts)
            h = H / M
            out = Material.from_PH(fluid, M, P, h)
        self.results = [Result("Outlet flow", out.m, "m"), Result("Outlet temperature", out.T, "T"),
                        Result("Outlet pressure", out.P, "P"), Result("Outlet vapor fraction", out.state.Q, "frac"),
                        Result("Outlet fluid", out.fluid.label())]
        return {"out": out}


class Splitter(UnitOp):
    TYPE, LABEL, PREFIX, CATEGORY = "splitter", "Splitter", "SPL", "Mixing & splitting"
    DESCRIPTION = "Divides a stream into branches with identical conditions."
    SPECS = ("Split fractions", "Mass flows")
    PARAMS = [Param("n_out", "Number of outlets", "int", 2, minv=2, maxv=6, rebuild_ports=True),
              Param("spec", "Specification", "choice", SPECS[0], choices=SPECS)] + [
        Param(f"frac{i}", f"Fraction to outlet {i}", "float", 0.5, qty="frac", minv=0.0, maxv=1.0,
              visible=(lambda v, i=i: v.get("spec") == "Split fractions" and int(v.get("n_out", 2)) > i))
        for i in range(1, 6)] + [
        Param(f"flow{i}", f"Flow to outlet {i}", "float", 0.5, qty="m", minv=0.0,
              visible=(lambda v, i=i: v.get("spec") == "Mass flows" and int(v.get("n_out", 2)) > i))
        for i in range(1, 6)]

    def outlet_ports(self):
        return [(f"out{i + 1}", f"Outlet {i + 1}") for i in range(int(self.values["n_out"]))]

    def calculate(self, inlets):
        inp = inlets["in"]
        n = int(self.values["n_out"])
        if self.values["spec"] == self.SPECS[0]:
            fr = [self.num(f"frac{i}") for i in range(1, n)]
            rest = 1.0 - sum(fr)
            if rest < -1e-9:
                raise UnitOpError("Split fractions add up to more than 1.")
            flows = [f * inp.m for f in fr] + [max(rest, 0.0) * inp.m]
        else:
            fl = [self.num(f"flow{i}") for i in range(1, n)]
            rest = inp.m - sum(fl)
            if rest < -1e-12:
                raise UnitOpError("Specified branch flows exceed the inlet flow.")
            flows = fl + [max(rest, 0.0)]
        outs = {f"out{i + 1}": inp.with_flow(f) for i, f in enumerate(flows)}
        self.results = [Result(f"Outlet {i + 1} flow", f, "m") for i, f in enumerate(flows)]
        return outs


# ===================================================================================
# Conversion reactor (combustion or user reactions)
# ===================================================================================

_INERT_FOR_COMBUSTION = {"CarbonDioxide", "Water", "SulfurDioxide", "Nitrogen", "Oxygen", "Argon", "Helium",
                         "Neon", "Krypton", "Xenon", "Air"}


def parse_reactions(text: str) -> list[dict]:
    """Parse lines like 'CH4 + 2 O2 -> CO2 + 2 H2O ; X=0.95 ; key=CH4'.

    Species may be formulas (CH4, O2, H2O, C3H8 ...) or CoolProp names (Methane). Conversion X
    (default 1) applies to the key reactant (default: the first reactant). Lines starting with
    # are comments.
    """
    import re
    out = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(";")]
        eq = parts[0].replace("=>", "->").replace("→", "->")
        if "->" not in eq:
            raise UnitOpError(f"Reaction line {lineno}: write it as 'A + 2 B -> C', e.g. CH4 + 2 O2 -> CO2 + 2 H2O.")
        lhs, rhs = eq.split("->", 1)
        nu: dict[str, float] = {}
        first_reactant = None
        for side, sign in ((lhs, -1.0), (rhs, 1.0)):
            for term in side.split("+"):
                term = term.strip()
                if not term:
                    continue
                m = re.match(r"^(\d+(?:\.\d*)?|\.\d+)?\s*\*?\s*(.+)$", term)
                coef = float(m.group(1)) if m.group(1) else 1.0
                try:
                    name = species_name(m.group(2))
                except ThermoError:
                    raise UnitOpError(f"Reaction line {lineno}: unknown species '{m.group(2).strip()}'.")
                nu[name] = nu.get(name, 0.0) + sign * coef
                if sign < 0 and first_reactant is None:
                    first_reactant = name
        X, key = 1.0, first_reactant
        for opt in parts[1:]:
            if not opt:
                continue
            k, _, v = opt.partition("=")
            k = k.strip().lower()
            if k in ("x", "conversion", "conv"):
                try:
                    X = float(v)
                except ValueError:
                    raise UnitOpError(f"Reaction line {lineno}: conversion '{v.strip()}' is not a number.")
            elif k == "key":
                try:
                    key = species_name(v)
                except ThermoError:
                    raise UnitOpError(f"Reaction line {lineno}: unknown key species '{v.strip()}'.")
            else:
                raise UnitOpError(f"Reaction line {lineno}: unknown option '{opt}' (use X=... and key=...).")
        if key not in nu or nu[key] >= 0:
            raise UnitOpError(f"Reaction line {lineno}: the key species must be a reactant.")
        if not 0.0 <= X <= 1.0:
            raise UnitOpError(f"Reaction line {lineno}: conversion X must be between 0 and 1.")
        out.append({"nu": nu, "X": X, "key": key, "text": eq.strip(), "line": lineno})
    return out


def _check_balance(r: dict) -> None:
    tot: dict[str, float] = {}
    for sp, n in r["nu"].items():
        el = elements(sp)
        if el is None:
            raise UnitOpError(f"No chemical formula is available for {sp}, so it cannot react.")
        for e, k in el.items():
            tot[e] = tot.get(e, 0.0) + n * k
    bad = {e: v for e, v in tot.items() if abs(v) > 1e-9}
    if bad:
        raise UnitOpError(f"Reaction '{r['text']}' is not balanced: "
                          + ", ".join(f"{e} {v:+g}" for e, v in bad.items()) + " atoms.")


def _heat_of_reaction(nu: dict) -> float:
    """Standard heat of reaction at 298.15 K [J per mol of extent] (ideal gas, water as vapor)."""
    missing = [sp for sp in nu if sp not in FORMATION_ENTHALPY]
    if missing:
        raise UnitOpError(f"No enthalpy of formation is tabulated for {', '.join(missing)}.")
    return sum(n * FORMATION_ENTHALPY[sp] for sp, n in nu.items())


class Reactor(UnitOp):
    TYPE, LABEL, PREFIX, CATEGORY = "reactor", "Reactor / combustor", "R", "Reaction"
    DESCRIPTION = ("Conversion reactor. Combustion mode burns every fuel in the feed completely to CO2, H2O, "
                   "SO2 and N2; custom mode applies your reactions in order. The heat of reaction comes from "
                   "standard enthalpies of formation.")
    MODES = ("Combustion (complete)", "Custom reactions")
    ENERGY = ("Adiabatic", "Outlet temperature", "Heat duty")
    PARAMS = [
        Param("n_in", "Number of inlets", "int", 2, minv=1, maxv=4, rebuild_ports=True,
              tip="Inlets are mixed adiabatically at the lowest inlet pressure before reacting "
                  "(e.g. inlet 1 = air, inlet 2 = fuel)."),
        Param("mode", "Reactions", "choice", MODES[0], choices=MODES),
        Param("conversion", "Fuel conversion", "float", 1.0, qty="frac", minv=0.0, maxv=1.0,
              visible=when("mode", MODES[0]), tip="Fraction of each fuel that burns (1 = complete burnout)."),
        Param("reactions", "Reactions", "text", "CH4 + 2 O2 -> CO2 + 2 H2O ; X=1 ; key=CH4",
              visible=when("mode", MODES[1]),
              tip="One reaction per line:  CH4 + 2 O2 -> CO2 + 2 H2O ; X=0.98 ; key=CH4\n"
                  "X = conversion of the key reactant (default 1, key defaults to the first reactant). "
                  "Reactions are applied in order. Species can be formulas or CoolProp names."),
        Param("energy", "Energy balance", "choice", ENERGY[0], choices=ENERGY),
        Param("T_out", "Outlet temperature", "float", 1473.15, qty="T", minv=1e-6, visible=when("energy", ENERGY[1])),
        Param("duty", "Heat added", "float", 0.0, qty="power", visible=when("energy", ENERGY[2]),
              tip="Heat added to the reactor (negative = heat loss)."),
        Param("dP", "Pressure drop", "float", 0.0, qty="dP", minv=0.0),
    ]

    def inlet_ports(self):
        return [(f"in{i + 1}", f"Inlet {i + 1}") for i in range(int(self.values["n_in"]))]

    def optional_inlets(self):
        return {p for p, _ in self.inlet_ports()}

    # -- reactions ------------------------------------------------------------------------
    def _combustion_reactions(self, moles: dict) -> list[dict]:
        rx = []
        for sp, n in moles.items():
            if n <= 0 or sp in _INERT_FOR_COMBUSTION:
                continue
            el = elements(sp)
            if not el or not set(el) <= {"C", "H", "O", "N", "S"} or not (set(el) & {"C", "H", "S"}):
                continue                     # not a fuel (e.g. refrigerants, noble gases)
            c, h, o, nn, s_ = (el.get(k, 0.0) for k in ("C", "H", "O", "N", "S"))
            o2 = c + h / 4 + s_ - o / 2
            if o2 <= 0:
                continue
            nu = {sp: -1.0, "Oxygen": -o2}
            for prod, k in (("CarbonDioxide", c), ("Water", h / 2), ("SulfurDioxide", s_), ("Nitrogen", nn / 2)):
                if k:
                    nu[prod] = nu.get(prod, 0.0) + k
            rx.append({"nu": nu, "X": self.num("conversion"), "key": sp, "text": f"{sp} combustion"})
        return rx

    def calculate(self, inlets):
        mats = [inlets[p] for p, _ in self.inlet_ports() if p in inlets]
        flowing = [m for m in mats if m.m > 0]
        if not flowing:
            raise UnitOpError("No inlet with flow is connected.")
        P = min(m.P for m in flowing) - self.num("dP")
        if P <= 0:
            raise UnitOpError("Pressure drop is larger than the inlet pressure.")
        H_in = sum(formation_enthalpy_flow(m) for m in flowing)
        m_tot = sum(m.m for m in flowing)
        moles: dict[str, float] = {}
        for m in flowing:
            f = m.fluid.expanded()
            n = m.m / f.molar_mass
            for c, x in zip(f.components, f.x):
                moles[c] = moles.get(c, 0.0) + n * x
        feed_moles = dict(moles)

        combustion = self.values["mode"] == self.MODES[0]
        rxs = self._combustion_reactions(moles) if combustion else parse_reactions(self.values["reactions"])
        if not rxs:
            self.warnings.append("No reactions apply: the feed contains no fuel." if combustion
                                 else "No reactions are defined.")
        for r in rxs:
            _check_balance(r)
            r["dHr"] = _heat_of_reaction(r["nu"])

        extents = []
        if combustion and rxs:
            want = [r["X"] * moles.get(r["key"], 0.0) for r in rxs]
            o2_need = sum(w * -r["nu"]["Oxygen"] for w, r in zip(want, rxs))
            o2_have = moles.get("Oxygen", 0.0)
            scale = 1.0
            if o2_need > o2_have:
                scale = o2_have / o2_need if o2_need > 0 else 0.0
                self.warnings.append(f"Fuel-rich feed: not enough O2, so only {100 * scale:.1f} % of the requested "
                                     "fuel burns (complete combustion assumed; CO is not formed).")
            for w, r in zip(want, rxs):
                xi = w * scale
                for sp, nu in r["nu"].items():
                    moles[sp] = moles.get(sp, 0.0) + nu * xi
                extents.append(xi)
        else:
            for r in rxs:
                xi = r["X"] * moles.get(r["key"], 0.0) / -r["nu"][r["key"]]
                limit = min(moles.get(sp, 0.0) / -nu for sp, nu in r["nu"].items() if nu < 0)
                if xi > limit * (1 + 1e-12):
                    lim_sp = min((sp for sp, nu in r["nu"].items() if nu < 0),
                                 key=lambda sp: moles.get(sp, 0.0) / -r["nu"][sp])
                    self.warnings.append(f"Reaction '{r['text']}' is limited by {lim_sp}; "
                                         "the requested conversion of the key species is not reached.")
                    xi = limit
                for sp, nu in r["nu"].items():
                    moles[sp] = moles.get(sp, 0.0) + nu * xi
                extents.append(xi)

        moles = {c: max(n, 0.0) for c, n in moles.items() if n > 1e-15}
        if not moles:
            raise UnitOpError("All material was consumed.")
        f_out = Fluid.from_moles(moles)
        n_out = sum(moles.values())
        m_out = n_out * f_out.molar_mass
        # CoolProp's molar masses are consistent to ~1e-6, so element-balanced reactions change the
        # computed mass very slightly; the outlet keeps the exact inlet mass flow.
        if abs(m_out - m_tot) > 1e-5 * m_tot:
            raise UnitOpError(f"Mass is not conserved by the reactions ({m_out:.6g} vs {m_tot:.6g} kg/s).")
        offsets = sum(n * formation_offset(c) for c, n in zip(f_out.components, (n_out * x for x in f_out.x)))

        energy = self.values["energy"]
        if energy == self.ENERGY[1]:
            out = Material.from_PT(f_out, m_tot, P, self.num("T_out"))
            Q = out.H + offsets - H_in
        else:
            Q = 0.0 if energy == self.ENERGY[0] else self.num("duty")
            out = Material.from_PH(f_out, m_tot, P, (H_in + Q - offsets) / m_tot)
        self.energy["heat"] = Q

        released = sum(-r["dHr"] * xi for r, xi in zip(rxs, extents))
        self.reaction_heat = released
        self.results = [
            Result("Outlet temperature", out.T, "T"),
            Result("Outlet pressure", out.P, "P"),
            Result("Heat released (LHV, 298 K)", released, "power"),
            Result("Heat added", Q, "power"),
        ]
        if combustion and rxs:
            fuel_mass = sum(xi * component_molar_mass(r["key"]) for r, xi in zip(rxs, extents))
            o2_stoich = sum(feed_moles.get(r["key"], 0.0) * -r["nu"]["Oxygen"] for r in rxs)
            o2_feed = feed_moles.get("Oxygen", 0.0)
            if fuel_mass > 0:
                self.results.append(Result("Fuel LHV", released / fuel_mass, "h"))
            self.results.append(Result("Fuel burned", fuel_mass, "m"))
            if o2_feed > 0:
                phi = o2_stoich / o2_feed
                self.results.append(Result("Equivalence ratio φ", phi, "frac"))
                if phi > 0:
                    self.results.append(Result("Excess air [%]", (1 / phi - 1) * 100, None))
        for i, (r, xi) in enumerate(zip(rxs, extents), 1):
            self.results.append(Result(f"R{i}: {r['text']}", f"ΔHr {r['dHr'] / 1e3:.2f} kJ/mol"))
            self.results.append(Result(f"R{i} extent", f"{xi:.5g} mol/s"))
        for c, x in zip(f_out.components, f_out.x):
            self.results.append(Result(f"x {c} (outlet)", x, "frac"))
        if out.T > 2000.0:
            self.warnings.append(f"Outlet temperature {out.T:.0f} K is above 2000 K, the upper limit of CoolProp's "
                                 "property models; dissociation (CO, OH, NO) is also not modelled.")
        elif out.T > 1900.0:
            self.warnings.append("Above about 1900 K dissociation (not modelled here) lowers the real flame "
                                 "temperature.")
        return {"out": out}


BLOCK_TYPES: dict[str, type[UnitOp]] = {
    cls.TYPE: cls for cls in (Feed, Product, Heater, Cooler, HeatExchanger, Compressor, Pump, Expander,
                              Valve, Flash, Mixer, Splitter, Reactor)
}
