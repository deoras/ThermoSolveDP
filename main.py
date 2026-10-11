"""Start ThermoSolveDP:  python main.py [flowsheet.tpf]

    python main.py --selftest   solve every built-in example headlessly and exit (used by the app builders)
"""
import sys
from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication


class ThermoSolveApp(QApplication):
    """QApplication that also accepts macOS 'open document' events (double-clicking a .tpf in Finder)."""

    def __init__(self, argv):
        super().__init__(argv)
        self.window = None
        self.pending: list[Path] = []

    def event(self, e):
        if e.type() == QEvent.FileOpen:
            path = Path(e.file())
            if self.window is None:
                self.pending.append(path)
            else:
                self.window.open_path_from_os(path)
            return True
        return super().event(e)


def selftest() -> int:
    """Solve every example and build the GUI pieces without showing them. Exit code 0 = all good."""
    import os
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.stdout is None or sys.stderr is None:
        # Windowed builds (Windows .exe) have no console: write the report to a log file instead,
        # next to the executable, and also echo it to the console the program was started from.
        log_path = Path(sys.executable).with_name("selftest.log")
        log = open(log_path, "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from thermosolvedp.engine import solve
    from thermosolvedp.engine.thermo import set_property_package
    from thermosolvedp.examples import EXAMPLES
    from thermosolvedp.gui.main_window import MainWindow
    from thermosolvedp.gui.plots import PlotWindow

    ok = True
    win = MainWindow()
    plots = PlotWindow(win)
    for title, (_fname, build) in EXAMPLES.items():
        fs = build()
        rep = solve(fs)
        status = "OK" if rep.converged else f"FAILED: {rep.error}"
        print(f"  {title}: {status} ({rep.elapsed:.1f} s)", flush=True)
        ok &= rep.converged
        if rep.converged:
            plots.set_data(fs, win.units)
    set_property_package("HEOS")
    win.dirty = False
    win.close()
    del app
    print("SELFTEST " + ("PASSED" if ok else "FAILED"), flush=True)
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    from thermosolvedp.gui.icons import app_icon
    from thermosolvedp.gui.main_window import MainWindow

    app = ThermoSolveApp(sys.argv)
    app.setApplicationName("ThermoSolveDP")
    app.setOrganizationName("ThermoSolveDP")
    app.setWindowIcon(app_icon())
    win = MainWindow()
    app.window = win
    win.show()
    files = [Path(a) for a in sys.argv[1:] if not a.startswith("-") and Path(a).is_file()]
    for p in files + app.pending:
        win.open_path_from_os(p)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
