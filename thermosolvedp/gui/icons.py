"""Vector icons for the unit operations, drawn with QPainter so they scale and follow light/dark themes."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication

# hue (HSV degrees) per block type, used for the soft fill and accents
HUES = {
    "feed": 140, "product": 140,
    "heater": 8, "cooler": 205,
    "compressor": 175, "pump": 175, "expander": 175, "valve": 30,
    "hx": 265, "flash": 290,
    "mixer": 45, "splitter": 45,
    "reactor": 15,
}
HOT = QColor(214, 69, 65)
COLD = QColor(52, 120, 200)


def is_dark() -> bool:
    pal = QApplication.palette()
    return pal.color(QPalette.Window).lightness() < 128


def theme_colors(type_name: str) -> tuple[QColor, QColor]:
    """(fill, stroke) for a block type in the current theme."""
    pal = QApplication.palette()
    base = pal.color(QPalette.Base)
    stroke = pal.color(QPalette.Text)
    hue = HUES.get(type_name, 0)
    tint = QColor.fromHsv(hue, 150, 210)
    a = 0.30 if is_dark() else 0.20
    fill = QColor(int(base.red() * (1 - a) + tint.red() * a),
                  int(base.green() * (1 - a) + tint.green() * a),
                  int(base.blue() * (1 - a) + tint.blue() * a))
    return fill, stroke


def _poly(*pts) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _zigzag(x0: float, x1: float, y: float, amp: float, n: int) -> QPainterPath:
    path = QPainterPath(QPointF(x0, y))
    step = (x1 - x0) / (2 * n)
    for i in range(1, 2 * n + 1):
        path.lineTo(x0 + i * step, y + (amp if i % 2 else -amp) if i < 2 * n else y)
    return path


def draw_icon(p: QPainter, type_name: str, w: float, h: float,
              fill: QColor | None = None, stroke: QColor | None = None, width: float = 1.6) -> None:
    """Draw a block symbol inside the rectangle (0, 0, w, h)."""
    if fill is None or stroke is None:
        fill, stroke = theme_colors(type_name)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(stroke, width)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(fill)
    t = type_name
    if t == "feed":
        p.drawPolygon(_poly((1, 1), (w * 0.66, 1), (w - 1, h / 2), (w * 0.66, h - 1), (1, h - 1)))
    elif t == "product":
        p.drawPolygon(_poly((1, 1), (w * 0.66, 1), (w - 1, h / 2), (w * 0.66, h - 1), (1, h - 1), (w * 0.22, h / 2)))
    elif t in ("heater", "cooler"):
        d = min(w, h) - 4
        r = QRectF((w - d) / 2, (h - d) / 2, d, d)
        p.drawLine(QPointF(0, h / 2), QPointF(r.left(), h / 2))
        p.drawLine(QPointF(r.right(), h / 2), QPointF(w, h / 2))
        p.drawEllipse(r)
        acc = HOT if t == "heater" else COLD
        p.setPen(QPen(acc, width + 0.4))
        p.setBrush(Qt.NoBrush)
        p.drawPath(_zigzag(r.left() + d * 0.18, r.right() - d * 0.18, h / 2, d * 0.16, 3))
        # arrow showing heat in (heater) or out (cooler)
        cx = w / 2
        if t == "heater":
            p.drawLine(QPointF(cx, r.bottom() + 1), QPointF(cx, r.bottom() - d * 0.22))
        else:
            p.drawLine(QPointF(cx, r.top() - 1), QPointF(cx, r.top() + d * 0.22))
    elif t == "compressor":
        p.drawPolygon(_poly((1, 1), (w - 1, h * 0.28), (w - 1, h * 0.72), (1, h - 1)))
    elif t == "expander":
        p.drawPolygon(_poly((1, h * 0.28), (w - 1, 1), (w - 1, h - 1), (1, h * 0.72)))
    elif t == "pump":
        d = min(w, h) - 4
        r = QRectF((w - d) / 2, (h - d) / 2, d, d)
        p.drawLine(QPointF(0, h / 2), QPointF(r.left(), h / 2))
        p.drawLine(QPointF(r.right(), h / 2), QPointF(w, h / 2))
        p.drawEllipse(r)
        p.setBrush(stroke)
        cx, cy = w / 2, h / 2
        p.drawPolygon(_poly((cx - d * 0.18, cy - d * 0.24), (cx + d * 0.26, cy), (cx - d * 0.18, cy + d * 0.24)))
    elif t == "valve":
        p.drawPolygon(_poly((1, 1), (w / 2, h / 2), (1, h - 1)))
        p.drawPolygon(_poly((w - 1, 1), (w / 2, h / 2), (w - 1, h - 1)))
    elif t == "hx":
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 7, 7)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(HOT, width + 0.4))
        p.drawLine(QPointF(0, h * 0.32), QPointF(w, h * 0.32))
        p.setPen(QPen(COLD, width + 0.4))
        p.drawPath(_zigzag(w * 0.1, w * 0.9, h * 0.68, h * 0.09, 5))
        p.drawLine(QPointF(0, h * 0.68), QPointF(w * 0.1, h * 0.68))
        p.drawLine(QPointF(w * 0.9, h * 0.68), QPointF(w, h * 0.68))
    elif t == "flash":
        r = QRectF(1, 1, w - 2, h - 2)
        p.drawRoundedRect(r, w * 0.45, w * 0.45)
        clip = QPainterPath()
        clip.addRoundedRect(r, w * 0.45, w * 0.45)
        p.save()
        p.setClipPath(clip)
        liq = QColor(COLD)
        liq.setAlpha(70)
        p.setPen(Qt.NoPen)
        p.setBrush(liq)
        p.drawRect(QRectF(0, h * 0.62, w, h))
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(COLD, width))
        p.drawLine(QPointF(w * 0.12, h * 0.62), QPointF(w * 0.88, h * 0.62))
        p.setPen(pen)
        p.drawRoundedRect(r, w * 0.45, w * 0.45)
    elif t == "mixer":
        p.drawPolygon(_poly((1, 1), (w - 1, h / 2), (1, h - 1)))
    elif t == "reactor":
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 8, 8)
        # flame
        cx, base, fh, fw = w / 2, h * 0.80, h * 0.58, w * 0.26
        flame = QPainterPath(QPointF(cx, base))
        flame.cubicTo(QPointF(cx - fw * 1.3, base - fh * 0.15), QPointF(cx - fw * 0.9, base - fh * 0.65),
                      QPointF(cx - fw * 0.1, base - fh))
        flame.cubicTo(QPointF(cx - fw * 0.05, base - fh * 0.62), QPointF(cx + fw * 0.55, base - fh * 0.75),
                      QPointF(cx + fw * 0.45, base - fh * 0.42))
        flame.cubicTo(QPointF(cx + fw * 1.35, base - fh * 0.3), QPointF(cx + fw * 1.05, base - fh * 0.02),
                      QPointF(cx, base))
        fill = QColor(HOT)
        fill.setAlpha(150)
        p.setPen(QPen(HOT, width))
        p.setBrush(fill)
        p.drawPath(flame)
    elif t == "splitter":
        p.drawPolygon(_poly((w - 1, 1), (1, h / 2), (w - 1, h - 1)))
    else:
        p.drawRect(QRectF(1, 1, w - 2, h - 2))
    p.restore()


def block_icon(type_name: str, size: int = 32) -> QIcon:
    from .geometry import SIZES

    w, h = SIZES.get(type_name, (60, 50))
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    s = (size - 4) / max(w, h)
    p.translate((size - w * s) / 2, (size - h * s) / 2)
    p.scale(s, s)
    draw_icon(p, type_name, w, h, width=1.6 / s if s < 1 else 1.6)
    p.end()
    return QIcon(pm)


def app_icon_pixmap(size: int, margin: float = 0.0) -> QPixmap:
    """The application icon at a given pixel size (margin = fraction of empty border, as macOS icons use)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    inset = size * margin
    inner = size - 2 * inset
    p.translate(inset, inset)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(38, 52, 74))
    p.drawRoundedRect(QRectF(0, 0, inner, inner), inner * 0.22, inner * 0.22)
    s = inner / 100
    p.scale(s, s)
    p.setPen(QPen(HOT, 9, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(18, 38), QPointF(82, 38))
    p.setPen(QPen(QColor(110, 170, 235), 9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(_zigzag(18, 82, 66, 9, 3))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon.addPixmap(app_icon_pixmap(size))
    return icon
