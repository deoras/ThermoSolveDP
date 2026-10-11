"""Property diagrams: T–s, P–h (with saturation dome and process paths) and heat-exchanger T–Q."""
from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout,
                               QWidget)

from ..engine.flowsheet import Flowsheet
from ..engine.thermo import Fluid, ThermoError, flash, saturation_dome
from ..engine.unitops import HeatExchanger
from ..engine.units import UnitSystem
from .icons import is_dark

LIGHT = {"surface": "#fcfcfb", "text": "#0b0b0b", "muted": "#52514e", "grid": "#e4e3df",
         "process": "#2a78d6", "hot": "#eb6834", "cold": "#2a78d6", "dome": "#8a8984"}
DARK = {"surface": "#1a1a19", "text": "#ffffff", "muted": "#c3c2b7", "grid": "#383835",
        "process": "#3987e5", "hot": "#d95926", "cold": "#3987e5", "dome": "#8a8984"}


def theme() -> dict:
    return DARK if is_dark() else LIGHT


def style_axes(fig: Figure, ax, th: dict):
    fig.set_facecolor(th["surface"])
    ax.set_facecolor(th["surface"])
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(th["grid"])
    ax.tick_params(colors=th["muted"], labelsize=9)
    ax.xaxis.label.set_color(th["muted"])
    ax.yaxis.label.set_color(th["muted"])
    ax.title.set_color(th["text"])
    ax.grid(True, color=th["grid"], linewidth=0.7)
    ax.set_axisbelow(True)


def path_states(path: dict, n: int = 24):
    """States along a block's process path (pure fluids get intermediate flashes)."""
    f: Fluid = path["fluid"]
    P1, P2, h1, h2 = path["P1"], path["P2"], path["h1"], path["h2"]
    if not f.is_pure or path["start"].fluid != path["end"].fluid:
        # mixture flashes are slow: draw a straight segment between the computed end states
        return [path["start"], path["end"]]
    out = [path["start"]]
    for t in np.linspace(0.0, 1.0, n + 1)[1:-1]:
        try:
            if path["kind"] == "isenthalpic":          # valve: constant h, falling P
                out.append(flash(f, "PH", P1 * (P2 / P1) ** t, h1))
            elif path["kind"] == "compression":        # log P with linearly varying entropy
                s1, s2 = path["start"].s, path["end"].s
                out.append(flash(f, "PS", P1 * (P2 / P1) ** t, s1 + (s2 - s1) * t))
            else:                                      # (nearly) isobaric heat transfer
                out.append(flash(f, "PH", P1 + (P2 - P1) * t, h1 + (h2 - h1) * t))
        except ThermoError:
            pass
    out.append(path["end"])
    return out


class _PlotTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        self.bar = QHBoxLayout()
        lay.addLayout(self.bar)
        self.fig = Figure(figsize=(7, 5), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.fig)
        lay.addWidget(NavigationToolbar2QT(self.canvas, self))
        lay.addWidget(self.canvas, 1)


class PropertyDiagramTab(_PlotTab):
    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind     # 'Ts' or 'Ph'
        self.bar.addWidget(QLabel("Fluid:"))
        self.fluid = QComboBox()
        self.fluid.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.fluid.currentIndexChanged.connect(lambda _i: self.draw())
        self.bar.addWidget(self.fluid)
        self.bar.addStretch()
        self.fs: Flowsheet | None = None
        self.units = UnitSystem()

    def set_data(self, fs: Flowsheet, units: UnitSystem):
        self.fs, self.units = fs, units
        fluids = []
        for s in fs.streams.values():
            if s.material is not None and s.material.fluid.spec() not in fluids:
                fluids.append(s.material.fluid.spec())
        cur = self.fluid.currentText()
        self.fluid.blockSignals(True)
        self.fluid.clear()
        for f in fluids:
            self.fluid.addItem(Fluid.parse(f).label(), f)
        idx = max(0, self.fluid.findText(cur))
        self.fluid.setCurrentIndex(idx)
        self.fluid.blockSignals(False)
        self.draw()

    def draw(self):
        th = theme()
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        style_axes(self.fig, ax, th)
        u = self.units
        if self.fs is None or self.fluid.count() == 0:
            ax.text(0.5, 0.5, "Run the simulation to see the diagram", ha="center", va="center",
                    color=th["muted"], transform=ax.transAxes)
            self.canvas.draw_idle()
            return
        spec = self.fluid.currentData()
        fluid = Fluid.parse(spec)
        Ts = self.kind == "Ts"

        def xy(T, P, s, h):
            if Ts:
                return u.to_display("s", s), u.to_display("T", T)
            return u.to_display("h", h), u.to_display("P", P)

        dome = saturation_dome(fluid) if fluid.is_pure else None
        if dome is not None and len(dome["T"]):
            xl, yl = xy(dome["T"], dome["P"], dome["s_l"], dome["h_l"])
            xv, yv = xy(dome["T"], dome["P"], dome["s_v"], dome["h_v"])
            ax.plot(np.concatenate([xl, xv[::-1]]), np.concatenate([yl, yv[::-1]]), color=th["dome"],
                    linewidth=1.4, label="Saturation dome")

        drew_path = False
        for b in self.fs.blocks.values():
            for path in b.paths:
                if path["fluid"].spec() != spec:
                    continue
                states = path_states(path)
                if len(states) < 2:
                    continue
                pts = [xy(st.T, st.P, st.s, st.h) for st in states]
                ax.plot([p[0] for p in pts], [p[1] for p in pts], color=th["process"], linewidth=2,
                        label=None if drew_path else "Process path", solid_capstyle="round")
                drew_path = True

        xs, ys, names = [], [], []
        for s in self.fs.streams.values():
            m = s.material
            if m is None or m.fluid.spec() != spec or m.m <= 0:
                continue
            x, y = xy(m.T, m.P, m.s, m.h)
            xs.append(x); ys.append(y); names.append(s.name)
        ax.scatter(xs, ys, s=42, color=th["process"], edgecolor=th["surface"], linewidth=2, zorder=5,
                   label="Stream states")
        for x, y, n in zip(xs, ys, names):
            ax.annotate(n, (x, y), textcoords="offset points", xytext=(6, 5), fontsize=9, color=th["text"])

        if Ts:
            ax.set_xlabel(f"Specific entropy  s  [{u.label('s')}]")
            ax.set_ylabel(f"Temperature  T  [{u.label('T')}]")
        else:
            ax.set_xlabel(f"Specific enthalpy  h  [{u.label('h')}]")
            ax.set_ylabel(f"Pressure  P  [{u.label('P')}]")
            ax.set_yscale("log")
        title = f"{'T–s' if Ts else 'P–h'} diagram: {fluid.label()}"
        if not fluid.is_pure:
            title += "  (mixture: no dome; paths drawn as straight segments)"
        ax.set_title(title, fontsize=11, loc="left")
        leg = ax.legend(frameon=False, fontsize=9, loc="best")
        for t in leg.get_texts():
            t.set_color(th["muted"])
        self.canvas.draw_idle()


class TQTab(_PlotTab):
    def __init__(self):
        super().__init__()
        self.bar.addWidget(QLabel("Heat exchanger:"))
        self.hx = QComboBox()
        self.hx.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.hx.currentIndexChanged.connect(lambda _i: self.draw())
        self.bar.addWidget(self.hx)
        self.bar.addStretch()
        self.fs = None
        self.units = UnitSystem()

    def set_data(self, fs: Flowsheet, units: UnitSystem):
        self.fs, self.units = fs, units
        cur = self.hx.currentText()
        self.hx.blockSignals(True)
        self.hx.clear()
        for b in fs.blocks.values():
            if isinstance(b, HeatExchanger) and b.profile:
                self.hx.addItem(b.name, b.uid)
        self.hx.setCurrentIndex(max(0, self.hx.findText(cur)))
        self.hx.blockSignals(False)
        self.draw()

    def draw(self):
        th = theme()
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        style_axes(self.fig, ax, th)
        u = self.units
        if self.fs is None or self.hx.count() == 0:
            ax.text(0.5, 0.5, "No solved heat exchanger in this flowsheet", ha="center", va="center",
                    color=th["muted"], transform=ax.transAxes)
            self.canvas.draw_idle()
            return
        b = self.fs.blocks[self.hx.currentData()]
        pr = b.profile
        q = np.array([u.to_display("power", x) for x in pr["q"]])
        Th = np.array([u.to_display("T", x) for x in pr["Th"]])
        Tc = np.array([u.to_display("T", x) for x in pr["Tc"]])
        ax.plot(q, Th, color=th["hot"], linewidth=2, label="Hot stream")
        ax.plot(q, Tc, color=th["cold"], linewidth=2, label="Cold stream")
        dts = np.array(pr["Th"]) - np.array(pr["Tc"])
        i = int(np.argmin(dts))
        ax.annotate(f"min ΔT = {u.fmt('dT', dts[i], 3)} {u.label('dT')}", (q[i], (Th[i] + Tc[i]) / 2),
                    textcoords="offset points", xytext=(8, 0), fontsize=9, color=th["text"], va="center")
        ax.plot([q[i], q[i]], [Tc[i], Th[i]], color=th["muted"], linewidth=1, linestyle=(0, (2, 2)))
        ax.set_xlabel(f"Heat transferred from hot inlet  [{u.label('power')}]")
        ax.set_ylabel(f"Temperature  [{u.label('T')}]")
        ax.set_title(f"T–Q profile: {b.name} (counter-current)", fontsize=11, loc="left")
        leg = ax.legend(frameon=False, fontsize=9)
        for t in leg.get_texts():
            t.set_color(th["muted"])
        self.canvas.draw_idle()


class PlotWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Property diagrams")
        self.resize(860, 640)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.ts = PropertyDiagramTab("Ts")
        self.ph = PropertyDiagramTab("Ph")
        self.tq = TQTab()
        self.tabs.addTab(self.ts, "T–s")
        self.tabs.addTab(self.ph, "P–h")
        self.tabs.addTab(self.tq, "Heat exchanger T–Q")
        lay.addWidget(self.tabs)
        row = QHBoxLayout()
        row.addWidget(QLabel("Compressor, pump and expander paths are drawn with entropy varying "
                             "linearly in log P between the computed end states (illustrative)."))
        row.itemAt(0).widget().setEnabled(False)
        row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)

    def set_data(self, fs: Flowsheet, units: UnitSystem):
        for t in (self.ts, self.ph, self.tq):
            t.set_data(fs, units)
