"""pXv graphical interface.

The window is built around one large viewport. Everything else, the parameter
panel and the log, is subordinate to it, because the work is judging imagery: is
the mask on the water, is the field plausible, does the streamplot look like the
river. Parameters are edited on the same project structure the CLI reads, so any
session can be reproduced headlessly with ``pxv run project.yaml``.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSlider, QStackedWidget, QStatusBar, QToolBar, QVBoxLayout,
    QWidget,
)

from pxv.gui.forms import SectionForm
from pxv.gui.viewport import MODES, Viewport
from pxv.gui.workers import start
from pxv.io import metadata as meta_io
from pxv.io import video as video_io
from pxv.preprocess.enhance import preprocess_frame, rolling_background, static_background
from pxv.project import Project, load_project, save_project

STEPS = [
    ("Video", "video"),
    ("Preprocess", "preprocess"),
    ("Stabilise", "stabilize"),
    ("Geometry", "geometry"),
    ("Analysis region", "analysis"),
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
        self.resize(1680, 1000)

        self.project = Project()
        self.project_path: Path | None = None
        self.frames: np.ndarray | None = None
        self.result = None
        self.groundtruth = None
        self._streamplot: np.ndarray | None = None
        self._thread = None
        self._worker = None

        self._build_ui()
        if project_path:
            self.load(project_path)

    # ------------------------------------------------------------------ ui

    def _build_ui(self) -> None:
        # --- the viewport is the centre of the application
        self.viewport = Viewport()
        self.viewport.polygon_finished.connect(self._on_polygon)

        centre = QWidget()
        cv = QVBoxLayout(centre)
        cv.setContentsMargins(6, 4, 6, 4)
        cv.setSpacing(4)
        cv.addWidget(self._view_bar())
        cv.addWidget(self.viewport, 1)
        cv.addWidget(self._frame_bar())
        self.setCentralWidget(centre)

        self._build_parameter_dock()
        self._build_log_dock()
        self._build_toolbar()
        self._build_menu()

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(300)
        status = QStatusBar()
        status.addPermanentWidget(self.progress)
        self.setStatusBar(status)

    def _view_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        row.addWidget(QLabel("<b>View</b>"))
        self.mode_box = QComboBox()
        self.mode_box.addItems(MODES)
        self.mode_box.setMinimumWidth(150)
        self.mode_box.currentTextChanged.connect(self._redraw)
        row.addWidget(self.mode_box)

        self.overlay_vectors = QComboBox()
        self.overlay_vectors.addItems(["vectors: on", "vectors: off"])
        self.overlay_vectors.currentTextChanged.connect(self._redraw)
        row.addWidget(self.overlay_vectors)

        self.overlay_gt = QComboBox()
        self.overlay_gt.addItems(["ground truth: on", "ground truth: off"])
        self.overlay_gt.currentTextChanged.connect(self._redraw)
        row.addWidget(self.overlay_gt)

        self.overlay_mask = QComboBox()
        self.overlay_mask.addItems(["mask: on", "mask: off"])
        self.overlay_mask.currentTextChanged.connect(self._redraw)
        row.addWidget(self.overlay_mask)

        row.addSpacing(16)
        self.btn_draw = QPushButton("Draw analysis polygon")
        self.btn_draw.setCheckable(True)
        self.btn_draw.toggled.connect(self._toggle_draw)
        row.addWidget(self.btn_draw)
        self.btn_clear_poly = QPushButton("Clear polygons")
        self.btn_clear_poly.clicked.connect(self._clear_polygons)
        row.addWidget(self.btn_clear_poly)

        row.addStretch(1)
        self.fit_button = QPushButton("Fit view")
        self.fit_button.clicked.connect(lambda: self.viewport.plot.autoRange())
        row.addWidget(self.fit_button)
        return bar

    def _frame_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("frame"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.valueChanged.connect(self._redraw)
        row.addWidget(self.slider, 1)
        self.frame_label = QLabel("-")
        self.frame_label.setMinimumWidth(120)
        row.addWidget(self.frame_label)
        return bar

    def _build_parameter_dock(self) -> None:
        panel = QWidget()
        lv = QVBoxLayout(panel)
        lv.setContentsMargins(8, 8, 8, 8)

        self.step_box = QComboBox()
        self.step_box.addItems([name for name, _ in STEPS])
        self.step_box.currentIndexChanged.connect(lambda i: self.forms.setCurrentIndex(i))
        lv.addWidget(self.step_box)

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

        dock = QDockWidget("Parameters", self)
        dock.setWidget(panel)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        dock.setMinimumWidth(330)
        self.param_dock = dock

    def _build_log_dock(self) -> None:
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        dock = QDockWidget("Log", self)
        dock.setWidget(self.log_view)
        dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        dock.setMaximumHeight(220)
        self.log_dock = dock

    def _build_toolbar(self) -> None:
        tb = QToolBar("Run")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.btn_run = QPushButton("Run")
        self.btn_run.clicked.connect(self.run_pipeline)
        tb.addWidget(self.btn_run)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        tb.addWidget(self.btn_cancel)
        tb.addSeparator()

        self.btn_export_image = QPushButton("Export image")
        self.btn_export_image.setToolTip(
            "Streamplot JPG coloured by speed, with a world file so it drops into QGIS")
        self.btn_export_image.setEnabled(False)
        self.btn_export_image.clicked.connect(self.export_image)
        tb.addWidget(self.btn_export_image)

        self.btn_export_mesh = QPushButton("Export mesh")
        self.btn_export_mesh.setToolTip(
            "UGRID NetCDF mesh, GeoTIFF and GeoPackage points for QGIS")
        self.btn_export_mesh.setEnabled(False)
        self.btn_export_mesh.clicked.connect(self.export_mesh)
        tb.addWidget(self.btn_export_mesh)

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

        v = self.menuBar().addMenu("&View")
        v.addAction(self.param_dock.toggleViewAction())
        v.addAction(self.log_dock.toggleViewAction())

    # ------------------------------------------------------------ plumbing

    def log(self, text: str) -> None:
        self.log_view.appendPlainText(str(text))

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
            self, "Open video", "", "Video (*.mp4 *.MP4 *.avi *.mov *.mkv);;All files (*)")
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
        self.project.export.outdir = str(Path(path).parent / f"{Path(path).stem}-out")
        self._refresh_forms()
        self.log(info.describe())
        self.log(meta_io.read_telemetry(path).describe())
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
        self._load_groundtruth()
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

    def _load_groundtruth(self) -> None:
        self.groundtruth = None
        if not self.project.groundtruth.path:
            return
        try:
            from pxv.io.groundtruth import read_groundtruth

            self.groundtruth = read_groundtruth(self.project.groundtruth)
            self.log(self.groundtruth.describe())
        except Exception as exc:
            self.log(f"ground truth not loaded: {exc}")

    # -------------------------------------------------------------- frames

    def load_frames(self) -> None:
        self._apply_forms()
        if not self.project.video.path:
            return
        self.statusBar().showMessage("decoding...")
        project = self.project

        def job(progress=None):
            from pxv.pipeline import load_frames as lf

            return lf(project)

        def done(payload):
            self.frames, _, _ = payload
            self.slider.setMaximum(max(0, len(self.frames) - 1))
            self.slider.setValue(0)
            self.log(f"decoded {len(self.frames)} frames of "
                     f"{self.frames.shape[2]}x{self.frames.shape[1]}")
            self.statusBar().showMessage(f"{len(self.frames)} frames ready", 4000)
            self._redraw(reset=True)

        self._thread, self._worker = start(self, job, on_done=done, on_fail=self._fail)

    def _preprocessed(self, idx: int) -> np.ndarray:
        cfg = self.project.preprocess
        if cfg.background == "static":
            bg = static_background(self.frames, cfg.bg_stride)
        elif cfg.background == "rolling":
            bg = rolling_background(self.frames, cfg.bg_window)[idx]
        else:
            bg = None
        return preprocess_frame(self.frames[idx], cfg, bg)

    def _groundtruth_pixels(self) -> np.ndarray | None:
        if self.groundtruth is None or self.result is None:
            return None
        t = self.result.transform
        try:
            inv = np.linalg.inv(t.matrix)
        except np.linalg.LinAlgError:
            return None
        import cv2

        pts = np.column_stack([self.groundtruth.x, self.groundtruth.y]).astype(float)
        px = cv2.perspectiveTransform(pts[None], inv).reshape(-1, 2)
        h, w = self.frames.shape[1:3] if self.frames is not None else (1e9, 1e9)
        keep = (px[:, 0] > 0) & (px[:, 0] < w) & (px[:, 1] > 0) & (px[:, 1] < h)
        return px[keep]

    def _redraw(self, *_, reset: bool = False) -> None:
        if self.frames is None:
            return
        idx = min(self.slider.value(), len(self.frames) - 1)
        self.frame_label.setText(f"{idx} / {len(self.frames) - 1}")
        mode = self.mode_box.currentText()

        try:
            if mode == "streamplot" and self.result is not None:
                if self._streamplot is None:
                    self._render_streamplot(idx)
                self.viewport.show_image(self._streamplot, reset=reset)
                self.viewport.clear_overlays()
                return
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

        if mode == "speed map" and self.result is not None:
            self.viewport.show_image(img, reset=reset)
            self.viewport.image_item.setRect(
                __import__("pyqtgraph").QtCore.QRectF(0, 0, self.frames.shape[2],
                                                      self.frames.shape[1]))
        else:
            self.viewport.show_image(img, reset=reset)

        field = self.result.field if (self.result and
                                      self.overlay_vectors.currentText() == "vectors: on") else None
        self.viewport.show_vectors(field)
        self.viewport.show_groundtruth(
            self._groundtruth_pixels() if self.overlay_gt.currentText() == "ground truth: on"
            else None)
        self.viewport.show_polygons(
            self.project.analysis.include if self.overlay_mask.currentText() == "mask: on" else [])

    def _backdrop(self, idx: int) -> np.ndarray:
        """A faded colour frame reads far better under a streamplot than grey."""
        from pxv.io import video as video_io
        from pxv.preprocess.enhance import crop

        try:
            frame = video_io.read_frame(
                self.project.video.path,
                self.project.video.start + idx * self.project.video.step)
            return crop(frame, self.project.video.roi)
        except Exception:
            return self.frames[idx]

    def _render_streamplot(self, idx: int) -> None:
        from pxv.export.streamlines import render_streamlines

        backdrop = self._backdrop(idx)
        self._streamplot = render_streamlines(
            self.result, backdrop=backdrop, cmap=self.project.export.cmap,
            backdrop_alpha=self.project.export.backdrop_alpha, dpi=110,
            title=f"{self.project.name}  |  surface velocity")

    # ------------------------------------------------------------- polygon

    def _toggle_draw(self, on: bool) -> None:
        if on:
            self.viewport.begin_polygon()
            self.statusBar().showMessage(
                "click to add vertices, right-click to close the polygon", 0)
        else:
            self.viewport.cancel_polygon()
            self.statusBar().clearMessage()

    def _on_polygon(self, points: list) -> None:
        self._apply_forms()
        self.project.analysis.include.append(points)
        self._refresh_forms()
        self.btn_draw.setChecked(False)
        self.log(f"analysis polygon added with {len(points)} vertices "
                 f"({len(self.project.analysis.include)} total)")
        self._redraw()

    def _clear_polygons(self) -> None:
        self._apply_forms()
        self.project.analysis.include = []
        self._refresh_forms()
        self.log("analysis polygons cleared")
        self._redraw()

    # ------------------------------------------------------------- running

    def run_pipeline(self) -> None:
        self._apply_forms()
        if not self.project.video.path:
            QMessageBox.information(self, "pXv", "Open a video first.")
            return
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setRange(0, 0)
        self._streamplot = None
        self.log("-" * 64)
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
            self.btn_export_image.setEnabled(True)
            self.btn_export_mesh.setEnabled(True)
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.mode_box.setCurrentText("streamplot")
            self._redraw(reset=True)

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

    # ------------------------------------------------------------- exports

    def _outdir(self) -> Path:
        out = Path(self.project.export.outdir)
        out.mkdir(parents=True, exist_ok=True)
        return out

    def export_image(self) -> None:
        if self.result is None:
            return
        self._apply_forms()
        from pxv.export import streamlines

        idx = min(self.slider.value(), len(self.frames) - 1)
        out = self._outdir()
        cfg = self.project.export
        try:
            written = [streamlines.plot_streamlines(
                self.result, out / f"{self.project.name}-streamlines.jpg",
                backdrop=self._backdrop(idx), cmap=cfg.cmap,
                backdrop_alpha=cfg.backdrop_alpha, dpi=cfg.dpi,
                title=f"{self.project.name}  |  surface velocity")]
            written.append(streamlines.plot_quicklook(
                self.result, out / f"{self.project.name}-quicklook.png",
                backdrop=self._backdrop(idx), cmap=cfg.cmap))
        except Exception:
            self._fail(traceback.format_exc())
            return
        for p in written:
            self.log(f"wrote {p}")
        self.statusBar().showMessage(f"image exported to {out}", 6000)

    def export_mesh(self) -> None:
        if self.result is None:
            return
        self._apply_forms()
        from pxv.export import datasets

        out = self._outdir()
        written = []
        try:
            for fn, name in ((datasets.write_ugrid, f"{self.project.name}-mesh.nc"),
                             (datasets.write_geotiff, f"{self.project.name}-velocity.tif"),
                             (datasets.write_gpkg, f"{self.project.name}-velocity.gpkg")):
                if (p := fn(self.result, out / name)):
                    written.append(p)
        except Exception:
            self._fail(traceback.format_exc())
            return
        for p in written:
            self.log(f"wrote {p}")
        if not written:
            self.log("no mesh written: the geometry has no metric transform")
        self.statusBar().showMessage(f"mesh exported to {out}", 6000)

    # --------------------------------------------------------------- close

    def closeEvent(self, event) -> None:
        """Stop background work before the window goes away."""
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            if not self._thread.wait(5000):
                self._thread.terminate()
                self._thread.wait(1000)
        super().closeEvent(event)


def main(project_path: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow(project_path)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
