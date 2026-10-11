"""Block sizes and port geometry (no Qt imports, so it is usable from scripts and tests)."""
from __future__ import annotations

SIZES = {
    "feed": (52, 30), "product": (52, 30),
    "heater": (52, 52), "cooler": (52, 52),
    "compressor": (62, 52), "pump": (52, 52), "expander": (62, 52),
    "valve": (48, 30), "hx": (84, 56), "flash": (46, 84),
    "mixer": (40, 52), "splitter": (40, 52),
    "reactor": (60, 64),
}


def block_size(block) -> tuple[float, float]:
    w, h = SIZES.get(block.TYPE, (60, 50))
    if block.TYPE in ("mixer", "reactor"):
        h = max(h, 18 * (int(block.values["n_in"]) + 1))
    elif block.TYPE == "splitter":
        h = max(h, 18 * (int(block.values["n_out"]) + 1))
    return float(w), float(h)


def port_geometry(block) -> dict[str, tuple[float, float, float, float]]:
    """Return {port_id: (x, y, dx, dy)} in block-local coordinates (origin top-left).

    (dx, dy) is the outward direction in which a connecting line leaves the port.
    Horizontal flipping is applied.
    """
    w, h = block_size(block)
    t = block.TYPE
    g: dict[str, tuple[float, float, float, float]] = {}
    if t == "hx":
        g = {"hot_in": (0, 0.32 * h, -1, 0), "hot_out": (w, 0.32 * h, 1, 0),
             "cold_in": (w, 0.68 * h, 1, 0), "cold_out": (0, 0.68 * h, -1, 0)}
    elif t == "flash":
        g = {"in": (0, 0.5 * h, -1, 0), "vapor": (0.5 * w, 0, 0, -1), "liquid": (0.5 * w, h, 0, 1)}
    elif t in ("mixer", "reactor"):
        ins = block.inlet_ports()
        for k, (p, _) in enumerate(ins):
            g[p] = (0, h * (k + 1) / (len(ins) + 1), -1, 0)
        g["out"] = (w, 0.5 * h, 1, 0)
    elif t == "splitter":
        outs = block.outlet_ports()
        g["in"] = (0, 0.5 * h, -1, 0)
        for k, (p, _) in enumerate(outs):
            g[p] = (w, h * (k + 1) / (len(outs) + 1), 1, 0)
    else:
        for p, _ in block.inlet_ports():
            g[p] = (0, 0.5 * h, -1, 0)
        for p, _ in block.outlet_ports():
            g[p] = (w, 0.5 * h, 1, 0)
    if block.layout.get("flip"):
        g = {p: (w - x, y, -dx, dy) for p, (x, y, dx, dy) in g.items()}
    return g


def place(block, x: float, port_y: float, port: str | None = None) -> None:
    """Position a block so that the given port (default: first inlet or outlet) sits at height port_y."""
    geo = port_geometry(block)
    if port is None:
        ports = [p for p, _ in block.inlet_ports()] or [p for p, _ in block.outlet_ports()]
        port = ports[0]
    block.layout["x"] = x
    block.layout["y"] = port_y - geo[port][1]
