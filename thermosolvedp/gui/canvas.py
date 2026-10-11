"""Flowsheet canvas: blocks, ports and streams on a QGraphicsScene."""
from __future__ import annotations

import math

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPalette,
                           QPen, QPolygonF, QTransform)
from PySide6.QtWidgets import (QApplication, QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem,
                               QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QInputDialog, QMenu)

from ..engine.flowsheet import Flowsheet, FlowsheetError, Stream
from ..engine.unitops import BLOCK_TYPES, UnitOp
from ..engine.units import UnitSystem
from .geometry import block_size, port_geometry
from .icons import COLD, HOT, draw_icon, is_dark

MIME_BLOCK = "application/x-thermosolvedp-block"
GRID = 10

STATUS_COLORS = {"ok": QColor(46, 160, 67), "warning": QColor(219, 155, 23), "error": QColor(214, 57, 57)}
PHASE_COLORS = {
    "liquid": COLD, "saturated liquid": COLD,
    "two-phase": QColor(140, 90, 200),
    "vapor": HOT, "saturated vapor": HOT, "supercritical gas": HOT, "supercritical": QColor(200, 110, 40),
    "supercritical liquid": QColor(60, 150, 170),
}


def text_color() -> QColor:
    return QApplication.palette().color(QPalette.Text)


def muted_color() -> QColor:
    c = QApplication.palette().color(QPalette.Text)
    c.setAlpha(150)
    return c


# =====================================================================================
# Ports
# =====================================================================================

class PortItem(QGraphicsEllipseItem):
    R = 4.5

    def __init__(self, block_item: "BlockItem", port_id: str, label: str, is_inlet: bool, x, y, dx, dy):
        super().__init__(-self.R, -self.R, 2 * self.R, 2 * self.R, block_item)
        self.block_item = block_item
        self.port_id = port_id
        self.label = label
        self.is_inlet = is_inlet
        self.direction = QPointF(dx, dy)
        self.setPos(x, y)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CrossCursor)
        self.setZValue(2)
        self.setToolTip(f"{label} ({'inlet' if is_inlet else 'outlet'}) — drag to connect")
        self._hover = False
        self.refresh()

    def refresh(self):
        fs = self.block_item.scene_fs()
        connected = fs is not None and (
            fs.stream_to(self.block_item.block.uid, self.port_id) if self.is_inlet
            else fs.stream_from(self.block_item.block.uid, self.port_id)) is not None
        stroke = QColor(52, 120, 200) if self.is_inlet else QColor(214, 69, 65)
        self.setPen(QPen(stroke, 1.6))
        if connected:
            self.setBrush(QBrush(stroke))
        else:
            self.setBrush(QApplication.palette().color(QPalette.Base))
        r = self.R * (1.5 if self._hover else 1.0)
        self.setRect(-r, -r, 2 * r, 2 * r)

    def hoverEnterEvent(self, e):
        self._hover = True
        self.refresh()
        super().hoverEnterEvent(e)

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.refresh()
        super().hoverLeaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.scene().start_connection(self, e.scenePos())
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self.scene().update_connection(e.scenePos())

    def mouseReleaseEvent(self, e):
        self.scene().finish_connection(e.scenePos())


# =====================================================================================
# Blocks
# =====================================================================================

class BlockItem(QGraphicsItem):
    def __init__(self, block: UnitOp):
        super().__init__()
        self.block = block
        self.ports: dict[str, PortItem] = {}
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(1)
        self.label = QGraphicsSimpleTextItem(self)
        f = QFont()
        f.setPointSizeF(f.pointSizeF() * 0.95)
        self.label.setFont(f)
        self._w, self._h = block_size(block)
        self.setPos(block.layout.get("x", 0.0), block.layout.get("y", 0.0))
        self.rebuild()

    def scene_fs(self):
        sc = self.scene()
        return sc.fs if sc is not None else None

    def rebuild(self):
        """Recreate ports and label (after a port-count change or rename)."""
        self.prepareGeometryChange()
        self._w, self._h = block_size(self.block)
        for p in self.ports.values():
            p.setParentItem(None)
            if p.scene():
                p.scene().removeItem(p)
        self.ports = {}
        geo = port_geometry(self.block)
        for pid, label in self.block.inlet_ports():
            self.ports[pid] = PortItem(self, pid, label, True, *geo[pid])
        for pid, label in self.block.outlet_ports():
            self.ports[pid] = PortItem(self, pid, label, False, *geo[pid])
        self.update_label()
        self.update()

    def update_label(self):
        self.label.setText(self.block.name)
        self.label.setBrush(text_color())
        br = self.label.boundingRect()
        below = self._h + 4
        if self.block.TYPE == "flash":
            below = self._h + 12          # leave room for the liquid line
        self.label.setPos((self._w - br.width()) / 2, below)

    def refresh_ports(self):
        for p in self.ports.values():
            p.refresh()

    def boundingRect(self) -> QRectF:
        return QRectF(-8, -8, self._w + 16, self._h + 16)

    def paint(self, p: QPainter, option, widget=None):
        if self.isSelected():
            sel = QApplication.palette().color(QPalette.Highlight)
            p.setPen(QPen(sel, 1.4, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(-5, -5, self._w + 10, self._h + 10), 5, 5)
        p.save()
        if self.block.layout.get("flip"):
            p.translate(self._w, 0)
            p.scale(-1, 1)
        draw_icon(p, self.block.TYPE, self._w, self._h)
        p.restore()
        col = STATUS_COLORS.get(self.block.status)
        if col is not None:
            p.setPen(QPen(QApplication.palette().color(QPalette.Base), 1.2))
            p.setBrush(col)
            p.drawEllipse(QPointF(self._w + 1, -1), 4.5, 4.5)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            return QPointF(round(value.x() / GRID) * GRID, round(value.y() / GRID) * GRID)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.block.layout["x"], self.block.layout["y"] = self.pos().x(), self.pos().y()
            sc = self.scene()
            if sc is not None:
                sc.block_moved(self)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        sc = self.scene()
        if sc is not None and sc.moved_since_press:
            sc.moved_since_press = False
            sc.layoutChanged.emit()

    def tooltip_text(self, units: UnitSystem) -> str:
        b = self.block
        lines = [f"<b>{b.name}</b> — {b.LABEL}"]
        if b.error:
            lines.append(f"<span style='color:#d63939'>Error: {b.error}</span>")
        for w in b.warnings:
            lines.append(f"<span style='color:#db9b17'>Warning: {w}</span>")
        from .property_panel import fmt_result
        for r in b.results[:6]:
            lines.append(f"{r.label}: {fmt_result(r, units)}")
        return "<br>".join(lines)


# =====================================================================================
# Streams
# =====================================================================================

def _clear_below(x0: float, x1: float, rects: list[QRectF], floor: float) -> float:
    """A y coordinate below every obstacle that overlaps the horizontal span [x0, x1]."""
    lo, hi = min(x0, x1), max(x0, x1)
    y = floor
    for r in rects:
        if r.right() >= lo and r.left() <= hi:
            y = max(y, r.bottom())
    return y + 28


def _blocked(y: float, x0: float, x1: float, rects: list[QRectF]) -> bool:
    lo, hi = min(x0, x1), max(x0, x1)
    return any(r.top() < y < r.bottom() and r.right() > lo and r.left() < hi for r in rects)


def route(p1: QPointF, d1: QPointF, p2: QPointF, d2: QPointF, r1: QRectF, r2: QRectF,
          obstacles: list[QRectF] | None = None) -> list[QPointF]:
    """Orthogonal route from p1 (leaving in direction d1) to p2 (entering against d2).

    Backward and same-side connections are taken round underneath the blocks they would cross.
    """
    obstacles = obstacles or []
    stub = 14.0
    a = p1 + d1 * stub
    b = p2 + d2 * stub
    pts = [p1, a]
    h1, h2 = d1.y() == 0, d2.y() == 0
    if h1 and h2:
        if d1.x() * d2.x() < 0 and (b.x() - a.x()) * d1.x() >= 0:
            mx = (a.x() + b.x()) / 2
            pts += [QPointF(mx, a.y()), QPointF(mx, b.y())]
        elif d1.x() == d2.x():
            X = max(a.x(), b.x()) if d1.x() > 0 else min(a.x(), b.x())
            if not _blocked(a.y(), a.x(), X, obstacles) and not _blocked(b.y(), b.x(), X, obstacles):
                pts += [QPointF(X, a.y()), QPointF(X, b.y())]
            else:   # go underneath, then rise straight up to the destination port's stub
                y = _clear_below(a.x(), b.x(), obstacles + [r1, r2], max(r1.bottom(), r2.bottom()))
                pts += [QPointF(a.x(), y), QPointF(b.x(), y)]
        else:  # backward connection: go round underneath the blocks in between
            y = _clear_below(a.x(), b.x(), obstacles + [r1, r2], max(r1.bottom(), r2.bottom()))
            pts += [QPointF(a.x(), y), QPointF(b.x(), y)]
    elif not h1 and h2:      # leaves vertically (flash vapor/liquid), enters horizontally
        pts += [QPointF(b.x(), a.y())]
    elif h1 and not h2:      # leaves horizontally, enters vertically
        pts += [QPointF(b.x(), a.y())]
    else:
        my = (a.y() + b.y()) / 2
        pts += [QPointF(a.x(), my), QPointF(b.x(), my)]
    pts += [b, p2]
    clean = [pts[0]]
    for q in pts[1:]:
        if QLineF(clean[-1], q).length() > 0.01:
            clean.append(q)
    return clean


class StreamItem(QGraphicsPathItem):
    def __init__(self, stream: Stream, src: PortItem, dst: PortItem):
        super().__init__()
        self.stream = stream
        self.src, self.dst = src, dst
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(0)
        self.label = QGraphicsSimpleTextItem(self)
        self.label.setZValue(3)
        self.tag = QGraphicsSimpleTextItem(self)
        f = QFont()
        f.setPointSizeF(f.pointSizeF() * 0.8)
        self.tag.setFont(f)
        self._arrow = QPolygonF()
        self._hover = False
        self.show_conditions = False
        self.units = UnitSystem()
        self.update_path()

    def color(self) -> QColor:
        m = self.stream.material
        if m is None:
            return muted_color()
        return PHASE_COLORS.get(m.state.phase, muted_color())

    def update_path(self):
        p1 = self.src.scenePos()
        p2 = self.dst.scenePos()
        d1 = self._scene_dir(self.src)
        d2 = self._scene_dir(self.dst)
        r1 = self.src.block_item.sceneBoundingRect()
        r2 = self.dst.block_item.sceneBoundingRect()
        sc = self.scene()
        obstacles = []
        if sc is not None:
            # obstacles include each block's label, with a little clearance
            obstacles = [bi.mapRectToScene(bi.boundingRect().united(bi.childrenBoundingRect())).adjusted(-4, -4, 4, 4)
                         for bi in sc.block_items.values()
                         if bi is not self.src.block_item and bi is not self.dst.block_item]
        pts = route(p1, d1, p2, d2, r1, r2, obstacles)
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        self.prepareGeometryChange()
        self.setPath(path)
        # arrow head at the destination, pointing into the port
        tip = pts[-1]
        prev = pts[-2]
        ang = math.atan2(tip.y() - prev.y(), tip.x() - prev.x())
        L, W = 9.0, 4.0
        back = QPointF(tip.x() - L * math.cos(ang), tip.y() - L * math.sin(ang))
        perp = QPointF(-math.sin(ang) * W, math.cos(ang) * W)
        tip_adj = QPointF(tip.x() - PortItem.R * math.cos(ang), tip.y() - PortItem.R * math.sin(ang))
        back = QPointF(back.x() - PortItem.R * math.cos(ang), back.y() - PortItem.R * math.sin(ang))
        self._arrow = QPolygonF([tip_adj, back + perp, back - perp])
        # label on the longest segment
        segs = list(zip(pts[:-1], pts[1:]))
        a, b = max(segs, key=lambda s: QLineF(s[0], s[1]).length())
        mid = (a + b) / 2
        self._mid, self._horizontal = mid, abs(a.y() - b.y()) < 0.5
        self.refresh_label()

    @staticmethod
    def _scene_dir(port: PortItem) -> QPointF:
        return port.direction

    def refresh_label(self):
        self.label.setText(self.stream.name)
        self.label.setBrush(text_color())
        br = self.label.boundingRect()
        mid = self._mid
        if self._horizontal:
            self.label.setPos(mid.x() - br.width() / 2, mid.y() - br.height() - 2)
        else:
            self.label.setPos(mid.x() + 4, mid.y() - br.height() / 2)
        m = self.stream.material
        if self.show_conditions and m is not None:
            u = self.units
            self.tag.setText(f"{u.fmt('T', m.T, 4)} {u.label('T')}  {u.fmt('P', m.P, 4)} {u.label('P')}\n"
                             f"{u.fmt('m', m.m, 4)} {u.label('m')}")
            self.tag.setBrush(muted_color())
            tb = self.tag.boundingRect()
            if self._horizontal:
                self.tag.setPos(mid.x() - tb.width() / 2, mid.y() + 3)
            else:
                self.tag.setPos(mid.x() + 4, mid.y() + br.height() / 2)
            self.tag.show()
        else:
            self.tag.hide()

    def boundingRect(self) -> QRectF:
        return super().boundingRect().adjusted(-8, -8, 8, 8)

    def shape(self) -> QPainterPath:
        st = QPainterPathStroker()
        st.setWidth(10)
        return st.createStroke(self.path())

    def paint(self, p: QPainter, option, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        col = self.color()
        width = 2.0
        if self.isSelected() or self._hover:
            width = 3.2
        pen = QPen(col, width)
        pen.setJoinStyle(Qt.RoundJoin)
        if self.stream.tear:
            pen.setStyle(Qt.DashLine)
        if self.isSelected():
            hl = QApplication.palette().color(QPalette.Highlight)
            hl.setAlpha(90)
            p.setPen(QPen(hl, 8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(self.path())
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(self.path())
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(self._arrow)

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()

    def tooltip_text(self, u: UnitSystem) -> str:
        m = self.stream.material
        s = f"<b>Stream {self.stream.name}</b>"
        if self.stream.tear:
            s += " (tear)"
        if m is None:
            return s + "<br>Not calculated yet"
        return (s + f"<br>{m.fluid.label()}<br>T = {u.fmt('T', m.T)} {u.label('T')}<br>"
                f"P = {u.fmt('P', m.P)} {u.label('P')}<br>ṁ = {u.fmt('m', m.m)} {u.label('m')}<br>"
                f"Vapor fraction = {u.fmt('frac', m.state.Q)}<br>{m.state.phase}")


# =====================================================================================
# Scene
# =====================================================================================

class FlowsheetScene(QGraphicsScene):
    modelChanged = Signal()          # structure or connections changed
    layoutChanged = Signal()         # only positions changed
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fs = Flowsheet()
        self.units = UnitSystem()
        self.show_conditions = False
        self.block_items: dict[str, BlockItem] = {}
        self.stream_items: dict[str, StreamItem] = {}
        self._drag_port: PortItem | None = None
        self._drag_line: QGraphicsPathItem | None = None
        self.moved_since_press = False
        self.setSceneRect(-2000, -2000, 6000, 4000)

    # -- loading --------------------------------------------------------------------
    def load(self, fs: Flowsheet):
        self.clear()
        self.block_items.clear()
        self.stream_items.clear()
        self.fs = fs
        for b in fs.blocks.values():
            self._add_block_item(b)
        for s in fs.streams.values():
            self._add_stream_item(s)
        self.refresh_results()

    def _add_block_item(self, b: UnitOp) -> BlockItem:
        item = BlockItem(b)
        self.addItem(item)
        self.block_items[b.uid] = item
        item.update_label()
        item.refresh_ports()
        return item

    def _add_stream_item(self, s: Stream) -> StreamItem:
        src = self.block_items[s.src].ports[s.src_port]
        dst = self.block_items[s.dst].ports[s.dst_port]
        item = StreamItem(s, src, dst)
        item.units = self.units
        item.show_conditions = self.show_conditions
        self.addItem(item)
        self.stream_items[s.uid] = item
        item.update_path()
        src.refresh()
        dst.refresh()
        return item

    # -- editing ------------------------------------------------------------------------
    def add_block(self, type_name: str, pos: QPointF) -> BlockItem:
        w, h = block_size(BLOCK_TYPES[type_name]("tmp", "tmp"))
        b = self.fs.add_block(type_name, round((pos.x() - w / 2) / GRID) * GRID,
                              round((pos.y() - h / 2) / GRID) * GRID)
        item = self._add_block_item(b)
        self.clearSelection()
        item.setSelected(True)
        self.modelChanged.emit()
        return item

    def delete_selected(self):
        items = self.selectedItems()
        if not items:
            return
        for it in items:
            if isinstance(it, StreamItem) and it.stream.uid in self.fs.streams:
                self._remove_stream(it.stream.uid)
        for it in items:
            if isinstance(it, BlockItem) and it.block.uid in self.fs.blocks:
                removed = self.fs.remove_block(it.block.uid)
                for sid in removed:
                    self._remove_stream_item(sid)
                self.removeItem(it)
                del self.block_items[it.block.uid]
        self.modelChanged.emit()

    def _remove_stream(self, sid: str):
        self.fs.remove_stream(sid)
        self._remove_stream_item(sid)

    def _remove_stream_item(self, sid: str):
        item = self.stream_items.pop(sid, None)
        if item is not None:
            self.removeItem(item)
            item.src.refresh()
            item.dst.refresh()

    def flip_selected(self):
        changed = False
        for it in self.selectedItems():
            if isinstance(it, BlockItem):
                it.block.layout["flip"] = not it.block.layout.get("flip", False)
                it.rebuild()
                self.block_moved(it)
                changed = True
        if changed:
            self.layoutChanged.emit()

    def rebuild_block(self, uid: str):
        """Call after a parameter that changes the ports (e.g. number of mixer inlets)."""
        for sid in self.fs.prune_ports(uid):
            self._remove_stream_item(sid)
        item = self.block_items[uid]
        item.rebuild()
        for s in self.fs.streams.values():
            if s.src == uid or s.dst == uid:
                si = self.stream_items[s.uid]
                si.src = self.block_items[s.src].ports[s.src_port]
                si.dst = self.block_items[s.dst].ports[s.dst_port]
                si.update_path()
        item.refresh_ports()

    def block_moved(self, item: BlockItem):
        self.moved_since_press = True
        for si in self.stream_items.values():
            si.update_path()

    def rename_item(self, item):
        if isinstance(item, BlockItem):
            name, ok = QInputDialog.getText(None, "Rename block", "Name:", text=item.block.name)
            if ok and name.strip():
                item.block.name = name.strip()
                item.update_label()
                self.modelChanged.emit()
        elif isinstance(item, StreamItem):
            name, ok = QInputDialog.getText(None, "Rename stream", "Name:", text=item.stream.name)
            if ok and name.strip():
                item.stream.name = name.strip()
                item.refresh_label()
                self.modelChanged.emit()

    # -- connecting -------------------------------------------------------------------------
    def start_connection(self, port: PortItem, pos: QPointF):
        self._drag_port = port
        self._drag_line = QGraphicsPathItem()
        pen = QPen(QApplication.palette().color(QPalette.Highlight), 1.6, Qt.DashLine)
        self._drag_line.setPen(pen)
        self._drag_line.setZValue(10)
        self.addItem(self._drag_line)
        self.update_connection(pos)

    def update_connection(self, pos: QPointF):
        if self._drag_port is None:
            return
        path = QPainterPath(self._drag_port.scenePos())
        path.lineTo(pos)
        self._drag_line.setPath(path)

    def finish_connection(self, pos: QPointF):
        src = self._drag_port
        self._drag_port = None
        if self._drag_line is not None:
            self.removeItem(self._drag_line)
            self._drag_line = None
        if src is None:
            return
        target = None
        for it in self.items(pos, Qt.IntersectsItemShape, Qt.DescendingOrder):
            if isinstance(it, PortItem) and it is not src:
                target = it
                break
        if target is None:
            # forgiving drop: nearest port within 14 px
            best = 14.0
            for bi in self.block_items.values():
                for pi in bi.ports.values():
                    d = QLineF(pi.scenePos(), pos).length()
                    if pi is not src and d < best:
                        best, target = d, pi
        if target is None:
            return
        if src.is_inlet == target.is_inlet:
            self.message.emit("Connect an outlet (red) to an inlet (blue).")
            return
        out_p, in_p = (target, src) if src.is_inlet else (src, target)
        try:
            s = self.fs.connect(out_p.block_item.block.uid, out_p.port_id, in_p.block_item.block.uid, in_p.port_id)
        except FlowsheetError as exc:
            self.message.emit(str(exc))
            return
        item = self._add_stream_item(s)
        self.clearSelection()
        item.setSelected(True)
        self.modelChanged.emit()

    # -- results ------------------------------------------------------------------------------
    def set_units(self, units: UnitSystem):
        self.units = units
        for si in self.stream_items.values():
            si.units = units
        self.refresh_results()

    def set_show_conditions(self, on: bool):
        self.show_conditions = on
        for si in self.stream_items.values():
            si.show_conditions = on
            si.refresh_label()

    def refresh_results(self):
        for bi in self.block_items.values():
            bi.setToolTip(bi.tooltip_text(self.units))
            bi.update_label()
            bi.update()
        for si in self.stream_items.values():
            si.setToolTip(si.tooltip_text(self.units))
            si.refresh_label()
            si.update()

    def refresh_theme(self):
        for bi in self.block_items.values():
            bi.update_label()
            bi.refresh_ports()
            bi.update()
        self.refresh_results()

    # -- painting & events ---------------------------------------------------------------------
    def drawBackground(self, p: QPainter, rect: QRectF):
        bg = QApplication.palette().color(QPalette.Base)
        p.fillRect(rect, bg)
        dot = QApplication.palette().color(QPalette.Text)
        dot.setAlpha(40 if not is_dark() else 55)
        step = GRID * 2
        left = math.floor(rect.left() / step) * step
        top = math.floor(rect.top() / step) * step
        pts = []
        x = left
        while x < rect.right():
            y = top
            while y < rect.bottom():
                pts.append(QPointF(x, y))
                y += step
            x += step
        p.setPen(QPen(dot, 1.3))
        p.drawPoints(pts)

    def mousePressEvent(self, e):
        self.moved_since_press = False
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if self.focusItem() is None and e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
            e.accept()
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        item = self.itemAt(e.scenePos(), QTransform())
        while item is not None and not isinstance(item, (BlockItem, StreamItem)):
            item = item.parentItem()
        if item is None:
            return
        if not item.isSelected():
            self.clearSelection()
            item.setSelected(True)
        menu = QMenu()
        a_ren = menu.addAction("Rename…")
        a_flip = a_tear = None
        if isinstance(item, BlockItem):
            a_flip = menu.addAction("Flip horizontally")
        else:
            a_tear = menu.addAction("Use as tear stream")
            a_tear.setCheckable(True)
            a_tear.setChecked(item.stream.tear)
        menu.addSeparator()
        a_del = menu.addAction("Delete")
        chosen = menu.exec(e.screenPos())
        if chosen is a_ren:
            self.rename_item(item)
        elif chosen is a_flip and a_flip is not None:
            self.flip_selected()
        elif chosen is a_tear and a_tear is not None:
            item.stream.tear = a_tear.isChecked()
            item.update()
            self.modelChanged.emit()
        elif chosen is a_del:
            self.delete_selected()

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_BLOCK):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_BLOCK):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasFormat(MIME_BLOCK):
            t = bytes(e.mimeData().data(MIME_BLOCK)).decode()
            self.add_block(t, e.scenePos())
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class FlowsheetView(QGraphicsView):
    def __init__(self, scene: FlowsheetScene):
        super().__init__(scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setAcceptDrops(True)
        self._pan = None

    def wheelEvent(self, e):
        # Ctrl/Cmd + wheel zooms; plain wheel / trackpad scrolls
        if e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            f = 1.0015 ** e.angleDelta().y()
            self.zoom(f)
            e.accept()
        else:
            super().wheelEvent(e)

    def zoom(self, f: float):
        cur = self.transform().m11()
        f = max(0.2 / cur, min(f, 4.0 / cur))
        self.scale(f, f)

    def zoom_fit(self):
        sc = self.scene()
        items = [i for i in sc.items() if isinstance(i, (BlockItem, StreamItem))]
        if not items:
            self.resetTransform()
            self.centerOn(0, 0)
            return
        r = QRectF()
        for i in items:
            r = r.united(i.sceneBoundingRect())
        r = r.adjusted(-60, -60, 60, 60)
        self.fitInView(r, Qt.KeepAspectRatio)
        if self.transform().m11() > 1.6:
            self.resetTransform()
            self.scale(1.6, 1.6)
            self.centerOn(r.center())

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and e.modifiers() & Qt.AltModifier):
            self._pan = e.position()
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.unsetCursor()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_BLOCK):
            e.acceptProposedAction()
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_BLOCK):
            e.acceptProposedAction()
        super().dragMoveEvent(e)
