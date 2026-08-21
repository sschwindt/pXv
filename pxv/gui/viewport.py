"""The central viewport.

This is the element the whole interface is built around: one large view that
shows the footage, the derived field, and the streamplot that will be exported.
The streamplot is rendered by the same code that writes the file, so what is on
screen is what gets exported.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout

MODES = ("video", "preprocessed", "speed map", "streamplot")


class Viewport(QWidget):
    """Image view with vector, ground-truth and polygon overlays."""

    polygon_finished = Signal(list)
    point_clicked = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget()
        self.plot.setAspectLocked(True)
        self.plot.invertY(True)
        self.plot.hideAxis("left")
        self.plot.hideAxis("bottom")
        self.plot.setBackground("#101418")
        layout.addWidget(self.plot)

        self.image_item = pg.ImageItem()
        self.plot.addItem(self.image_item)

        self.vector_item = pg.PlotDataItem(pen=pg.mkPen((255, 205, 70), width=1.4))
        self.plot.addItem(self.vector_item)

        self.gt_item = pg.ScatterPlotItem(size=13, pen=pg.mkPen((255, 255, 255), width=2),
                                          brush=pg.mkBrush(230, 60, 50, 200))
        self.plot.addItem(self.gt_item)

        self.mask_item = pg.PlotDataItem(pen=pg.mkPen((90, 220, 255), width=2))
        self.plot.addItem(self.mask_item)

        self.draft_item = pg.PlotDataItem(pen=pg.mkPen((255, 255, 0), width=2,
                                                       style=Qt.DashLine))
        self.plot.addItem(self.draft_item)
        self.draft_points = pg.ScatterPlotItem(size=9, brush=pg.mkBrush(255, 255, 0, 220))
        self.plot.addItem(self.draft_points)

        self._drawing = False
        self._draft: list[list[float]] = []
        self.plot.scene().sigMouseClicked.connect(self._on_click)

    # ------------------------------------------------------------- drawing

    @property
    def drawing(self) -> bool:
        return self._drawing

    def begin_polygon(self) -> None:
        self._drawing = True
        self._draft = []
        self._refresh_draft()

    def cancel_polygon(self) -> None:
        self._drawing = False
        self._draft = []
        self._refresh_draft()

    def finish_polygon(self) -> None:
        if len(self._draft) >= 3:
            self.polygon_finished.emit([list(p) for p in self._draft])
        self.cancel_polygon()

    def _on_click(self, event) -> None:
        point = self.plot.plotItem.vb.mapSceneToView(event.scenePos())
        x, y = float(point.x()), float(point.y())
        if not self._drawing:
            self.point_clicked.emit(x, y)
            return
        if event.button() == Qt.RightButton:
            self.finish_polygon()
            return
        self._draft.append([round(x, 1), round(y, 1)])
        self._refresh_draft()

    def _refresh_draft(self) -> None:
        if not self._draft:
            self.draft_item.setData([], [])
            self.draft_points.setData([], [])
            return
        pts = np.asarray(self._draft, float)
        closed = np.vstack([pts, pts[:1]]) if len(pts) > 2 else pts
        self.draft_item.setData(closed[:, 0], closed[:, 1])
        self.draft_points.setData(pts[:, 0], pts[:, 1])

    # -------------------------------------------------------------- layers

    def show_image(self, img: np.ndarray, reset: bool = False) -> None:
        if img.ndim == 3:
            self.image_item.setImage(np.transpose(img, (0, 1, 2)), autoLevels=False,
                                     levels=(0, 255))
        else:
            self.image_item.setImage(img, autoLevels=True)
        h, w = img.shape[:2]
        self.image_item.setRect(pg.QtCore.QRectF(0, 0, w, h))
        if reset:
            self.plot.autoRange()

    def show_vectors(self, field, scale: float = 8.0, max_arrows: int = 40) -> None:
        if field is None:
            self.vector_item.setData([], [])
            return
        step = max(1, int(np.ceil(max(field.u.shape) / max_arrows)))
        sel = field.valid[::step, ::step]
        if not sel.any():
            self.vector_item.setData([], [])
            return
        x = field.x[::step, ::step][sel]
        y = field.y[::step, ::step][sel]
        u = field.u[::step, ::step][sel]
        v = field.v[::step, ::step][sel]
        xs = np.empty(len(x) * 3)
        ys = np.empty(len(y) * 3)
        xs[0::3], xs[1::3], xs[2::3] = x, x + scale * u, np.nan
        ys[0::3], ys[1::3], ys[2::3] = y, y + scale * v, np.nan
        self.vector_item.setData(xs, ys, connect="finite")

    def show_groundtruth(self, xy: np.ndarray | None) -> None:
        if xy is None or len(xy) == 0:
            self.gt_item.setData([], [])
            return
        self.gt_item.setData(xy[:, 0], xy[:, 1])

    def show_polygons(self, polygons: list) -> None:
        xs: list[float] = []
        ys: list[float] = []
        for poly in polygons or []:
            if len(poly) < 3:
                continue
            pts = np.asarray(poly, float)
            xs.extend(pts[:, 0].tolist() + [pts[0, 0], np.nan])
            ys.extend(pts[:, 1].tolist() + [pts[0, 1], np.nan])
        self.mask_item.setData(xs, ys, connect="finite")

    def clear_overlays(self) -> None:
        self.vector_item.setData([], [])
        self.gt_item.setData([], [])
        self.mask_item.setData([], [])
