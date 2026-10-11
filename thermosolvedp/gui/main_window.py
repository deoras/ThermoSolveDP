"""Main application window."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QDrag, QKeySequence, QTextCharFormat
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit, QTabWidget, QToolBar)

from .. import __version__
from ..engine.flowsheet import Flowsheet, FlowsheetError
from ..engine.solver import SOLVER_NAME, SolveReport, solve
from ..engine.unitops import BLOCK_TYPES
from ..engine.units import UnitSystem
from ..examples import EXAMPLES
from .canvas import MIME_BLOCK, BlockItem, FlowsheetScene, FlowsheetView, StreamItem
from .dialogs import FluidPackageDialog, SolverDialog, UnitsDialog
from .icons import app_icon, block_icon
from .plots import PlotWindow
from .property_panel import PropertyPanel
from .tables import StreamTable, SummaryTable

FILE_FILTER = "ThermoSolveDP flowsheet (*.tpf);;All files (*)"
PALETTE_ORDER = [
    ("Streams", ["feed", "product"]),
    ("Pressure change", ["compressor", "pump", "expander", "valve"]),
    ("Heat transfer", ["heater", "cooler", "hx"]),
    ("Separation", ["flash"]),
    ("Reaction", ["reactor"]),
    ("Mixing & splitting", ["mixer", "splitter"]),
]


class Palette(QListWidget):
    """Block palette; items can be dragged onto the canvas or double-clicked."""
    addRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setIconSize(QSize(30, 30))
        self.setSpacing(1)
        for cat, types in PALETTE_ORDER:
            head = QListWidgetItem(cat.upper())
            head.setFlags(Qt.NoItemFlags)
            f = head.font()
            f.setPointSizeF(f.pointSizeF() * 0.8)
            f.setBold(True)
            head.setFont(f)
            self.addItem(head)
            for t in types:
                cls = BLOCK_TYPES[t]
                it = QListWidgetItem(block_icon(t, 30), cls.LABEL)
                it.setData(Qt.UserRole, t)
                it.setToolTip(cls.DESCRIPTION)
                self.addItem(it)
        self.itemDoubleClicked.connect(lambda it: it.data(Qt.UserRole) and self.addRequested.emit(it.data(Qt.UserRole)))

    def refresh_icons(self):
        for i in range(self.count()):
            it = self.item(i)
            t = it.data(Qt.UserRole)
            if t:
                it.setIcon(block_icon(t, 30))

    def startDrag(self, actions):
        it = self.currentItem()
        if it is None or not it.data(Qt.UserRole):
            return
        md = QMimeData()
        md.setData(MIME_BLOCK, it.data(Qt.UserRole).encode())
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(it.icon().pixmap(36, 36))
        drag.exec(Qt.CopyAction)


class SolveWorker(QObject):
    log = Signal(str)
    done = Signal(object)

    def __init__(self, fs: Flowsheet, cancel: threading.Event):
        super().__init__()
        self.fs, self.cancel = fs, cancel

    def run(self):
        try:
            rep = solve(self.fs, log=self.log.emit, cancel=self.cancel)
        except Exception as exc:  # never let the worker die silently
            rep = SolveReport()
            rep.fail(f"Unexpected solver error: {exc!r}")
        self.done.emit(rep)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowIcon(app_icon())
        self.settings = QSettings("ThermoSolveDP", "ThermoSolveDP")
        self.units = UnitSystem.from_dict(self.settings.value("units", {}) or {})
        self.path: Path | None = None
        self.dirty = False
        self.report: SolveReport | None = None
        self._thread: QThread | None = None
        self._cancel = threading.Event()
        self.plot_window: PlotWindow | None = None

        self.scene = FlowsheetScene(self)
        self.scene.units = self.units
        self.view = FlowsheetView(self.scene)
        self.setCentralWidget(self.view)

        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self.status_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_label)

        self.scene.modelChanged.connect(self._on_model_changed)
        self.scene.layoutChanged.connect(self._on_layout_changed)
        self.scene.selectionChanged.connect(self._on_selection)
        self.scene.message.connect(lambda m: self.statusBar().showMessage(m, 6000))

        self.resize(1360, 860)
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self.new_flowsheet(ask=False)

    # ------------------------------------------------------------------------------ ui
    def _build_docks(self):
        self.palette = Palette()
        self.palette.addRequested.connect(self._add_block_center)
        d = QDockWidget("Blocks", self)
        d.setObjectName("palette")
        d.setWidget(self.palette)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.LeftDockWidgetArea, d)
        self.palette.setMinimumWidth(190)

        self.props = PropertyPanel()
        self.props.units = self.units
        self.props.blockChanged.connect(self._on_block_edited)
        self.props.streamChanged.connect(self._on_stream_edited)
        self.props.message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        d = QDockWidget("Properties", self)
        d.setObjectName("properties")
        d.setWidget(self.props)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.props.setMinimumWidth(340)
        self.addDockWidget(Qt.RightDockWidgetArea, d)

        self.bottom = QTabWidget()
        self.stream_table = StreamTable()
        self.summary_table = SummaryTable()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.bottom.addTab(self.stream_table, "Streams")
        self.bottom.addTab(self.summary_table, "Energy summary")
        self.bottom.addTab(self.log, "Messages")
        d = QDockWidget("Results", self)
        d.setObjectName("results")
        d.setWidget(self.bottom)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.BottomDockWidgetArea, d)
        self.resizeDocks([d], [250], Qt.Vertical)

    def _act(self, text, slot, shortcut=None, tip=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(shortcut)
        if tip:
            a.setStatusTip(tip)
            a.setToolTip(tip)
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        return a

    def _build_actions(self):
        self.a_new = self._act("&New", lambda: self.new_flowsheet(), QKeySequence.New)
        self.a_open = self._act("&Open…", self.open_file, QKeySequence.Open)
        self.a_save = self._act("&Save", self.save, QKeySequence.Save)
        self.a_save_as = self._act("Save &As…", self.save_as, QKeySequence.SaveAs)
        self.a_exp_streams = self._act("Stream table (CSV)…", lambda: self.export("streams"))
        self.a_exp_blocks = self._act("Block results (CSV)…", lambda: self.export("blocks"))
        self.a_exp_energy = self._act("Energy summary (CSV)…", lambda: self.export("energy"))
        self.a_exp_all = self._act("All three (CSV)…", lambda: self.export("all"), "Ctrl+Shift+E",
                                   "Save streams, block results and energy summary as three CSV files")
        self.a_quit = self._act("&Quit", self.close, QKeySequence.Quit)
        self.a_delete = self._act("&Delete", self.scene.delete_selected, QKeySequence.Delete)
        self.a_flip = self._act("&Flip horizontally", self.scene.flip_selected, "F",
                                "Mirror the selected blocks (useful for drawing recycles)")
        self.a_select_all = self._act("Select &all", self._select_all, QKeySequence.SelectAll)
        self.a_run = self._act("▶  Run", self.run, "F5", "Solve the flowsheet (F5)")
        self.a_stop = self._act("■  Stop", self.stop, None, "Stop the running solve")
        self.a_stop.setEnabled(False)
        self.a_solver = self._act("Solver settings (Thermosolve1.0)…", self.solver_settings)
        self.a_package = self._act("Fluid package…", self.fluid_package, None,
                                   "Property method (CoolProp reference, Peng-Robinson, SRK) and k_ij values")
        self.a_units = self._act("Units…", self.units_dialog, None, "Choose display units")
        self.a_cond = self._act("Show stream conditions on canvas", self._toggle_conditions, None, None, True)
        self.a_fit = self._act("Zoom to fit", self.view.zoom_fit, "Ctrl+0")
        self.a_zoom_in = self._act("Zoom in", lambda: self.view.zoom(1.2), QKeySequence.ZoomIn)
        self.a_zoom_out = self._act("Zoom out", lambda: self.view.zoom(1 / 1.2), QKeySequence.ZoomOut)
        self.a_plots = self._act("Diagrams…", self.show_plots, "Ctrl+D", "T–s, P–h and heat exchanger T–Q diagrams")
        self.a_about = self._act("About ThermoSolveDP", self.about)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_open):
            m.addAction(a)
        ex = m.addMenu("Open example")
        for title in EXAMPLES:
            ex.addAction(title, lambda t=title: self.open_example(t))
        m.addSeparator()
        for a in (self.a_save, self.a_save_as):
            m.addAction(a)
        exm = m.addMenu("Export results")
        for a in (self.a_exp_streams, self.a_exp_blocks, self.a_exp_energy):
            exm.addAction(a)
        exm.addSeparator()
        exm.addAction(self.a_exp_all)
        m.addSeparator()
        m.addAction(self.a_quit)
        m = mb.addMenu("&Edit")
        for a in (self.a_delete, self.a_flip, self.a_select_all):
            m.addAction(a)
        m = mb.addMenu("&Simulation")
        for a in (self.a_run, self.a_stop, self.a_package, self.a_solver):
            m.addAction(a)
        m = mb.addMenu("&View")
        for a in (self.a_plots, self.a_cond, self.a_units):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_fit, self.a_zoom_in, self.a_zoom_out):
            m.addAction(a)
        m = mb.addMenu("&Help")
        m.addAction(self.a_about)

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setObjectName("toolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        for a in (self.a_new, self.a_open, self.a_save):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_run)
        tb.addAction(self.a_stop)
        tb.addSeparator()
        tb.addAction(self.a_package)
        tb.addAction(self.a_plots)
        tb.addAction(self.a_units)
        tb.addAction(self.a_fit)
        self.addToolBar(tb)

    # ------------------------------------------------------------------------------ files
    def _title(self):
        name = self.path.name if self.path else self._untitled
        self.setWindowTitle(f"{name}{' •' if self.dirty else ''} — ThermoSolveDP")

    def _set_dirty(self, d=True):
        self.dirty = d
        self._title()

    def _maybe_save(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes to the current flowsheet?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def _load(self, fs: Flowsheet, path: Path | None, untitled="Untitled"):
        from ..engine.thermo import ThermoError, set_property_package
        try:
            set_property_package(fs.settings.get("property_method", "HEOS"), fs.settings.get("kij", {}))
        except ThermoError:
            fs.settings["property_method"] = "HEOS"
            set_property_package("HEOS")
        self.scene.load(fs)
        self.props.fs = fs
        self.path = path
        self._untitled = untitled
        self.report = None
        if fs.units:
            self._apply_units(UnitSystem.from_dict(fs.units), mark_dirty=False)
        self.props.show_none()
        self.stream_table.refresh(fs, self.units)
        self.summary_table.refresh(fs, None, self.units)
        self._set_dirty(False)
        self._update_status()
        QTimer.singleShot(0, self.view.zoom_fit)

    def new_flowsheet(self, ask=True):
        if ask and not self._maybe_save():
            return
        self._load(Flowsheet(), None)
        self.log.clear()

    def open_file(self):
        if not self._maybe_save():
            return
        start = str(self.path.parent) if self.path else self.settings.value("last_dir", str(Path.home()))
        fn, _ = QFileDialog.getOpenFileName(self, "Open flowsheet", start, FILE_FILTER)
        if fn:
            self.open_path(Path(fn))

    def open_path_from_os(self, path: Path):
        """Open a file handed over by the operating system (double-click in Finder/Explorer)."""
        if path.suffix.lower() != ".tpf" or not path.is_file():
            return
        if self._thread is not None or not self._maybe_save():
            return
        self.open_path(path)
        self.raise_()
        self.activateWindow()

    def open_path(self, path: Path):
        try:
            fs = Flowsheet.load(path)
        except (OSError, FlowsheetError, KeyError, ValueError) as exc:
            QMessageBox.critical(self, "Open", f"Could not open {path.name}:\n{exc}")
            return
        self.settings.setValue("last_dir", str(path.parent))
        self._load(fs, path)
        self._log(f"Opened {path}")

    def open_example(self, title: str):
        if not self._maybe_save():
            return
        fname, builder = EXAMPLES[title]
        self._load(builder(), None, untitled=f"Example – {title}")
        self.log.clear()
        self._log(f"Loaded example: {title}. Press Run (F5) to solve.")

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        return self._write(self.path)

    def save_as(self) -> bool:
        start = str(self.path) if self.path else str(Path(self.settings.value("last_dir", str(Path.home()))) /
                                                    "flowsheet.tpf")
        fn, _ = QFileDialog.getSaveFileName(self, "Save flowsheet", start, FILE_FILTER)
        if not fn:
            return False
        p = Path(fn)
        if p.suffix == "":
            p = p.with_suffix(".tpf")
        ok = self._write(p)
        if ok:
            self.path = p
            self.settings.setValue("last_dir", str(p.parent))
            self._title()
        return ok

    def _write(self, p: Path) -> bool:
        self.scene.fs.units = self.units.to_dict()
        try:
            self.scene.fs.save(p)
        except OSError as exc:
            QMessageBox.critical(self, "Save", f"Could not save:\n{exc}")
            return False
        self._set_dirty(False)
        self.statusBar().showMessage(f"Saved {p.name}", 4000)
        return True

    def export(self, what: str):
        """Export results as CSV: 'streams', 'blocks', 'energy' or 'all' (three files)."""
        from ..engine import export as ex
        fs = self.scene.fs
        if self.report is None or not any(st.material is not None for st in fs.streams.values()):
            QMessageBox.information(self, "Export results", "Run the simulation first (Run / F5).")
            return
        stem = self.path.stem if self.path else "thermosolve"
        folder = Path(self.settings.value("export_dir", str(self.path.parent if self.path else Path.home())))
        names = {"streams": f"{stem}_streams.csv", "blocks": f"{stem}_blocks.csv",
                 "energy": f"{stem}_energy.csv", "all": f"{stem}.csv"}
        titles = {"streams": "Export stream table", "blocks": "Export block results",
                  "energy": "Export energy summary",
                  "all": "Export all results (name is used as the prefix of three files)"}
        fn, _ = QFileDialog.getSaveFileName(self, titles[what], str(folder / names[what]), "CSV (*.csv)")
        if not fn:
            return
        p = Path(fn)
        if p.suffix.lower() != ".csv":
            p = p.with_suffix(".csv")
        try:
            if what == "streams":
                files = [ex.write_stream_table(fs, self.units, p)]
            elif what == "blocks":
                files = [ex.write_block_results(fs, self.units, p)]
            elif what == "energy":
                files = [ex.write_energy_summary(fs, self.report, self.units, p)]
            else:
                files = ex.write_all(fs, self.report, self.units, p)
        except OSError as exc:
            QMessageBox.critical(self, "Export results", f"Could not write the file:\n{exc}")
            return
        self.settings.setValue("export_dir", str(p.parent))
        note = "" if fs.solved else " (results are from the last run and may be out of date)"
        self._log("Exported " + ", ".join(f.name for f in files) + f" to {p.parent}{note}", level="ok")
        self.statusBar().showMessage(f"Exported {len(files)} file(s){note}.", 6000)

    # ------------------------------------------------------------------------------ editing
    def _add_block_center(self, type_name: str):
        c = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_block(type_name, c)

    def _select_all(self):
        for it in self.scene.items():
            if isinstance(it, (BlockItem, StreamItem)):
                it.setSelected(True)

    def _on_model_changed(self):
        self.scene.fs.solved = False
        self._set_dirty()
        self._update_status()
        self._on_selection()

    def _on_layout_changed(self):
        self._set_dirty()

    def _on_block_edited(self, uid: str, ports_changed: bool):
        if ports_changed:
            self.scene.rebuild_block(uid)
        item = self.scene.block_items.get(uid)
        if item:
            item.update_label()
        self.scene.fs.solved = False
        self._set_dirty()
        self._update_status()

    def _on_stream_edited(self, uid: str):
        item = self.scene.stream_items.get(uid)
        if item:
            item.refresh_label()
            item.update()
        self.scene.fs.solved = False
        self._set_dirty()
        self._update_status()

    def _on_selection(self):
        try:
            sel = self.scene.selectedItems()
        except RuntimeError:
            return
        if len(sel) == 1 and isinstance(sel[0], BlockItem):
            self.props.show_block(sel[0].block)
        elif len(sel) == 1 and isinstance(sel[0], StreamItem):
            self.props.show_stream(sel[0].stream)
        elif not sel:
            self.props.show_none()

    def _update_status(self):
        fs = self.scene.fs
        n_b, n_s = len(fs.blocks), len(fs.streams)
        if self._thread is not None:
            state = "Solving…"
        elif fs.solved:
            state = "Solved"
        elif self.report is not None:
            state = "Results out of date — press Run" if not self.report.error else "Not solved"
        else:
            state = "Not solved"
        method = {"HEOS": "CoolProp reference", "PR": "Peng-Robinson", "SRK": "SRK"}.get(
            fs.settings.get("property_method", "HEOS"), "")
        self.status_label.setText(f"{n_b} blocks · {n_s} streams · {method} · {state}")

    # ------------------------------------------------------------------------------ solving
    def run(self):
        if self._thread is not None:
            return
        self.setFocus()   # commit any field being edited
        self._log(f"— {SOLVER_NAME} solving —", bold=True)
        self._cancel.clear()
        self._thread = QThread(self)
        self._worker = SolveWorker(self.scene.fs, self._cancel)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._log)
        self._worker.done.connect(self._solved)
        self._thread.finished.connect(self._worker.deleteLater)
        self._set_busy(True)
        self._thread.start()

    def stop(self):
        self._cancel.set()

    def _set_busy(self, busy: bool):
        self.a_run.setEnabled(not busy)
        self.a_stop.setEnabled(busy)
        self.view.setInteractive(not busy)
        self.props.setEnabled(not busy)
        self.palette.setEnabled(not busy)
        for a in (self.a_new, self.a_open, self.a_delete, self.a_flip):
            a.setEnabled(not busy)
        if busy:
            QApplication.setOverrideCursor(Qt.BusyCursor)
        else:
            QApplication.restoreOverrideCursor()
        self._update_status()

    def _solved(self, rep: SolveReport):
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self.report = rep
        self._set_busy(False)
        for lvl, msg in rep.messages:
            self._log(msg, level=lvl)
        if rep.converged:
            self._log(f"Solved by {SOLVER_NAME} in {rep.elapsed:.2f} s.", level="ok")
        self.scene.refresh_results()
        self.props.refresh()
        self.stream_table.refresh(self.scene.fs, self.units)
        self.summary_table.refresh(self.scene.fs, rep, self.units)
        if rep.error:
            self.bottom.setCurrentWidget(self.log)
            if rep.error_block and rep.error_block in self.scene.block_items:
                self.scene.clearSelection()
                self.scene.block_items[rep.error_block].setSelected(True)
            self.statusBar().showMessage(rep.error, 10000)
        else:
            self.bottom.setCurrentWidget(self.stream_table)
            warn = sum(1 for lvl, _ in rep.messages if lvl == "warning")
            self.statusBar().showMessage(f"Solved{f' with {warn} warning(s)' if warn else ''}.", 6000)
        if self.plot_window is not None and self.plot_window.isVisible():
            self.plot_window.set_data(self.scene.fs, self.units)
        self._update_status()

    def _log(self, text: str, level: str = "info", bold=False):
        colors = {"error": QColor(214, 57, 57), "warning": QColor(200, 140, 10), "ok": QColor(46, 160, 67)}
        fmt = QTextCharFormat()
        if level in colors:
            fmt.setForeground(colors[level])
        if bold:
            fmt.setFontWeight(700)
        cur = self.log.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        prefix = {"error": "Error: ", "warning": "Warning: "}.get(level, "")
        cur.insertText(prefix + text + "\n", fmt)
        self.log.setTextCursor(cur)
        self.log.ensureCursorVisible()

    # ------------------------------------------------------------------------------ settings & views
    def solver_settings(self):
        dlg = SolverDialog(self.scene.fs.settings, self)
        if dlg.exec():
            self.scene.fs.settings.update(dlg.settings())
            self._set_dirty()

    def fluid_package(self):
        from ..engine.solver import flowsheet_components
        from ..engine.thermo import set_property_package
        fs = self.scene.fs
        dlg = FluidPackageDialog(fs.settings, [], self)
        # components depend on the method (Air is split into N2/O2/Ar for cubic equations)
        def comps():
            set_property_package(dlg._method(), dlg.user)
            return flowsheet_components(fs)
        dlg.components = comps()
        dlg.method.currentIndexChanged.connect(lambda _i: (setattr(dlg, "components", comps()), dlg._fill()))
        dlg._fill()
        ok = dlg.exec()
        new = dlg.settings() if ok else {"property_method": fs.settings.get("property_method", "HEOS"),
                                          "kij": fs.settings.get("kij", {})}
        set_property_package(new["property_method"], new["kij"])
        if ok and (new["property_method"] != fs.settings.get("property_method", "HEOS")
                   or new["kij"] != fs.settings.get("kij", {})):
            fs.settings.update(new)
            fs.solved = False
            self._set_dirty()
            self._update_status()
            n_user = len(new["kij"])
            extra = f" with {n_user} user k_ij value(s)" if n_user else ""
            self._log(f"Fluid package: {dlg.method.currentText()}{extra}. Press Run to recalculate.")

    def units_dialog(self):
        dlg = UnitsDialog(self.units, self)
        if dlg.exec():
            self._apply_units(dlg.units())

    def _apply_units(self, u: UnitSystem, mark_dirty=True):
        self.units = u
        self.settings.setValue("units", u.to_dict())
        self.scene.fs.units = u.to_dict()
        self.scene.set_units(u)
        self.props.units = u
        self.props.refresh()
        self.stream_table.refresh(self.scene.fs, u)
        self.summary_table.refresh(self.scene.fs, self.report, u)
        if self.plot_window is not None and self.plot_window.isVisible():
            self.plot_window.set_data(self.scene.fs, u)
        if mark_dirty:
            self._set_dirty()

    def _toggle_conditions(self):
        self.scene.set_show_conditions(self.a_cond.isChecked())

    def show_plots(self):
        if self.plot_window is None:
            self.plot_window = PlotWindow(self)
        self.plot_window.set_data(self.scene.fs, self.units)
        self.plot_window.show()
        self.plot_window.raise_()

    def about(self):
        import CoolProp
        QMessageBox.about(self, "About ThermoSolveDP",
                          f"<b>ThermoSolveDP {__version__}</b><br>Steady-state thermodynamic process simulator."
                          f"<br>Created by Deoras Prabhudharwadkar"
                          f"<br><br>Properties: CoolProp {CoolProp.__version__} (HEOS backend)<br>"
                          f"Solver engine: {SOLVER_NAME}<br>GUI: Qt for Python (PySide6, LGPL v3)"
                          f"<br><br>Released under the MIT License. "
                          f"See LICENSE and THIRD_PARTY_NOTICES.txt.")

    def changeEvent(self, e):
        if e.type() in (e.Type.PaletteChange, e.Type.ApplicationPaletteChange):
            QTimer.singleShot(0, self._retheme)
        super().changeEvent(e)

    def _retheme(self):
        self.scene.refresh_theme()
        self.palette.refresh_icons()
        self.props.refresh()

    def closeEvent(self, e):
        if self._thread is not None:
            self._cancel.set()
            self._thread.wait(5000)
        if not self._maybe_save():
            e.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        e.accept()
