"""Display-unit conversion. The engine works in SI; the GUI converts on the way in and out."""
from __future__ import annotations

from dataclasses import dataclass

# factor-based units: value_SI = value_display * factor
P_UNITS = {"Pa": 1.0, "kPa": 1e3, "bar": 1e5, "MPa": 1e6, "atm": 101325.0, "psi": 6894.757293168}
M_UNITS = {"kg/s": 1.0, "kg/h": 1 / 3600, "t/h": 1000 / 3600, "lb/h": 0.45359237 / 3600, "lb/s": 0.45359237}
POWER_UNITS = {"W": 1.0, "kW": 1e3, "MW": 1e6}
T_UNITS = ("°C", "K", "°F")


@dataclass
class UnitSystem:
    T: str = "°C"
    P: str = "bar"
    m: str = "kg/s"
    power: str = "kW"

    # -- labels -------------------------------------------------------------------
    def label(self, qty: str | None) -> str:
        return {
            "T": self.T,
            "dT": "°F" if self.T == "°F" else "K",
            "P": self.P,
            "dP": self.P,
            "m": self.m,
            "power": self.power,
            "UA": f"{self.power}/K",
            "h": "kJ/kg",
            "s": "kJ/kg·K",
            "rho": "kg/m³",
            "cp": "kJ/kg·K",
            "M": "g/mol",
            "frac": "–",
            "JT": f"K/{self.P}" if self.T != "°F" else f"°F/{self.P}",
        }.get(qty or "", "")

    # -- conversions ---------------------------------------------------------------
    def to_display(self, qty: str | None, v):
        if v is None or isinstance(v, (str, bool)):
            return v       # numbers and numpy arrays are converted below
        if qty == "T":
            return {"K": v, "°C": v - 273.15, "°F": (v - 273.15) * 1.8 + 32}[self.T]
        if qty == "dT":
            return v * 1.8 if self.T == "°F" else v
        if qty in ("P", "dP"):
            return v / P_UNITS[self.P]
        if qty == "m":
            return v / M_UNITS[self.m]
        if qty == "power":
            return v / POWER_UNITS[self.power]
        if qty == "UA":
            return v / POWER_UNITS[self.power]
        if qty in ("h", "s", "cp"):
            return v / 1e3
        if qty == "M":
            return v * 1e3
        if qty == "JT":  # K/Pa -> display
            return v * P_UNITS[self.P] * (1.8 if self.T == "°F" else 1.0)
        return v

    def from_display(self, qty: str | None, v: float) -> float:
        if qty == "T":
            return {"K": v, "°C": v + 273.15, "°F": (v - 32) / 1.8 + 273.15}[self.T]
        if qty == "dT":
            return v / 1.8 if self.T == "°F" else v
        if qty in ("P", "dP"):
            return v * P_UNITS[self.P]
        if qty == "m":
            return v * M_UNITS[self.m]
        if qty in ("power", "UA"):
            return v * POWER_UNITS[self.power]
        if qty in ("h", "s", "cp"):
            return v * 1e3
        if qty == "M":
            return v / 1e3
        return v

    def fmt(self, qty: str | None, v, digits: int = 5) -> str:
        if v is None:
            return "–"
        if isinstance(v, str):
            return v
        if isinstance(v, bool):
            return "yes" if v else "no"
        d = self.to_display(qty, v)
        if isinstance(d, int) and not isinstance(d, bool):
            return str(d)
        if d == 0:
            return "0"
        a = abs(d)
        if a >= 1e6 or a < 1e-3:
            return f"{d:.{digits - 1}e}"
        return f"{d:.{digits}g}" if a >= 1 else f"{d:.{digits - 1}f}".rstrip("0").rstrip(".")

    def to_dict(self) -> dict:
        return {"T": self.T, "P": self.P, "m": self.m, "power": self.power}

    @staticmethod
    def from_dict(d: dict) -> "UnitSystem":
        u = UnitSystem()
        for k in ("T", "P", "m", "power"):
            if k in d:
                setattr(u, k, d[k])
        return u
