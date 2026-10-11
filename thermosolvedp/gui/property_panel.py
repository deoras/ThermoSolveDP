"""Right-hand panel: edit the selected block's specifications or stream estimate, and show results."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLayout,
                               QLabel, QLineEdit, QScrollArea, QSizePolicy, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..engine.flowsheet import Flowsheet, Stream
from ..engine.material import Material
from ..engine.thermo import Fluid, ThermoError
from ..engine.unitops import UnitOp
from ..engine.units import UnitSystem
from .dialogs import FluidEdit
from .icons import block_icon


def fmt_result(r, u: UnitSystem) -> str:
    if r.value is None:
        return "–"
    if isinstance(r.value, str):
        return r.value
    unit = u.label(r.qty)
    return f"{u.fmt(r.qty, r.value)} {unit if unit != '–' else ''}".strip()


def stream_rows(m: Material, u: UnitSystem) -> list[tuple[str, str]]:
    """(label, formatted value) rows describing a stream."""
    st = m.state
    rows = [
        ("Temperature", f"{u.fmt('T', st.T)} {u.label('T')}"),
        ("Pressure", f"{u.fmt('P', st.P)} {u.label('P')}"),
        ("Mass flow", f"{u.fmt('m', m.m)} {u.label('m')}"),
        ("Molar flow", f"{u.fmt(None, m.n * 3.6)} kmol/h"),
        ("Phase", st.phase),
        ("Vapor fraction (molar)", u.fmt("frac", st.Q) if st.Q is not None else "–"),
        ("Vapor fraction (mass)", u.fmt("frac", st.vapor_mass_fraction) if st.Q is not None else "–"),
        ("Enthalpy", f"{u.fmt('h', st.h)} {u.label('h')}"),
        ("Entropy", f"{u.fmt('s', st.s)} {u.label('s')}"),
        ("Density", f"{u.fmt('rho', st.rho)} {u.label('rho')}"),
        ("Cp", f"{u.fmt('cp', st.cp)} {u.label('cp')}" if st.cp is not None else "–"),
        ("Molar mass", f"{u.fmt('M', m.fluid.molar_mass)} {u.label('M')}"),
        ("Fluid", m.fluid.label() if m.fluid.is_pure else "mixture"),
    ]
    if not m.fluid.is_pure:
        for c, x in zip(m.fluid.components, m.fluid.x):
            rows.append((f"  x {c}", f"{x:.5f}"))
    return rows


class NumberEdit(QWidget):
    """A line edit for a value shown in display units, with a unit label."""
    committed = Signal(float)

    def __init__(self, value_si: float, qty: str | None, units: UnitSystem, tip: str = ""):
        super().__init__()
        self.qty, self.units = qty, units
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(units.fmt(qty, value_si, 8) if qty else f"{value_si:g}")
        self.edit.setToolTip(tip)
        self.edit.setAlignment(Qt.AlignRight)
        self.edit.setMinimumWidth(80)
        self.unit = QLabel(units.label(qty))
        self.unit.setMinimumWidth(52)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.unit)
        self.edit.editingFinished.connect(self._commit)
        self._last = self.edit.text()

    def _commit(self):
        txt = self.edit.text().strip().replace(",", ".")
        if txt == self._last:
            return
        try:
            v = float(txt)
        except ValueError:
            self.edit.setStyleSheet("border: 1px solid #d63939;")
            self.edit.setToolTip("Not a number")
            return
        self.edit.setStyleSheet("")
        self._last = txt
        self.committed.emit(self.units.from_display(self.qty, v))


class PropertyPanel(QScrollArea):
    blockChanged = Signal(str, bool)    # uid, ports_changed
    streamChanged = Signal(str)
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.units = UnitSystem()
        self.fs: Flowsheet | None = None
        self.current = None
        self.show_none()

    # -- helpers -------------------------------------------------------------------------
    def _new_page(self) -> QVBoxLayout:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        lay.setSizeConstraint(QLayout.SetMinAndMaxSize)   # scroll instead of squeezing the form
        self.setWidget(w)
        return lay

    def _header(self, lay, title: str, subtitle: str, icon_type: str | None = None):
        row = QHBoxLayout()
        if icon_type:
            ic = QLabel()
            ic.setPixmap(block_icon(icon_type, 30).pixmap(30, 30))
            row.addWidget(ic)
        t = QLabel(title)
        f = t.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        t.setFont(f)
        row.addWidget(t, 1)
        lay.addLayout(row)
        if subtitle:
            s = QLabel(subtitle)
            s.setWordWrap(True)
            s.setEnabled(False)          # renders in the theme's muted text color
            lay.addWidget(s)

    def _results_table(self, rows: list[tuple[str, str]]) -> QTableWidget:
        from .tables import CopyTable
        t = CopyTable(len(rows), 2)
        t.setHorizontalHeaderLabels(["Result", "Value"])
        t.verticalHeader().hide()
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        t.setTextElideMode(Qt.ElideMiddle)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setAlternatingRowColors(True)
        for i, (a, b) in enumerate(rows):
            lab = QTableWidgetItem(a)
            lab.setToolTip(a)
            t.setItem(i, 0, lab)
            it = QTableWidgetItem(b)
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            t.setItem(i, 1, it)
        t.resizeRowsToContents()
        h = t.horizontalHeader().height() + sum(t.rowHeight(i) for i in range(len(rows))) + 4
        t.setMinimumHeight(min(h, 520))
        t.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        return t

    def _notice(self, lay, text: str, color: str):
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lab.setStyleSheet(f"QLabel {{ background: {color}22; border-left: 3px solid {color}; padding: 6px; }}")
        lay.addWidget(lab)

    # -- pages ---------------------------------------------------------------------------
    def show_none(self):
        self.current = None
        lay = self._new_page()
        self._header(lay, "Properties", "")
        tips = QLabel(
            "<b>Getting started</b><ul style='margin-left:-20px'>"
            "<li>Drag blocks from the palette onto the canvas (or double-click them).</li>"
            "<li>Drag from a red outlet port to a blue inlet port to create a stream.</li>"
            "<li>Select a block to set its specifications here.</li>"
            "<li>Press <b>Run</b> (F5) to solve. Hover over blocks and streams for results.</li>"
            "<li>Recycle loops are detected automatically. For a closed cycle, select one stream and "
            "give it an estimate.</li></ul>"
            "<b>Canvas</b><ul style='margin-left:-20px'><li>Ctrl/⌘ + scroll to zoom, middle-drag or "
            "Alt-drag to pan.</li><li>Delete/Backspace removes the selection; right-click for rename, "
            "flip and tear options.</li></ul>")
        tips.setWordWrap(True)
        tips.setTextFormat(Qt.RichText)
        lay.addWidget(tips)
        lay.addStretch()

    def show_block(self, b: UnitOp):
        self.current = ("block", b.uid)
        lay = self._new_page()
        self._header(lay, b.name, f"{b.LABEL}. {b.DESCRIPTION}", b.TYPE)

        box = QGroupBox("Specifications")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        name = QLineEdit(b.name)
        name.editingFinished.connect(lambda: self._rename_block(b, name.text()))
        form.addRow("Name", name)
        for p in b.visible_params():
            if p.kind == "text":           # multi-line editors get full-width rows
                form.addRow(QLabel(p.label + ":"))
                for wdg in self._text_rows(b, p, b.values[p.key]):
                    form.addRow(wdg)
            else:
                form.addRow(p.label, self._param_widget(b, p))
        lay.addWidget(box)

        if b.error:
            self._notice(lay, f"<b>Error:</b> {b.error}", "#d63939")
        for w in b.warnings:
            self._notice(lay, f"<b>Warning:</b> {w}", "#db9b17")
        if b.results:
            box2 = QGroupBox("Results")
            v = QVBoxLayout(box2)
            rows = [(r.label, fmt_result(r, self.units)) for r in b.results]
            v.addWidget(self._results_table(rows))
            lay.addWidget(box2)
            if self.fs is not None and not self.fs.solved:
                stale = QLabel("Results are from the last run and may be out of date.")
                stale.setEnabled(False)
                stale.setWordWrap(True)
                lay.addWidget(stale)
        lay.addStretch()

    def _param_widget(self, b: UnitOp, p):
        val = b.values[p.key]
        if p.kind == "choice":
            w = QComboBox()
            w.addItems(list(p.choices))
            w.setCurrentText(str(val))
            w.currentTextChanged.connect(lambda t, k=p.key: self._set(b, k, t, rebuild=True))
            return w
        if p.kind == "int":
            w = QSpinBox()
            w.setRange(int(p.minv if p.minv is not None else 0), int(p.maxv if p.maxv is not None else 10_000))
            w.setValue(int(val))
            w.setToolTip(p.tip)
            w.valueChanged.connect(lambda v, k=p.key, rp=p.rebuild_ports: self._set(b, k, v, rebuild=rp, ports=rp))
            return w
        if p.kind == "fluid":
            w = FluidEdit(str(val))
            w.setToolTip(p.tip)
            w.changed.connect(lambda t, k=p.key: self._set_fluid(b, k, t))
            return w
        if p.kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(val))
            w.toggled.connect(lambda v, k=p.key: self._set(b, k, v))
            return w
        w = NumberEdit(float(val), p.qty, self.units, p.tip)
        w.committed.connect(lambda v, k=p.key: self._set(b, k, v))
        return w

    def _text_rows(self, b: UnitOp, p, val):
        """Multi-line editor (used for reaction lists): editor, help text and an Apply button."""
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QPlainTextEdit, QPushButton
        ed = QPlainTextEdit(str(val))
        ed.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        ed.setFixedHeight(120)
        ed.setLineWrapMode(QPlainTextEdit.NoWrap)
        ed.setToolTip(p.tip)
        help_ = QLabel("One reaction per line, e.g.<br><code>CH4 + 2 O2 -&gt; CO2 + 2 H2O ; X=0.98 ; key=CH4</code>"
                       "<br>X = conversion of the key reactant (default 1). Applied in order.")
        help_.setWordWrap(True)
        help_.setEnabled(False)
        btn = QPushButton("Apply reactions")
        btn.clicked.connect(lambda: self._apply_text(b, p.key, ed.toPlainText()))
        return [ed, help_, btn]

    def _apply_text(self, b: UnitOp, key: str, text: str):
        if b.TYPE == "reactor":
            from ..engine.unitops import UnitOpError, parse_reactions
            try:
                rx = parse_reactions(text)
            except UnitOpError as exc:
                self.message.emit(str(exc))
                return
            self.message.emit(f"{len(rx)} reaction(s) set.")
        self._set(b, key, text)

    def _set(self, b: UnitOp, key: str, value, rebuild=False, ports=False):
        try:
            b.set_value(key, value)
        except (TypeError, ValueError) as exc:
            self.message.emit(str(exc))
            return
        self.blockChanged.emit(b.uid, ports)
        if rebuild:
            self.show_block(b)

    def _set_fluid(self, b: UnitOp, key: str, text: str):
        try:
            f = Fluid.parse(text)
        except ThermoError as exc:
            self.message.emit(str(exc))
            return
        b.values[key] = f.spec()
        self.blockChanged.emit(b.uid, False)

    def _rename_block(self, b: UnitOp, name: str):
        name = name.strip()
        if name and name != b.name:
            b.name = name
            self.blockChanged.emit(b.uid, False)

    def show_stream(self, s: Stream):
        self.current = ("stream", s.uid)
        lay = self._new_page()
        fs = self.fs
        src = fs.blocks[s.src] if fs else None
        dst = fs.blocks[s.dst] if fs else None
        sub = ""
        if src and dst:
            sp = dict(src.outlet_ports())[s.src_port]
            dp = dict(dst.inlet_ports())[s.dst_port]
            sub = f"From {src.name} ({sp.lower()}) to {dst.name} ({dp.lower()})"
        self._header(lay, f"Stream {s.name}", sub)

        box = QGroupBox("Stream")
        form = QFormLayout(box)
        name = QLineEdit(s.name)
        name.editingFinished.connect(lambda: self._rename_stream(s, name.text()))
        form.addRow("Name", name)
        tear = QCheckBox("Use as tear stream in recycle loops")
        tear.setChecked(s.tear)
        tear.toggled.connect(lambda v: (setattr(s, "tear", v), self.streamChanged.emit(s.uid)))
        form.addRow(tear)
        lay.addWidget(box)

        est = QGroupBox("Initial estimate (for recycle loops)")
        est.setCheckable(True)
        est.setChecked(bool(s.estimate.get("enabled")))
        ef = QFormLayout(est)
        fl = FluidEdit(s.estimate.get("fluid", ""))
        fl.setToolTip("Leave empty to use the fluid of the upstream feed")
        fl.changed.connect(lambda t: self._set_est(s, "fluid", t))
        ef.addRow("Fluid", fl)
        for key, label, qty in (("T", "Temperature", "T"), ("P", "Pressure", "P"), ("m", "Mass flow", "m")):
            ne = NumberEdit(float(s.estimate.get(key, 0.0)), qty, self.units)
            ne.committed.connect(lambda v, k=key: self._set_est(s, k, v))
            ef.addRow(label, ne)
        est.toggled.connect(lambda v: self._set_est(s, "enabled", v))
        hint = QLabel("Needed when the stream closes a loop with no feed (a closed cycle), "
                      "and helpful for hard-to-converge recycles.")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        ef.addRow(hint)
        lay.addWidget(est)

        if s.material is not None:
            box2 = QGroupBox("Conditions")
            v = QVBoxLayout(box2)
            v.addWidget(self._results_table(stream_rows(s.material, self.units)))
            lay.addWidget(box2)
        lay.addStretch()

    def _set_est(self, s: Stream, key: str, value):
        if key == "fluid" and value:
            try:
                value = Fluid.parse(value).spec()
            except ThermoError as exc:
                self.message.emit(str(exc))
                return
        s.estimate[key] = value
        self.streamChanged.emit(s.uid)

    def _rename_stream(self, s: Stream, name: str):
        name = name.strip()
        if name and name != s.name:
            s.name = name
            self.streamChanged.emit(s.uid)

    def refresh(self):
        """Rebuild the current page (after a solve or a units change)."""
        if self.current is None or self.fs is None:
            self.show_none()
            return
        kind, uid = self.current
        if kind == "block" and uid in self.fs.blocks:
            self.show_block(self.fs.blocks[uid])
        elif kind == "stream" and uid in self.fs.streams:
            self.show_stream(self.fs.streams[uid])
        else:
            self.show_none()
