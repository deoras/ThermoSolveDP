"""Small dialogs: mixture editor, units, solver settings, and the fluid entry widget."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QTableWidget, QToolButton, QVBoxLayout, QWidget)

from ..engine.thermo import Fluid, ThermoError, available_fluids
from ..engine.units import M_UNITS, P_UNITS, POWER_UNITS, T_UNITS, UnitSystem

_FLUIDS: list[str] | None = None


def fluid_names() -> list[str]:
    global _FLUIDS
    if _FLUIDS is None:
        _FLUIDS = available_fluids()
    return _FLUIDS


class FluidEdit(QWidget):
    """Editable fluid combo with completion plus a mixture editor button."""
    changed = Signal(str)

    def __init__(self, value: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.addItems(fluid_names())
        self.combo.setInsertPolicy(QComboBox.NoInsert)
        comp = QCompleter(fluid_names(), self.combo)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.combo.setCompleter(comp)
        self.combo.setCurrentText(value)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(10)
        btn = QToolButton()
        btn.setText("Mixture…")
        btn.setToolTip("Define a mixture with mole fractions")
        btn.clicked.connect(self._edit_mixture)
        lay.addWidget(self.combo, 1)
        lay.addWidget(btn)
        self.combo.lineEdit().editingFinished.connect(self._emit)
        self.combo.activated.connect(lambda _i: self._emit())
        self._last = value

    def value(self) -> str:
        return self.combo.currentText().strip()

    def _emit(self):
        v = self.value()
        if v != self._last:
            self._last = v
            self.changed.emit(v)

    def _edit_mixture(self):
        dlg = MixtureDialog(self.value(), self)
        if dlg.exec() == QDialog.Accepted:
            self.combo.setCurrentText(dlg.spec())
            self._emit()


class MixtureDialog(QDialog):
    def __init__(self, spec: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mixture composition")
        self.resize(420, 360)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Mole fractions are normalized automatically. CoolProp needs binary interaction "
                             "data for each pair; common hydrocarbons, refrigerants and air components work."))
        lay.itemAt(0).widget().setWordWrap(True)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Component", "Mole fraction"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("Add component")
        rem = QPushButton("Remove selected")
        add.clicked.connect(lambda: self._add_row("", 0.0))
        rem.clicked.connect(self._remove)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        try:
            f = Fluid.parse(spec)
            for c, x in zip(f.components, f.x):
                self._add_row(c, x)
        except ThermoError:
            self._add_row("Methane", 0.9)
            self._add_row("Ethane", 0.1)

    def _add_row(self, name: str, frac: float):
        r = self.table.rowCount()
        self.table.insertRow(r)
        cb = QComboBox()
        cb.setEditable(True)
        cb.addItems(fluid_names())
        cb.setCurrentText(name)
        sp = QDoubleSpinBox()
        sp.setDecimals(6)
        sp.setRange(0.0, 1e6)
        sp.setSingleStep(0.01)
        sp.setValue(frac)
        self.table.setCellWidget(r, 0, cb)
        self.table.setCellWidget(r, 1, sp)

    def _remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows and self.table.rowCount():
            rows = [self.table.rowCount() - 1]
        for r in rows:
            self.table.removeRow(r)

    def spec(self) -> str:
        parts = []
        for r in range(self.table.rowCount()):
            name = self.table.cellWidget(r, 0).currentText().strip()
            x = self.table.cellWidget(r, 1).value()
            if name and x > 0:
                parts.append(f"{name}[{x:.6g}]")
        return "&".join(parts)

    def _accept(self):
        try:
            Fluid.parse(self.spec())
        except ThermoError as exc:
            QMessageBox.warning(self, "Mixture", str(exc))
            return
        self.accept()


class UnitsDialog(QDialog):
    def __init__(self, units: UnitSystem, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Display units")
        form = QFormLayout(self)
        self.T = QComboBox(); self.T.addItems(T_UNITS); self.T.setCurrentText(units.T)
        self.P = QComboBox(); self.P.addItems(P_UNITS); self.P.setCurrentText(units.P)
        self.m = QComboBox(); self.m.addItems(M_UNITS); self.m.setCurrentText(units.m)
        self.W = QComboBox(); self.W.addItems(POWER_UNITS); self.W.setCurrentText(units.power)
        form.addRow("Temperature", self.T)
        form.addRow("Pressure", self.P)
        form.addRow("Mass flow", self.m)
        form.addRow("Power / duty", self.W)
        note = QLabel("Enthalpy is shown in kJ/kg and entropy in kJ/kg·K. Values are stored in SI, so "
                      "changing units never changes the model.")
        note.setWordWrap(True)
        form.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def units(self) -> UnitSystem:
        return UnitSystem(T=self.T.currentText(), P=self.P.currentText(), m=self.m.currentText(),
                          power=self.W.currentText())


class SolverDialog(QDialog):
    METHODS = ("Broyden", "Wegstein", "Direct substitution")

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Thermosolve1.0 solver settings")
        form = QFormLayout(self)
        self.method = QComboBox()
        self.method.addItems(self.METHODS)
        self.method.setCurrentText(settings.get("method", "Broyden"))
        self.max_iter = QSpinBox()
        self.max_iter.setRange(1, 10000)
        self.max_iter.setValue(int(settings.get("max_iter", 200)))
        self.tol = QLineEdit(f"{float(settings.get('tol', 1e-6)):g}")
        form.addRow("Recycle convergence method", self.method)
        form.addRow("Maximum iterations", self.max_iter)
        form.addRow("Tolerance (relative)", self.tol)
        note = QLabel("Broyden updates all tear streams together and handles strongly coupled loops "
                      "(e.g. recuperated cycles). Wegstein accelerates each variable separately. "
                      "Direct substitution is the most conservative.")
        note.setWordWrap(True)
        form.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _accept(self):
        try:
            t = float(self.tol.text())
            if not 0 < t < 1:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Solver settings", "Tolerance must be a number between 0 and 1.")
            return
        self.accept()

    def settings(self) -> dict:
        return {"method": self.method.currentText(), "max_iter": self.max_iter.value(),
                "tol": float(self.tol.text())}


class FluidPackageDialog(QDialog):
    """Choose the equation of state and review/edit binary interaction parameters (k_ij)."""

    SOURCE_TEXT = {
        "user": "User value",
        "fitted": "Fitted to CoolProp reference mixture",
        "estimated": "No data – assumed 0",
    }

    def __init__(self, settings: dict, components: list[str], parent=None):
        super().__init__(parent)
        from ..engine.thermo import METHODS
        self.setWindowTitle("Fluid package")
        self.resize(760, 560)
        self.components = components
        self.user = {k: float(v) for k, v in (settings.get("kij") or {}).items()}
        lay = QVBoxLayout(self)

        form = QFormLayout()
        self.method = QComboBox()
        for key, label in METHODS.items():
            self.method.addItem(label, key)
        self.method.setCurrentIndex(max(0, self.method.findData(settings.get("property_method", "HEOS"))))
        form.addRow("Property method", self.method)
        lay.addLayout(form)

        self.note = QLabel()
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Component i", "Component j", "k_ij", "Source"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.b_reset = QPushButton("Reset selected to automatic")
        self.b_import = QPushButton("Import CSV…")
        self.b_export = QPushButton("Export CSV…")
        for b in (self.b_reset, self.b_import, self.b_export):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.b_reset.clicked.connect(self._reset_selected)
        self.b_import.clicked.connect(self._import)
        self.b_export.clicked.connect(self._export)
        self.method.currentIndexChanged.connect(lambda _i: self._fill())
        self.table.itemChanged.connect(self._edited)
        self._filling = False
        self._fill()

    def _method(self) -> str:
        return self.method.currentData()

    def _pairs(self):
        c = self.components
        return [(c[i], c[j]) for i in range(len(c)) for j in range(i + 1, len(c))]

    def _fill(self):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QApplication, QTableWidgetItem
        from ..engine.thermo import PropertyPackage, has_reference_mixture_data, kij_for, pair_key

        method = self._method()
        cubic = method in ("PR", "SRK")
        for b in (self.b_reset, self.b_import, self.b_export):
            b.setEnabled(cubic)
        if not self.components:
            self.note.setText("Add feeds to the flowsheet first; the table lists every pair of components they "
                              "contain.")
        elif cubic:
            self.note.setText(
                "k_ij for pairs that CoolProp has reference mixture data for are fitted automatically to its "
                "bubble pressures. Pairs without data are assumed 0, which can be badly wrong for strongly "
                "non-ideal pairs (e.g. argon or R14 with HFCs). Type a value to override it with literature "
                "data. User values are saved in the flowsheet file.")
        else:
            self.note.setText(
                "The CoolProp reference model uses its own fitted mixture functions. It can only be used if every "
                "pair below has reference data. Otherwise choose Peng-Robinson or SRK.")
        self._filling = True
        self.table.setRowCount(0)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pkg = PropertyPackage(method, self.user) if cubic else None
            for a, b in self._pairs():
                r = self.table.rowCount()
                self.table.insertRow(r)
                for col, txt in ((0, a), (1, b)):
                    it = QTableWidgetItem(txt)
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    self.table.setItem(r, col, it)
                if cubic:
                    k, src = kij_for(a, b, pkg)
                    kv = QTableWidgetItem(f"{k:.5f}")
                    kv.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    st = QTableWidgetItem(self.SOURCE_TEXT[src])
                    if src == "estimated":
                        st.setForeground(QColor(200, 120, 10))
                else:
                    ok = has_reference_mixture_data(a, b)
                    kv = QTableWidgetItem("–")
                    kv.setFlags(kv.flags() & ~Qt.ItemIsEditable)
                    st = QTableWidgetItem("Reference data available" if ok else "Missing – mixture will fail")
                    if not ok:
                        st.setForeground(QColor(214, 57, 57))
                kv.setData(Qt.UserRole, pair_key(a, b))
                st.setFlags(st.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, 2, kv)
                self.table.setItem(r, 3, st)
        finally:
            QApplication.restoreOverrideCursor()
            self._filling = False
        self.table.resizeColumnsToContents()

    def _edited(self, item):
        if self._filling or item.column() != 2:
            return
        key = item.data(Qt.UserRole)
        try:
            v = float(item.text().replace(",", "."))
            if not -1.0 < v < 1.0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "k_ij", "k_ij must be a number between -1 and 1.")
            self._fill()
            return
        self.user[key] = v
        self._fill()

    def _reset_selected(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        for r in rows:
            self.user.pop(self.table.item(r, 2).data(Qt.UserRole), None)
        self._fill()

    def _import(self):
        import csv
        from PySide6.QtWidgets import QFileDialog
        from ..engine.thermo import ThermoError, canonical_name, pair_key
        fn, _ = QFileDialog.getOpenFileName(self, "Import k_ij", "", "CSV (*.csv);;All files (*)")
        if not fn:
            return
        n, bad = 0, []
        with open(fn, newline="", encoding="utf-8-sig") as f:
            for row in csv.reader(f):
                if len(row) < 3:
                    continue
                try:
                    a, b, k = canonical_name(row[0]), canonical_name(row[1]), float(row[2])
                except (ThermoError, ValueError):
                    if n or bad:      # tolerate a header line
                        bad.append(",".join(row))
                    continue
                self.user[pair_key(a, b)] = k
                n += 1
        msg = f"Imported {n} k_ij value(s)."
        if bad:
            msg += f"\nSkipped {len(bad)} line(s) that could not be read, e.g. {bad[0]}"
        QMessageBox.information(self, "Import k_ij", msg + "\n\nFormat: component1, component2, kij")
        self._fill()

    def _export(self):
        import csv
        from PySide6.QtWidgets import QFileDialog
        fn, _ = QFileDialog.getSaveFileName(self, "Export k_ij", "kij.csv", "CSV (*.csv)")
        if not fn:
            return
        with open(fn, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["component1", "component2", "kij", "source"])
            for r in range(self.table.rowCount()):
                w.writerow([self.table.item(r, c).text() for c in range(4)])

    def settings(self) -> dict:
        # user values for pairs not currently in the flowsheet are kept too (they may come back)
        return {"property_method": self._method(), "kij": dict(self.user)}
