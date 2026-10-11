"""Thermodynamic property layer built on CoolProp.

All values are SI: P [Pa], T [K], h [J/kg], s [J/kg/K], rho [kg/m3],
molar mass [kg/mol]. Vapor fraction ``Q`` is CoolProp's quality, which is
molar for mixtures and equal to the mass fraction for pure fluids.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Sequence

import CoolProp
import CoolProp.CoolProp as CP
from CoolProp.CoolProp import AbstractState


class ThermoError(Exception):
    """Raised when a fluid cannot be parsed or a flash calculation fails."""


# ---------------------------------------------------------------------------
# Fluid definition
# ---------------------------------------------------------------------------

def available_fluids() -> list[str]:
    """Names of all pure / pseudo-pure fluids in the CoolProp HEOS backend."""
    return sorted(CP.get_global_param_string("FluidsList").split(","), key=str.lower)


@lru_cache(maxsize=None)
def canonical_name(name: str) -> str:
    """Map an alias such as 'N2' or 'water' to CoolProp's canonical name."""
    name = name.strip()
    try:
        return CP.get_fluid_param_string(name, "name")
    except Exception as exc:  # CoolProp raises ValueError
        raise ThermoError(f"Unknown fluid '{name}'. Check the spelling or pick one from the list.") from exc


# Pseudo-pure fluids that can be replaced by their real components when they are mixed with
# something else. CoolProp's "Air" is a single fictitious substance with no binary
# interaction data, so a mixture such as Air + CO2 must be written as N2 + O2 + Ar + CO2.
# (Composition of the Lemmon et al. air model, mole fractions.)
PSEUDO_PURE_COMPONENTS = {
    "Air": {"Nitrogen": 0.7812, "Oxygen": 0.2096, "Argon": 0.0092},
}


@lru_cache(maxsize=None)
def is_pseudo_pure(name: str) -> bool:
    try:
        return CP.get_fluid_param_string(name, "pure") == "false"
    except Exception:
        return False


@lru_cache(maxsize=None)
def component_molar_mass(name: str) -> float:
    return CP.PropsSI("molar_mass", name)


@dataclass(frozen=True)
class Fluid:
    """A pure fluid or a mixture with fixed mole fractions."""

    components: tuple[str, ...]
    x: tuple[float, ...] = (1.0,)

    # -- construction -------------------------------------------------------
    @staticmethod
    def parse(spec: str) -> "Fluid":
        """Parse 'Water', 'N2' or 'Methane[0.9]&Ethane[0.1]' (mole fractions).

        An optional 'HEOS::' prefix is accepted. Fractions are normalized.
        """
        if spec is None or not str(spec).strip():
            raise ThermoError("No fluid specified.")
        spec = str(spec).strip()
        if "::" in spec:
            backend, spec = spec.split("::", 1)
            if backend.upper() != "HEOS":
                raise ThermoError("Only the HEOS backend is supported.")
        parts = [p for p in spec.split("&") if p.strip()]
        comps: list[str] = []
        fracs: list[float] = []
        for p in parts:
            p = p.strip()
            if "[" in p:
                name, rest = p.split("[", 1)
                try:
                    frac = float(rest.rstrip("]"))
                except ValueError as exc:
                    raise ThermoError(f"Bad mole fraction in '{p}'.") from exc
            else:
                name, frac = p, (1.0 if len(parts) == 1 else float("nan"))
            if frac != frac:
                raise ThermoError("Give a mole fraction for every component, e.g. Methane[0.9]&Ethane[0.1].")
            if frac < 0:
                raise ThermoError("Mole fractions must be positive.")
            comps.append(canonical_name(name))
            fracs.append(frac)
        return Fluid.from_moles(dict(zip(comps, fracs)))

    @staticmethod
    def from_moles(moles: dict[str, float]) -> "Fluid":
        merged: dict[str, float] = {}
        for k, v in moles.items():
            merged[k] = merged.get(k, 0.0) + v
        merged = {k: v for k, v in merged.items() if v > 1e-12}
        if len(merged) > 1:
            merged = _expand_pseudo_pure(merged)
        total = sum(merged.values())
        if total <= 0:
            raise ThermoError("Mixture has no components with a positive fraction.")
        comps = tuple(sorted(merged))
        return Fluid(comps, tuple(merged[c] / total for c in comps))

    @staticmethod
    def mix(parts: Sequence[tuple["Fluid", float]]) -> "Fluid":
        """Combine fluids given as (fluid, mass flow) pairs."""
        moles: dict[str, float] = {}
        for fluid, m in parts:
            if m <= 0:
                continue
            n = m / fluid.molar_mass
            for c, xi in zip(fluid.components, fluid.x):
                moles[c] = moles.get(c, 0.0) + n * xi
        if not moles:
            return parts[0][0]
        return Fluid.from_moles(moles)

    def expanded(self) -> "Fluid":
        """The same fluid with pseudo-pure components (e.g. Air) written as real components.

        Used when this fluid is about to be mixed with another one.
        """
        if not any(c in PSEUDO_PURE_COMPONENTS for c in self.components):
            return self
        moles = _expand_pseudo_pure(dict(zip(self.components, self.x)))
        total = sum(moles.values())
        comps = tuple(sorted(moles))
        return Fluid(comps, tuple(moles[c] / total for c in comps))

    # -- properties -----------------------------------------------------------
    @property
    def is_pure(self) -> bool:
        return len(self.components) == 1

    @property
    def molar_mass(self) -> float:
        return sum(xi * component_molar_mass(c) for c, xi in zip(self.components, self.x))

    @property
    def mass_fractions(self) -> tuple[float, ...]:
        mm = self.molar_mass
        return tuple(xi * component_molar_mass(c) / mm for c, xi in zip(self.components, self.x))

    def spec(self) -> str:
        if self.is_pure:
            return self.components[0]
        return "&".join(f"{c}[{xi:.6g}]" for c, xi in zip(self.components, self.x))

    def label(self) -> str:
        if self.is_pure:
            return self.components[0]
        return " / ".join(f"{c} {xi:.3f}" for c, xi in zip(self.components, self.x))

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.spec()


def _expand_pseudo_pure(moles: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c, n in moles.items():
        for sub, frac in PSEUDO_PURE_COMPONENTS.get(c, {c: 1.0}).items():
            out[sub] = out.get(sub, 0.0) + n * frac
    return out


# ---------------------------------------------------------------------------
# Property package: which equation of state, and binary interaction parameters
# ---------------------------------------------------------------------------

METHODS = {
    "HEOS": "CoolProp reference (Helmholtz / GERG-type mixtures)",
    "PR": "Peng-Robinson (cubic)",
    "SRK": "Soave-Redlich-Kwong (cubic)",
}


def pair_key(a: str, b: str) -> str:
    a, b = sorted((a, b))
    return f"{a}|{b}"


class PropertyPackage:
    def __init__(self, method: str = "HEOS", kij_user: Optional[dict] = None):
        if method not in METHODS:
            raise ThermoError(f"Unknown property method '{method}'.")
        self.method = method
        self.kij_user = {pair_key(*k.split("|")): float(v) for k, v in (kij_user or {}).items()}
        self.token = 0

    @property
    def cubic(self) -> bool:
        return self.method in ("PR", "SRK")


_PKG = PropertyPackage()
_PKG_LOCK = threading.RLock()
_TOKEN = [0]


def set_property_package(method: str = "HEOS", kij_user: Optional[dict] = None) -> PropertyPackage:
    """Select the equation of state (and user k_ij values, keyed 'A|B') for all later flashes."""
    global _PKG
    with _PKG_LOCK:
        new = PropertyPackage(method, kij_user)
        if new.method == _PKG.method and new.kij_user == _PKG.kij_user:
            return _PKG
        _TOKEN[0] += 1
        new.token = _TOKEN[0]
        _PKG = new
        return _PKG


def current_package() -> PropertyPackage:
    return _PKG


def for_package(fluid: Fluid) -> Fluid:
    """Adapt a fluid to the active package (cubic EOS have no pseudo-pure Air, so expand it)."""
    return fluid.expanded() if _PKG.cubic else fluid


_FIT_CACHE: dict[tuple[str, str], Optional[float]] = {}
_FIT_LOCK = threading.Lock()


def has_reference_mixture_data(a: str, b: str) -> bool:
    try:
        AbstractState("HEOS", f"{a}&{b}")
        return True
    except Exception:
        return False


def fit_kij(method: str, a: str, b: str) -> Optional[float]:
    """Fit a cubic-EOS k_ij to bubble pressures from CoolProp's reference mixture model.

    Returns None when CoolProp has no reference data for the pair. The fit matches
    bubble pressures at x = 0.25 / 0.5 / 0.75 at about 0.75 of the lower critical
    temperature, and averages the three k_ij values.
    """
    key = (method, pair_key(a, b))
    with _FIT_LOCK:
        if key in _FIT_CACHE:
            return _FIT_CACHE[key]
    result = None
    if has_reference_mixture_data(a, b):
        ref = AbstractState("HEOS", f"{a}&{b}")
        Tc = min(AbstractState("HEOS", a).T_critical(), AbstractState("HEOS", b).T_critical())
        fits = []
        for frac in (0.75, 0.7, 0.8, 0.85):
            T = frac * Tc
            for x in (0.25, 0.5, 0.75):
                try:
                    ref.set_mole_fractions([x, 1 - x])
                    ref.update(CoolProp.QT_INPUTS, 0.0, T)
                    p_ref = ref.p()
                except Exception:
                    continue
                k = _fit_one(method, a, b, x, T, p_ref)
                if k is not None:
                    fits.append(k)
            if len(fits) >= 2:
                break
        if fits:
            result = sum(fits) / len(fits)
    with _FIT_LOCK:
        _FIT_CACHE[key] = result
    return result


def _fit_one(method, a, b, x, T, p_ref) -> Optional[float]:
    cub = AbstractState(method, f"{a}&{b}")
    cub.set_mole_fractions([x, 1 - x])

    def g(k):
        cub.set_binary_interaction_double(0, 1, "kij", k)
        cub.update(CoolProp.QT_INPUTS, 0.0, T)
        return cub.p() / p_ref - 1.0

    try:
        k0, k1 = 0.0, 0.05
        f0, f1 = g(k0), g(k1)
        for _ in range(40):
            if abs(f1) < 1e-6:
                return k1
            if f1 == f0:
                return None
            k0, f0, k1 = k1, f1, k1 - f1 * (k1 - k0) / (f1 - f0)
            if not -0.5 < k1 < 0.8:
                return None
            f1 = g(k1)
        return k1 if abs(f1) < 1e-4 else None
    except Exception:
        return None


def kij_for(a: str, b: str, pkg: Optional[PropertyPackage] = None) -> tuple[float, str]:
    """(k_ij, source) for a pair in the active cubic package. source: user | fitted | estimated."""
    pkg = pkg or _PKG
    key = pair_key(a, b)
    if key in pkg.kij_user:
        return pkg.kij_user[key], "user"
    k = fit_kij(pkg.method, a, b)
    if k is not None:
        return k, "fitted"
    return 0.0, "estimated"


# ---------------------------------------------------------------------------
# AbstractState pool (one per thread; AbstractState objects are not thread safe)
# ---------------------------------------------------------------------------

_local = threading.local()


def _state_for(fluid: Fluid) -> AbstractState:
    pkg = _PKG
    pool = getattr(_local, "pool", None)
    if pool is None or getattr(_local, "token", None) != pkg.token:
        pool = _local.pool = {}
        _local.token = pkg.token
    key = fluid.components
    st = pool.get(key)
    if st is None:
        st = _build_state(fluid, pkg)
        pool[key] = st
    if not fluid.is_pure:
        st.set_mole_fractions(list(fluid.x))
    return st


def _build_state(fluid: Fluid, pkg: PropertyPackage) -> AbstractState:
    try:
        st = AbstractState(pkg.method, "&".join(fluid.components))
    except Exception as exc:
        msg = str(exc).split("\n")[0]
        if pkg.cubic:
            missing = []
            for c in fluid.components:
                try:
                    AbstractState(pkg.method, c)
                except Exception:
                    missing.append(c)
            raise ThermoError(
                f"{', '.join(missing) or fluid.label()} is not available for {METHODS[pkg.method]} in CoolProp "
                f"(no critical constants in its cubic library). ({msg})") from exc
        pseudo = [c for c in fluid.components if is_pseudo_pure(c)]
        if pseudo:
            raise ThermoError(
                f"{', '.join(pseudo)} is a pseudo-pure fluid (a blend modelled as one substance) and "
                "cannot be mixed with other fluids in CoolProp. Enter its real components instead, "
                "e.g. R410A = Difluoromethane[0.698]&Pentafluoroethane[0.302].") from exc
        if "binary pair" in msg or "interaction" in msg:
            raise ThermoError(
                f"CoolProp's reference model has no mixing data for some component pairs of {fluid.label()}. "
                "Switch to Peng-Robinson or SRK under Simulation ▸ Fluid package, where missing k_ij can be "
                f"entered or estimated. ({msg})") from exc
        raise ThermoError(f"CoolProp cannot build fluid {fluid.label()}: {msg}") from exc
    if pkg.cubic and not fluid.is_pure:
        comps = fluid.components
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                k, _src = kij_for(comps[i], comps[j], pkg)
                if k != 0.0:
                    st.set_binary_interaction_double(i, j, "kij", k)
    return st


# ---------------------------------------------------------------------------
# Thermodynamic state
# ---------------------------------------------------------------------------

_PHASES = {
    CoolProp.iphase_liquid: "liquid",
    CoolProp.iphase_gas: "vapor",
    CoolProp.iphase_twophase: "two-phase",
    CoolProp.iphase_supercritical: "supercritical",
    CoolProp.iphase_supercritical_gas: "supercritical gas",
    CoolProp.iphase_supercritical_liquid: "supercritical liquid",
}


@dataclass(frozen=True)
class State:
    fluid: Fluid
    P: float
    T: float
    h: float
    s: float
    rho: float
    Q: Optional[float]          # vapor fraction (molar for mixtures); None if single phase
    phase: str
    cp: Optional[float] = None
    x_liq: Optional[tuple[float, ...]] = None   # liquid mole fractions (two-phase mixtures)
    y_vap: Optional[tuple[float, ...]] = None   # vapor mole fractions (two-phase mixtures)

    @property
    def is_two_phase(self) -> bool:
        return self.Q is not None and 0.0 < self.Q < 1.0

    @property
    def vapor_fraction(self) -> float:
        """Molar vapor fraction; 0 for liquid-like and 1 for vapor-like single phase."""
        if self.Q is not None:
            return self.Q
        return 0.0 if "liquid" in self.phase else 1.0

    @property
    def vapor_mass_fraction(self) -> float:
        if self.Q is None:
            return self.vapor_fraction
        if self.fluid.is_pure or self.y_vap is None:
            return self.Q
        mv = sum(y * component_molar_mass(c) for c, y in zip(self.fluid.components, self.y_vap))
        return self.Q * mv / self.fluid.molar_mass

    def is_liquid_like(self) -> bool:
        if self.Q is not None:
            return self.Q <= 0.0
        return "liquid" in self.phase


_PAIRS = {
    "PT": CoolProp.PT_INPUTS,
    "PH": CoolProp.HmassP_INPUTS,
    "PS": CoolProp.PSmass_INPUTS,
    "PQ": CoolProp.PQ_INPUTS,
    "TQ": CoolProp.QT_INPUTS,
}


@lru_cache(maxsize=200_000)
def _flash_cached(token: int, fluid: Fluid, pair: str, a: float, b: float) -> State:
    st = _state_for(fluid)
    code = _PAIRS[pair]
    # CoolProp expects specific argument orders for some input pairs
    if pair == "PT":
        args = (a, b)
    elif pair == "PH":
        args = (b, a)          # (h, P)
    elif pair == "PS":
        args = (a, b)          # (P, s)
    elif pair == "PQ":
        args = (a, b)          # (P, Q)
    else:  # TQ
        args = (b, a)          # (Q, T)
    try:
        st.update(code, *args)
    except Exception as exc:
        names = {"PT": ("P", "T"), "PH": ("P", "h"), "PS": ("P", "s"), "PQ": ("P", "Q"), "TQ": ("T", "Q")}[pair]
        msg = str(exc).split("\n")[0]
        raise ThermoError(
            f"Property calculation failed for {fluid.label()} at {names[0]}={a:.6g}, {names[1]}={b:.6g}: {msg}"
        ) from exc

    Q = st.Q()
    if not (0.0 <= Q <= 1.0):
        Q = None
    try:
        ph = st.phase()
        phase = _PHASES.get(ph, "single phase")
    except Exception:
        phase = "single phase"
    if Q is not None and 0.0 < Q < 1.0:
        phase = "two-phase"
    elif Q is not None:
        phase = "saturated liquid" if Q == 0.0 else "saturated vapor"
    elif not fluid.is_pure and phase not in ("liquid", "vapor") and "super" not in phase:
        phase = "single phase"

    x_liq = y_vap = None
    if Q is not None and not fluid.is_pure:
        try:
            x_liq = tuple(st.mole_fractions_liquid())
            y_vap = tuple(st.mole_fractions_vapor())
        except Exception:
            pass
    cp = None
    if Q is None:
        try:
            cp = st.cpmass()
        except Exception:
            cp = None
    return State(fluid=fluid, P=st.p(), T=st.T(), h=st.hmass(), s=st.smass(), rho=st.rhomass(),
                 Q=Q, phase=phase, cp=cp, x_liq=x_liq, y_vap=y_vap)


def flash(fluid: Fluid, pair: str, a: float, b: float) -> State:
    """Compute a full state from an input pair.

    pair is one of 'PT', 'PH', 'PS', 'PQ', 'TQ'; (a, b) follow that order,
    e.g. flash(f, 'PH', P, h).
    """
    if pair not in _PAIRS:
        raise ValueError(f"Unsupported input pair {pair}")
    if pair[0] == "P" and not a > 0:
        raise ThermoError(f"Pressure must be positive (got {a:.6g} Pa).")
    if pair == "PT" and not b > 0:
        raise ThermoError(f"Temperature must be positive (got {b:.6g} K).")
    if pair in ("PQ", "TQ") and not (0.0 <= b <= 1.0):
        raise ThermoError("Vapor fraction must be between 0 and 1.")
    token = _PKG.token
    if _PKG.cubic and not fluid.is_pure and pair in ("PH", "PS"):
        # CoolProp's own H/S flash for cubic mixtures takes ~0.5 s; iterating on T with
        # PT flashes (a few ms each) gives the same state 10-50x faster.
        return _solve_T_cached(token, fluid, float(a), "h" if pair == "PH" else "s", float(b))
    try:
        return _flash_cached(token, fluid, pair, float(a), float(b))
    except ThermoError:
        # CoolProp's mixture PH/PS flash sometimes misclassifies the phase. Fall back to
        # a bracketed search on temperature using PT flashes, which are reliable.
        if fluid.is_pure or pair not in ("PH", "PS"):
            raise
        return _solve_T_cached(token, fluid, float(a), "h" if pair == "PH" else "s", float(b))


@lru_cache(maxsize=100_000)
def _solve_T_cached(token: int, fluid: Fluid, P: float, prop: str, target: float) -> State:
    return _solve_T(token, fluid, P, prop, target)


def _solve_T(token: int, fluid: Fluid, P: float, prop: str, target: float) -> State:
    """Find T at pressure P where h (or s) equals target, using PT flashes (Illinois method)."""
    cache: dict[float, State] = {}

    def f(T):
        st = cache.get(T)
        if st is None:
            st = cache[T] = _flash_cached(token, fluid, "PT", P, T)
        return getattr(st, prop) - target

    def safe(T):
        for dT in (0.0, 1e-4, -1e-4, 1e-2):
            try:
                return T + dT, f(T + dT)
            except ThermoError:
                continue
        return T, None

    # bracket: property increases monotonically with T at constant P
    T_lo, f_lo = safe(300.0)
    T_hi, f_hi = T_lo, f_lo
    step = 30.0
    for _ in range(60):
        if f_lo is not None and f_lo <= 0:
            break
        T_lo = max(T_lo - step, 1.0) if f_lo is not None else T_lo * 0.9
        T_lo, f_lo = safe(T_lo)
        step *= 1.5
        if T_lo <= 1.0:
            break
    step = 30.0
    for _ in range(60):
        if f_hi is not None and f_hi >= 0:
            break
        T_hi = T_hi + step if f_hi is not None else T_hi * 1.1
        T_hi, f_hi = safe(T_hi)
        step *= 1.5
        if T_hi > 4000:
            break
    if f_lo is None or f_hi is None or f_lo > 0 or f_hi < 0:
        raise ThermoError(f"Property calculation failed for {fluid.label()} at P={P:.6g} Pa, "
                          f"{prop}={target:.6g} (could not bracket the temperature).")
    tol = 1e-9 * max(abs(target), 1e4)
    side = 0
    for _ in range(100):
        if f_hi == f_lo:
            T = 0.5 * (T_lo + T_hi)
        else:
            T = T_hi - f_hi * (T_hi - T_lo) / (f_hi - f_lo)
            if not T_lo < T < T_hi:
                T = 0.5 * (T_lo + T_hi)
        T, fT = safe(T)
        if fT is None:
            raise ThermoError(f"Property calculation failed for {fluid.label()} at P={P:.6g} Pa, T={T:.6g} K.")
        if abs(fT) <= tol or T_hi - T_lo < 1e-9:
            return cache[T]
        if fT < 0:
            T_lo, f_lo = T, fT
            if side == -1:
                f_hi *= 0.5
            side = -1
        else:
            T_hi, f_hi = T, fT
            if side == 1:
                f_lo *= 0.5
            side = 1
    return cache[T]


def critical_point(fluid: Fluid) -> tuple[float, float]:
    st = _state_for(fluid)
    try:
        return st.T_critical(), st.p_critical()
    except Exception as exc:
        raise ThermoError(f"No critical point available for {fluid.label()}") from exc


def saturation_dome(fluid: Fluid, n: int = 120):
    """Return dict of arrays (T, P, s_l, s_v, h_l, h_v) for a pure fluid."""
    import numpy as np

    if not fluid.is_pure:
        return None
    st = _state_for(fluid)
    Tc = st.T_critical()
    try:
        Tmin = max(st.Ttriple(), st.Tmin())
    except Exception:
        try:
            Tmin = AbstractState("HEOS", fluid.components[0]).Ttriple()
        except Exception:
            Tmin = 0.4 * Tc
    Ts = np.linspace(Tmin + 0.01, Tc * 0.9995, n)
    out = {k: [] for k in ("T", "P", "s_l", "s_v", "h_l", "h_v")}
    for T in Ts:
        try:
            st.update(CoolProp.QT_INPUTS, 0.0, T)
            P, sl, hl = st.p(), st.smass(), st.hmass()
            st.update(CoolProp.QT_INPUTS, 1.0, T)
            sv, hv = st.smass(), st.hmass()
        except Exception:
            continue
        for k, v in zip(out, (T, P, sl, sv, hl, hv)):
            out[k].append(v)
    return {k: np.array(v) for k, v in out.items()}


# ---------------------------------------------------------------------------
# Chemistry: formulas, standard enthalpies of formation, formation-basis enthalpy
# ---------------------------------------------------------------------------

# Ideal-gas standard enthalpies of formation at 298.15 K [J/mol]
# (NIST Chemistry WebBook / NIST-JANAF thermochemical tables).
FORMATION_ENTHALPY = {
    "Hydrogen": 0.0, "Oxygen": 0.0, "Nitrogen": 0.0, "Argon": 0.0, "Helium": 0.0, "Neon": 0.0,
    "Krypton": 0.0, "Xenon": 0.0,
    "Water": -241_826.0, "CarbonDioxide": -393_510.0, "CarbonMonoxide": -110_530.0,
    "Methane": -74_870.0, "Ethane": -83_850.0, "n-Propane": -104_680.0, "n-Butane": -125_790.0,
    "IsoButane": -134_990.0, "n-Pentane": -146_760.0, "Isopentane": -153_700.0, "n-Hexane": -166_920.0,
    "n-Heptane": -187_780.0, "n-Octane": -208_750.0,
    "Ethylene": 52_470.0, "Propylene": 20_000.0,
    "Methanol": -200_940.0, "Ethanol": -234_950.0, "DimethylEther": -184_100.0,
    "Ammonia": -45_900.0, "HydrogenSulfide": -20_600.0, "SulfurDioxide": -296_840.0,
    "NitrousOxide": 82_050.0, "CarbonylSulfide": -138_410.0,
    "Benzene": 82_930.0, "Toluene": 50_170.0, "Acetone": -217_100.0,
}

FORMULA_ALIASES = {
    "C2H6": "Ethane", "C3H8": "n-Propane", "C4H10": "n-Butane", "C5H12": "n-Pentane", "C6H14": "n-Hexane",
    "C2H4": "Ethylene", "C3H6": "Propylene", "CH3OH": "Methanol", "C2H5OH": "Ethanol", "NH3": "Ammonia",
    "H2S": "HydrogenSulfide", "SO2": "SulfurDioxide", "N2O": "NitrousOxide", "COS": "CarbonylSulfide",
    "He": "Helium", "Ne": "Neon", "C6H6": "Benzene",
}


def species_name(token: str) -> str:
    """Canonical CoolProp name for a species written as a formula (CH4) or a name (Methane)."""
    token = token.strip()
    return canonical_name(FORMULA_ALIASES.get(token, token))


@lru_cache(maxsize=None)
def elements(name: str) -> Optional[dict]:
    """Element counts of a component from CoolProp's formula string, e.g. {'C': 1, 'H': 4}."""
    import re
    try:
        f = CP.get_fluid_param_string(name, "formula")
    except Exception:
        return None
    if not f or f.upper() == "N/A":
        return None
    out: dict[str, float] = {}
    for el, n in re.findall(r"([A-Z][a-z]?)_\{(\d+(?:\.\d+)?)\}", f):
        out[el] = out.get(el, 0.0) + float(n)
    return out or None


_OFFSETS: dict[tuple[int, str], float] = {}


def formation_offset(comp: str) -> float:
    """J/mol to add to the active package's molar enthalpy so that it becomes a formation-basis enthalpy.

    offset = dHf(298.15 K, ideal gas) - h_package(ideal gas, 298.15 K). Components without a tabulated
    dHf get dHf = 0, which is harmless as long as they do not react.
    """
    key = (_PKG.token, comp)
    v = _OFFSETS.get(key)
    if v is None:
        st = _state_for(Fluid((comp,), (1.0,)))
        try:
            st.update(CoolProp.PT_INPUTS, 10.0, 298.15)          # ~ideal gas
            h_ig = st.hmolar()
        except Exception as exc:
            raise ThermoError(f"Could not evaluate the ideal-gas enthalpy of {comp} at 298.15 K: {exc}") from exc
        v = FORMATION_ENTHALPY.get(comp, 0.0) - h_ig
        _OFFSETS[key] = v
    return v


def formation_enthalpy_flow(mat) -> float:
    """Total enthalpy flow [W] of a Material on the formation basis (elements at 298.15 K = 0).

    Each stream is evaluated with its own property model (a pseudo-pure Air stream uses the
    pseudo-pure model, with dHf = 0), so the result is consistent with the work and heat that
    the upstream blocks computed with that same model.
    """
    f = mat.fluid
    n = mat.m / f.molar_mass
    return mat.m * mat.h + n * sum(x * formation_offset(c) for c, x in zip(f.components, f.x))
