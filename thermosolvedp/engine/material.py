"""Material stream state: a fluid, a mass flow and a thermodynamic state."""
from __future__ import annotations

from dataclasses import dataclass

from .thermo import Fluid, State, flash


@dataclass(frozen=True)
class Material:
    m: float            # mass flow [kg/s]
    state: State

    # -- constructors -----------------------------------------------------------
    @staticmethod
    def from_PT(fluid: Fluid, m: float, P: float, T: float) -> "Material":
        return Material(m, flash(fluid, "PT", P, T))

    @staticmethod
    def from_PH(fluid: Fluid, m: float, P: float, h: float) -> "Material":
        return Material(m, flash(fluid, "PH", P, h))

    @staticmethod
    def from_PS(fluid: Fluid, m: float, P: float, s: float) -> "Material":
        return Material(m, flash(fluid, "PS", P, s))

    @staticmethod
    def from_PQ(fluid: Fluid, m: float, P: float, Q: float) -> "Material":
        return Material(m, flash(fluid, "PQ", P, Q))

    @staticmethod
    def from_TQ(fluid: Fluid, m: float, T: float, Q: float) -> "Material":
        return Material(m, flash(fluid, "TQ", T, Q))

    def with_flow(self, m: float) -> "Material":
        return Material(m, self.state)

    # -- shortcuts ---------------------------------------------------------------
    @property
    def fluid(self) -> Fluid:
        return self.state.fluid

    @property
    def P(self) -> float:
        return self.state.P

    @property
    def T(self) -> float:
        return self.state.T

    @property
    def h(self) -> float:
        return self.state.h

    @property
    def s(self) -> float:
        return self.state.s

    @property
    def H(self) -> float:
        """Enthalpy flow [W]."""
        return self.m * self.state.h

    @property
    def n(self) -> float:
        """Molar flow [mol/s]."""
        return self.m / self.fluid.molar_mass
