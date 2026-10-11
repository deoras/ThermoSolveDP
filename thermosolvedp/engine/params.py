"""Parameter descriptors used by unit operations (and by the GUI to build forms)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence


@dataclass
class Param:
    key: str
    label: str
    kind: str                     # 'float' | 'int' | 'choice' | 'fluid' | 'str' | 'bool'
    default: Any
    qty: Optional[str] = None     # physical quantity for unit conversion: 'T','dT','P','dP','m','power','UA','frac'
    choices: Sequence[str] = ()
    visible: Optional[Callable[[dict], bool]] = None
    tip: str = ""
    minv: Optional[float] = None
    maxv: Optional[float] = None
    rebuild_ports: bool = False   # changing this value changes the block's ports

    def is_visible(self, values: dict) -> bool:
        return True if self.visible is None else bool(self.visible(values))


def when(key: str, *options: str) -> Callable[[dict], bool]:
    """Helper: param visible only when values[key] is one of options."""
    return lambda v: v.get(key) in options


@dataclass
class Result:
    """A labelled result value shown in the GUI (stored in SI)."""
    label: str
    value: Any
    qty: Optional[str] = None     # same quantity codes as Param, plus 'h','s','none'
