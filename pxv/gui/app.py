"""pXv graphical interface.

A wizard over the same project structure the CLI uses, so anything done here can
be reproduced headlessly with ``pxv run project.yaml``.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSlider, QSplitter, QStackedWidget, QStatusBar, QVBoxLayout,
    QWidget,
)

from pxv.gui.forms import SectionForm
from pxv.gui.workers import start
from pxv.io import metadata as meta_io
from pxv.io import video as video_io
from pxv.preprocess.enhance import crop, preprocess_frame, static_background, to_gray
from pxv.project import Project, load_project, save_project

STEPS = [
    ("Video", "video"),
    ("Preprocess", "preprocess"),
    ("Stabilise", "stabilize"),
    ("Geometry", "geometry"),
    ("PIV", "piv"),
    ("PTV", "ptv"),
    ("Grid", "grid"),
    ("Ground truth", "groundtruth"),
    ("Calibrate", "calibrate"),
    ("Export", "export"),
]


class MainWindow(QMainWindow):
    def __init__(self, project_path: str | None = None):
        super().__init__()
        self.setWindowTitle("pXv  |  mixed PIV / PTV surface velocimetry")
        self.resize(1500, 950)

        self.project = Project()
        self.project_path: Path | None = None
        self.frames: np.ndarray | None = None
        self.result = None
        self._thread = None
        self._worker = None
        self._pick_mode: str | None = None
        self._picked: list[tuple[float, float]] = []

        self._build_ui()
        if project_path:
            self.load(project_path)

    # ------------------------------------------------------------------ ui

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal)

        # --- left: step list plus the form for the selected step
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 8, 8)
        self.step_list = QListWidget()
        self.step_list.addItems([name for name, _ in STEPS])
        self.step_list.setCurrentRow(0)
        self.step_list.setMaximumHeight(240)
        self.step_list.currentRowChanged.connect(self._on_step)
        lv.addWidget(QLabel("<b>Steps</b>"))
        lv.addWidget(self.step_list)

        self.forms = QStackedWidget()
        self.section_forms: dict[str, SectionForm] = {}
        for _, key in STEPS:
            form = SectionForm(getattr(self.project, key))
            self.section_forms[key] = form
            scroll = QScrollArea()
            scroll.setWidget(form)
            scroll.setWidgetResizable(True)
            self.forms.addWidget(scroll)
        lv.addWidget(self.forms, 1)

        pick_row = QHBoxLayout()
        self.btn_pick_scale = QPushButton("Pick 2 points for known distance")
        self.btn_pick_scale.clicked.connect(lambda: self._begin_pick("scale"))
        pick_row.addWidget(self.btn_pick_scale)
        lv.addLayout(pick_row)

        run_row = QHBoxLayout()
        self.btn_run = QPushButton("Run")
        self.btn_run.clicked.connect(self.run_pipeline)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_export = QPushButton("Export")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export)
        run_row.addWidget(self.btn_run)
        run_row.addWidget(self.btn_cancel)
        run_row.addWidget(self.btn_export)
        lv.addLayout(run_row)
        splitter.addWidget(left)

        # --- centre: image view plus overlays
        centre = QWidget()
        cv = QVBoxLayout(centre)
        cv.setContentsMargins(4, 4, 4, 4)

        bar = QHBoxLayout()
        self.view_mode = QComboBox()
        self.view_mode.addItems(["raw frame", "preprocessed", "speed map"])
        self.view_mode.currentTextChanged.connect(self._redraw)
        bar.addWidget(QLabel("view:"))
        bar.addWidget(self.view_mode)
        self.chk_vectors = QComboBox()
        self.chk_vectors.addItems(["vectors on", "vectors off"])
        self.chk_vectors.currentTextChanged.connect(self._redraw)
        bar.addWidget(self.chk_vectors)
        bar.addStretch(1)
        cv.addLayout(bar)

        pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)
        self.plot = pg.PlotWidget()
        self.plot.setAspectLocked(True)
        self.plot.invertY(True)
        self.image_item = pg.ImageItem()
        self.plot.addItem(self.image_item)
        self.vector_item = pg.PlotDataItem(pen=None)
        self.plot.addItem(self.vector_item)
        self.marker_item = pg.ScatterPlotItem(size=11, brush=pg.mkBrush(255, 90, 60, 220))
        self.plot.addItem(self.marker_item)
        self.plot.scene().sigMouseClicked.connect(self._on_click)
        cv.addWidget(self.plot, 1)

        srow = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.valueChanged.connect(self._redraw)
        self.frame_label = QLabel("frame -")
        srow.addWidget(QLabel("frame:"))
        srow.addWidget(self.slider, 1)
        srow.addWidget(self.frame_label)
        cv.addLayout(srow)
        splitter.addWidget(centre)

        # --- right: log
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 8, 8, 8)
        rv.addWidget(QLabel("<b>Log</b>"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        rv.addWidget(self.log_view, 1)
        splitter.addWidget(right)

        splitter.setSizes([380, 760, 360])
        self.setCentralWidget(splitter)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        status = QStatusBar()
        status.addPermanentWidget(self.progress)
        self.setStatusBar(status)
        self._build_menu()

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&File")
        for text, slot, key in (
            ("Open video...", self.open_video, QKeySequence.Open),
            ("Open project...", self.open_project, "Ctrl+Shift+O"),
            ("Save project", self.save, QKeySequence.Save),
            ("Save project as...", self.save_as, "Ctrl+Shift+S"),
        ):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(slot)
            m.addAction(a)
        m.addSeparator()
        a = QAction("Quit", self)
        a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close)
        m.addAction(a)

    # ------------------------------------------------------------ plumbing

    def log(self, text: str) -> None:
        self.log_view.appendPlainText(str(text))

    def _on_step(self, row: int) -> None:
        self.forms.setCurrentIndex(row)

    def _apply_forms(self) -> None:
        for form in self.section_forms.values():
            form.apply()

    def _refresh_forms(self) -> None:
        for key, form in self.section_forms.items():
            form.obj = getattr(self.project, key)
            form.refresh()

    # --------------------------------------------------------------- files

    def open_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open video", "", "Video (*.mp4 *.avi *.mov *.mkv);;All files (*)")
        if not path:
            return
        try:
            info = video_io.probe(path)
        except Exception as exc:
            QMessageBox.critical(self, "pXv", f"Could not open the video:\n{exc}")
            return
        self.project.video.path = path
        self.project.video.fps = info.fps
        self.project.name = Path(path).stem
        self.project.export.outdir = str(Path(path).with_suffix("").parent / f"{Path(path).stem}-out")
        self._refresh_forms()
        self.log(info.describe())
        tel = meta_io.read_telemetry(path)
        self.log(tel.describe())
        if not len(tel):
            self.log("No DJI SRT sidecar. Height and heading must be entered under Geometry.")
        self.load_frames()

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "YAML (*.yaml *.yml)")
        if path:
            self.load(path)

    def load(self, path: str) -> None:
        try:
            self.project = load_project(path)
        except Exception as exc:
            QMessageBox.critical(self, "pXv", f"Could not read the project:\n{exc}")
            return
        self.project_path = Path(path)
        self._refresh_forms()
        self.log(f"loaded {path}")
        if self.project.video.path:
            self.load_frames()

    def save(self) -> None:
        if self.project_path is None:
            self.save_as()
            return
        self._apply_forms()
        save_project(self.project, self.project_path)
        self.log(f"saved {self.project_path}")

    def save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save project", "project.yaml", "YAML (*.yaml)")
        if path:
            self.project_path = Path(path)
            self.save()

    # -------------------------------------------------------------- frames

    def load_frames(self) -> None:
        self._apply_forms()
        if not self.project.video.path:
            return
        self.statusBar().showMessage("decoding...")

        def job(progress=None):
            from pxv.pipeline import load_frames as lf
            return lf(self.project)

        def done(payload):
            self.frames, info, _ = payload
            self.slider.setMaximum(max(0, len(self.frames) - 1))
            self.slider.setValue(0)
            self.statusBar().showMessage(f"{len(self.frames)} frames ready", 4000)
            self.log(f"decoded {len(self.frames)} frames of "
                     f"{self.frames.shape[2]}x{self.frames.shape[1]}")
            self._redraw()

        self._thread, self._worker = start(
            self, job, on_done=done, on_fail=self._fail)

    def _preprocessed(self, idx: int) -> np.ndarray:
        bg = None
        if self.project.preprocess.background == "static":
            bg = static_background(self.frames, self.project.preprocess.bg_stride)
        return preprocess_frame(self.frames[idx], self.project.preprocess, bg)

    def _redraw(self, *_) -> None:
        if self.frames is None:
            return
        idx = min(self.slider.value(), len(self.frames) - 1)
        self.frame_label.setText(f"frame {idx}")
        mode = self.view_mode.currentText()
        try:
            if mode == "preprocessed":
                img = self._preprocessed(idx)
            elif mode == "speed map" and self.result is not None:
                u, v = self.result.velocity_world()
                img = np.where(self.result.field.valid, np.hypot(u, v), np.nan)
            else:
                img = self.frames[idx].astype(np.float32) / 255.0
        except Exception as exc:
            self.log(f"preview failed: {exc}")
            return
        self.image_item.setImage(img, autoLevels=True)
        if mode == "speed map" and self.result is not None:
            self.image_item.setRect(pg.QtCore.QRectF(
                0, 0, self.frames.shape[2], self.frames.shape[1]))
        else:
            self.image_item.setRect(pg.QtCore.QRectF(
                0, 0, img.shape[1], img.shape[0]))
        self._draw_vectors()

    def _draw_vectors(self) -> None:
        if self.result is None or self.chk_vectors.currentText() == "vectors off":
            self.vector_item.setData([], [])
            return
        fld = self.result.field
        step = max(1, fld.u.shape[0] // 28)
        x = fld.x[::step, ::step][fld.valid[::step, ::step]]
        y = fld.y[::step, ::step][fld.valid[::step, ::step]]
        u = fld.u[::step, ::step][fld.valid[::step, ::step]]
        v = fld.v[::step, ::step][fld.valid[::step, ::step]]
        if len(x) == 0:
            self.vector_item.setData([], [])
            return
        k = 6.0
        xs, ys = [], []
        for a, b, du, dv in zip(x, y, u, v):
            xs += [a, a + k * du, np.nan]
            ys += [b, b + k * dv, np.nan]
        self.vector_item.setData(xs, ys, connect="finite",
                                 pen=pg.mkPen((255, 200, 40), width=1.4))

    # ---------------------------------------------------------- point pick

    def _begin_pick(self, mode: str) -> None:
        self._pick_mode = mode
        self._picked = []
        self.marker_item.setData([], [])
        self.statusBar().showMessage("click two points spanning the known distance", 8000)

    def _on_click(self, event) -> None:
        if self._pick_mode is None:
            return
        p = self.plot.plotItem.vb.mapSceneToView(event.scenePos())
        self._picked.append((float(p.x()), float(p.y())))
        self.marker_item.setData([q[0] for q in self._picked], [q[1] for q in self._picked])
        if len(self._picked) == 2:
            (x1, y1), (x2, y2) = self._picked
            self.project.geometry.mode = "known_distance"
            self.project.geometry.known_p1 = [round(x1, 1), round(y1, 1)]
            self.project.geometry.known_p2 = [round(x2, 1), round(y2, 1)]
            self._refresh_forms()
            d = float(np.hypot(x2 - x1, y2 - y1))
            self.log(f"known-distance points set, {d:.1f} px apart. "
                     f"Enter the real length under Geometry: known length m.")
            self._pick_mode = None
            self.statusBar().showMessage("now set 'known length m' under Geometry", 8000)

    # ------------------------------------------------------------- running

    def run_pipeline(self) -> None:
        self._apply_forms()
        if not self.project.video.path:
            QMessageBox.information(self, "pXv", "Open a video first.")
            return
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setRange(0, 0)
        self.log("-" * 60)

        project = self.project

        def job(progress=None):
            from pxv.pipeline import run
            return run(project, verbose=False, progress=progress)

        def done(result):
            self.result = result
            for line in result.log:
                self.log(line)
            self.log(result.summary())
            self.btn_run.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            self.btn_export.setEnabled(True)
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.view_mode.setCurrentText("speed map")
            self._redraw()

        self._thread, self._worker = start(
            self, job, on_done=done, on_fail=self._fail, on_progress=self._on_progress)

    def _on_progress(self, current: int, total: int) -> None:
        if total and total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(current)
        else:
            self.progress.setRange(0, 0)

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.statusBar().showMessage("cancelling...", 3000)

    def _fail(self, message: str) -> None:
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        if message.strip() == "cancelled":
            self.log("cancelled")
            return
        self.log(message)
        QMessageBox.critical(self, "pXv", message.strip().splitlines()[-1])

    def closeEvent(self, event) -> None:
        """Stop background work before the window goes away.

        A QThread outliving its window aborts the process, so cancel and wait.
        """
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            if not self._thread.wait(5000):
                self._thread.terminate()
                self._thread.wait(1000)
        super().closeEvent(event)

    def export(self) -> None:
        if self.result is None:
            return
        self._apply_forms()
        from pxv.export import datasets, streamlines

        outdir = Path(self.project.export.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        idx = min(self.slider.value(), len(self.frames) - 1)
        backdrop = self.frames[idx]
        ecfg = self.project.export
        try:
            written = [streamlines.plot_streamlines(
                self.result, outdir / f"{self.project.name}-streamlines.jpg",
                backdrop=backdrop, cmap=ecfg.cmap, backdrop_alpha=ecfg.backdrop_alpha,
                dpi=ecfg.dpi, title=f"{self.project.name}  |  surface velocity")]
            written.append(streamlines.plot_quicklook(
                self.result, outdir / f"{self.project.name}-quicklook.png",
                backdrop=backdrop, cmap=ecfg.cmap))
            for fn, name in ((datasets.write_geotiff, f"{self.project.name}-velocity.tif"),
                             (datasets.write_gpkg, f"{self.project.name}-velocity.gpkg"),
                             (datasets.write_ugrid, f"{self.project.name}-mesh.nc")):
                if (p := fn(self.result, outdir / name)):
                    written.append(p)
        except Exception:
            self._fail(traceback.format_exc())
            return
        for p in written:
            self.log(f"wrote {p}")
        self.statusBar().showMessage(f"exported to {outdir}", 6000)


def main(project_path: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow(project_path)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
