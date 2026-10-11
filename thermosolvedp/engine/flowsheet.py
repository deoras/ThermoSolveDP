"""Flowsheet model: blocks connected by material streams, plus file I/O."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .material import Material
from .unitops import BLOCK_TYPES, UnitOp

FILE_VERSION = 1


@dataclass
class Stream:
    uid: str
    name: str
    src: str
    src_port: str
    dst: str
    dst_port: str
    tear: bool = False
    # initial estimate for recycle convergence (SI); fluid '' = take from the upstream feed
    estimate: dict = field(default_factory=lambda: {"enabled": False, "fluid": "", "T": 298.15,
                                                    "P": 101325.0, "m": 1.0})
    material: Optional[Material] = None
    waypoints: list = field(default_factory=list)   # reserved for manual routing

    def to_dict(self) -> dict:
        return {"uid": self.uid, "name": self.name, "src": self.src, "src_port": self.src_port,
                "dst": self.dst, "dst_port": self.dst_port, "tear": self.tear, "estimate": dict(self.estimate)}


class FlowsheetError(Exception):
    pass


class Flowsheet:
    def __init__(self):
        self.blocks: dict[str, UnitOp] = {}
        self.streams: dict[str, Stream] = {}
        self.settings = {"max_iter": 200, "tol": 1e-6, "method": "Broyden",
                         "property_method": "HEOS", "kij": {}}
        self.units: dict = {}
        self._next_id = 1
        self.solved = False

    # -- ids / names -----------------------------------------------------------------
    def _new_uid(self, prefix: str) -> str:
        uid = f"{prefix}{self._next_id}"
        self._next_id += 1
        return uid

    def _unique_name(self, base: str, existing: set[str]) -> str:
        i = 1
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"

    def unique_stream_name(self) -> str:
        names = {s.name for s in self.streams.values()}
        i = 1
        while f"S{i}" in names:
            i += 1
        return f"S{i}"

    # -- editing ------------------------------------------------------------------------
    def add_block(self, type_name: str, x: float = 0.0, y: float = 0.0, name: Optional[str] = None) -> UnitOp:
        cls = BLOCK_TYPES[type_name]
        if name is None:
            name = self._unique_name(cls.PREFIX, {b.name for b in self.blocks.values()})
        blk = cls(self._new_uid("b"), name)
        blk.layout.update({"x": x, "y": y})
        self.blocks[blk.uid] = blk
        self.solved = False
        return blk

    def remove_block(self, uid: str) -> list[str]:
        removed = [s.uid for s in list(self.streams.values()) if s.src == uid or s.dst == uid]
        for sid in removed:
            del self.streams[sid]
        del self.blocks[uid]
        self.solved = False
        return removed

    def connect(self, src: str, src_port: str, dst: str, dst_port: str, name: Optional[str] = None) -> Stream:
        sb, db = self.blocks.get(src), self.blocks.get(dst)
        if sb is None or db is None:
            raise FlowsheetError("Unknown block.")
        if src_port not in {p for p, _ in sb.outlet_ports()}:
            raise FlowsheetError(f"{sb.name} has no outlet '{src_port}'.")
        if dst_port not in {p for p, _ in db.inlet_ports()}:
            raise FlowsheetError(f"{db.name} has no inlet '{dst_port}'.")
        if self.stream_from(src, src_port):
            raise FlowsheetError(f"Outlet '{src_port}' of {sb.name} is already connected.")
        if self.stream_to(dst, dst_port):
            raise FlowsheetError(f"Inlet '{dst_port}' of {db.name} is already connected.")
        s = Stream(self._new_uid("s"), name or self.unique_stream_name(), src, src_port, dst, dst_port)
        self.streams[s.uid] = s
        self.solved = False
        return s

    def remove_stream(self, uid: str) -> None:
        self.streams.pop(uid, None)
        self.solved = False

    def stream_from(self, block: str, port: str) -> Optional[Stream]:
        for s in self.streams.values():
            if s.src == block and s.src_port == port:
                return s
        return None

    def stream_to(self, block: str, port: str) -> Optional[Stream]:
        for s in self.streams.values():
            if s.dst == block and s.dst_port == port:
                return s
        return None

    def prune_ports(self, uid: str) -> list[str]:
        """Remove streams attached to ports that a block no longer has (e.g. fewer mixer inlets)."""
        blk = self.blocks[uid]
        ins = {p for p, _ in blk.inlet_ports()}
        outs = {p for p, _ in blk.outlet_ports()}
        gone = [s.uid for s in self.streams.values()
                if (s.dst == uid and s.dst_port not in ins) or (s.src == uid and s.src_port not in outs)]
        for sid in gone:
            del self.streams[sid]
        return gone

    def clear_results(self) -> None:
        for b in self.blocks.values():
            b.reset()
        for s in self.streams.values():
            s.material = None
        self.solved = False

    # -- persistence ------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "app": "ThermoSolveDP", "version": FILE_VERSION,
            "settings": dict(self.settings), "units": dict(self.units), "next_id": self._next_id,
            "blocks": [b.to_dict() for b in self.blocks.values()],
            "streams": [s.to_dict() for s in self.streams.values()],
        }

    @staticmethod
    def from_dict(d: dict) -> "Flowsheet":
        fs = Flowsheet()
        fs.settings.update(d.get("settings", {}))
        fs.units = dict(d.get("units", {}))
        for bd in d.get("blocks", []):
            cls = BLOCK_TYPES.get(bd["type"])
            if cls is None:
                raise FlowsheetError(f"Unknown block type '{bd['type']}' in file.")
            blk = cls(bd["uid"], bd["name"])
            blk.load_values(bd.get("values", {}))
            blk.layout.update(bd.get("layout", {}))
            fs.blocks[blk.uid] = blk
        for sd in d.get("streams", []):
            s = Stream(sd["uid"], sd["name"], sd["src"], sd["src_port"], sd["dst"], sd["dst_port"],
                       tear=sd.get("tear", False))
            s.estimate.update(sd.get("estimate", {}))
            if s.src in fs.blocks and s.dst in fs.blocks:
                fs.streams[s.uid] = s
        # make sure new ids never collide with loaded ones
        nums = [int("".join(ch for ch in uid if ch.isdigit()) or 0) for uid in list(fs.blocks) + list(fs.streams)]
        fs._next_id = max([d.get("next_id", 1)] + [n + 1 for n in nums])
        return fs

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "Flowsheet":
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise FlowsheetError(f"Not a valid flowsheet file: {exc}") from exc
        return Flowsheet.from_dict(d)
