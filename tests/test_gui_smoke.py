"""Offscreen GUI smoke test: open each example, solve through the worker thread, open the diagrams."""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from thermosolvedp.examples import EXAMPLES  # noqa: E402
from thermosolvedp.gui.main_window import MainWindow  # noqa: E402

SHOTS = os.environ.get("THERMOSOLVEDP_SHOTS")


def wait_for_solve(win, timeout_ms=60000):
    loop = QEventLoop()
    QTimer.singleShot(timeout_ms, loop.quit)
    timer = QTimer()
    timer.timeout.connect(lambda: win._thread is None and loop.quit())
    timer.start(20)
    loop.exec()
    timer.stop()


@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.dirty = False
    w.resize(1500, 900)
    w.show()
    yield w
    w.dirty = False
    w.close()


@pytest.mark.parametrize("title", list(EXAMPLES))
def test_example_solves_in_gui(win, title):
    win.dirty = False
    win.open_example(title)
    QApplication.processEvents()
    win.run()
    wait_for_solve(win)
    assert win.report is not None and win.report.converged, win.report.error
    assert win.stream_table.columnCount() == len(win.scene.fs.streams)
    win.a_cond.setChecked(True)
    win._toggle_conditions()
    win.show_plots()
    QApplication.processEvents()
    win.view.zoom_fit()
    QApplication.processEvents()
    if SHOTS:
        name = EXAMPLES[title][0].replace(".tpf", "")
        win.grab().save(f"{SHOTS}/{name}.png")
        for i in range(3):
            win.plot_window.tabs.setCurrentIndex(i)
            QApplication.processEvents()
            win.plot_window.grab().save(f"{SHOTS}/{name}_plot{i}.png")
    win.plot_window.close()


def test_select_block_and_stream_shows_properties(win):
    win.dirty = False
    win.open_example("Rankine steam cycle")
    item = next(iter(win.scene.block_items.values()))
    win.scene.clearSelection()
    item.setSelected(True)
    QApplication.processEvents()
    assert win.props.current == ("block", item.block.uid)
    s = next(iter(win.scene.stream_items.values()))
    win.scene.clearSelection()
    s.setSelected(True)
    QApplication.processEvents()
    assert win.props.current == ("stream", s.stream.uid)


def test_add_connect_and_mixer_port_change(win):
    win.dirty = False
    win.new_flowsheet(ask=False)
    sc = win.scene
    from PySide6.QtCore import QPointF
    f = sc.add_block("feed", QPointF(0, 0))
    m = sc.add_block("mixer", QPointF(150, 0))
    sc.start_connection(f.ports["out"], f.ports["out"].scenePos())
    sc.finish_connection(m.ports["in2"].scenePos())
    assert len(sc.fs.streams) == 1
    m.block.set_value("n_in", 3)
    win._on_block_edited(m.block.uid, True)
    assert "in3" in sc.block_items[m.block.uid].ports
    m.block.set_value("n_in", 2)
    win._on_block_edited(m.block.uid, True)
    assert len(sc.fs.streams) == 1          # in2 survives a reduction to 2 inlets
    sc.clearSelection()
    f.setSelected(True)
    sc.delete_selected()
    assert len(sc.fs.streams) == 0 and len(sc.fs.blocks) == 1
    win.dirty = False


def test_fluid_package_dialog_lists_pairs_and_saves_user_kij(win):
    from thermosolvedp.engine.solver import flowsheet_components
    from thermosolvedp.gui.dialogs import FluidPackageDialog
    win.dirty = False
    win.open_example("Mixed-refrigerant JT cryocooler (7 components, Peng-Robinson)")
    fs = win.scene.fs
    dlg = FluidPackageDialog(fs.settings, flowsheet_components(fs), win)
    assert dlg.table.rowCount() == 21
    sources = [dlg.table.item(r, 3).text() for r in range(21)]
    assert sources.count("No data – assumed 0") == 13
    row = sources.index("No data – assumed 0")
    dlg.table.item(row, 2).setText("0.08")
    assert dlg.settings()["kij"] and list(dlg.settings()["kij"].values()) == [0.08]
    if SHOTS:
        dlg.resize(820, 620)
        dlg.show()
        QApplication.processEvents()
        dlg.grab().save(f"{SHOTS}/fluid_package.png")
    dlg.close()
    win.dirty = False


def test_export_menu_and_copy(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    win.dirty = False
    win.open_example("Rankine steam cycle")
    win.run()
    wait_for_solve(win)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "rk.csv"), "CSV"))
    win.export("all")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rk_blocks.csv", "rk_energy.csv", "rk_streams.csv"]
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "only"), "CSV"))
    win.export("energy")
    assert (tmp_path / "only.csv").exists()
    t = win.summary_table
    t.selectAll()
    t.copy_selection()
    txt = QApplication.clipboard().text()
    assert "Thermal efficiency" in txt and "\t" in txt
    win.dirty = False
