"""Bottom-panel tables: stream table and energy summary."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import QApplication, QHeaderView, QTableWidget, QTableWidgetItem

from ..engine.flowsheet import Flowsheet
from ..engine.solver import SolveReport
from ..engine.units import UnitSystem
from .property_panel import fmt_result, stream_rows


class CopyTable(QTableWidget):
    """Table where Ctrl/Cmd+C copies the whole selection (tab-separated, pastes into Excel/Numbers)."""

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Copy):
            self.copy_selection()
            e.accept()
            return
        super().keyPressEvent(e)

    def copy_selection(self):
        ranges = self.selectedRanges()
        if not ranges:
            return
        r0 = min(r.topRow() for r in ranges)
        r1 = max(r.bottomRow() for r in ranges)
        c0 = min(r.leftColumn() for r in ranges)
        c1 = max(r.rightColumn() for r in ranges)
        selected = {(i.row(), i.column()) for i in self.selectedIndexes()}
        whole = len(selected) == self.rowCount() * self.columnCount()
        lines = []
        if whole or c0 == 0:      # include headers when copying everything / full rows
            hdr = [self.horizontalHeaderItem(c).text() if self.horizontalHeaderItem(c) else ""
                   for c in range(c0, c1 + 1)]
            if self.verticalHeader().isVisible():
                hdr = [""] + hdr
            lines.append("\t".join(hdr))
        for r in range(r0, r1 + 1):
            row = []
            if self.verticalHeader().isVisible():
                vh = self.verticalHeaderItem(r)
                row.append(vh.text() if vh else "")
            for c in range(c0, c1 + 1):
                it = self.item(r, c)
                row.append(it.text() if (it and (r, c) in selected) else "")
            lines.append("\t".join(row))
        QApplication.clipboard().setText("\n".join(lines))


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


class StreamTable(CopyTable):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._data: list[list[str]] = []

    def refresh(self, fs: Flowsheet, u: UnitSystem):
        streams = sorted(fs.streams.values(), key=lambda s: natural_key(s.name))
        streams = [s for s in streams if s.material is not None]
        cols = [s.name for s in streams]
        row_labels: list[str] = ["From", "To"]
        values: dict[tuple[str, str], str] = {}
        for s in streams:
            values[("From", s.name)] = fs.blocks[s.src].name
            values[("To", s.name)] = fs.blocks[s.dst].name
            for lab, val in stream_rows(s.material, u):
                if lab not in row_labels:
                    row_labels.append(lab)
                values[(lab, s.name)] = val
        self.clear()
        self.setColumnCount(len(cols))
        self.setRowCount(len(row_labels))
        self.setHorizontalHeaderLabels(cols)
        self.setVerticalHeaderLabels(row_labels)
        bold = QFont()
        bold.setBold(True)
        self._data = [[""] + cols]
        for r, lab in enumerate(row_labels):
            line = [lab]
            for c, name in enumerate(cols):
                v = values.get((lab, name), "")
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.setItem(r, c, it)
                line.append(v)
            self._data.append(line)



class SummaryTable(CopyTable):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()

    def refresh(self, fs: Flowsheet, rep: SolveReport | None, u: UnitSystem):
        rows: list[tuple[str, str, str, str]] = []
        for b in fs.blocks.values():
            w, q = b.energy["work"], b.energy["heat"]
            w = w if abs(w) > 1e-3 else 0.0        # hide round-off (e.g. adiabatic flash)
            q = q if abs(q) > 1e-3 else 0.0
            if w or q:
                rows.append((b.name, b.LABEL,
                             f"{u.fmt('power', w)}" if w else "", f"{u.fmt('power', q)}" if q else ""))
        self.clear()
        self.setColumnCount(4)
        hdr = ["Block", "Type", f"Work into fluid [{u.label('power')}]", f"Heat into fluid [{u.label('power')}]"]
        self.setHorizontalHeaderLabels(hdr)
        summary = rep.summary if rep else []
        self.setRowCount(len(rows) + (len(summary) + 1 if summary else 0))
        r = 0
        for row in rows:
            for c, v in enumerate(row):
                it = QTableWidgetItem(v)
                if c >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.setItem(r, c, it)
            r += 1
        if summary:
            bold = QFont()
            bold.setBold(True)
            it = QTableWidgetItem("Flowsheet totals")
            it.setFont(bold)
            self.setItem(r, 0, it)
            r += 1
            for res in summary:
                self.setItem(r, 0, QTableWidgetItem(res.label))
                self.setSpan(r, 0, 1, 2)
                it = QTableWidgetItem(fmt_result(res, u))
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.setItem(r, 2, it)
                r += 1
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
