"""Render the application icon to packaging/ThermoSolveDP.icns (macOS) and .ico (Windows).

Run from the project folder:  python packaging/make_icons.py
"""
import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from thermosolvedp.gui.icons import app_icon_pixmap  # noqa: E402


def render(size: int, margin: float) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    app_icon_pixmap(size, margin).save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main():
    app = QApplication.instance() or QApplication([])
    out = ROOT / "packaging"
    # macOS icons leave ~10% transparent border around the rounded square
    mac = render(1024, 0.09)
    mac.save(out / "ThermoSolveDP.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256),
                                                (512, 512), (1024, 1024)])
    win = render(256, 0.0)
    win.save(out / "ThermoSolveDP.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
                                               (256, 256)])
    mac.save(out / "icon_1024.png")
    print("wrote", out / "ThermoSolveDP.icns", out / "ThermoSolveDP.ico")
    del app


if __name__ == "__main__":
    main()
