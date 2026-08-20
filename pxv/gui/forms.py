"""Build parameter forms straight from the project dataclasses.

Generating the widgets from the schema means the GUI can never drift out of sync
with what a project file accepts, and a new parameter appears in both places at
once.
"""

from __future__ import annotations

import dataclasses
import typing

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
    QSpinBox, QWidget,
)


def _literal_options(tp) -> list[str] | None:
    if typing.get_origin(tp) is typing.Literal:
        return [str(a) for a in typing.get_args(tp)]
    return None


class SectionForm(QWidget):
    """A form for one dataclass section of the project."""

    def __init__(self, obj, title: str = "", parent=None):
        super().__init__(parent)
        self.obj = obj
        self.widgets: dict[str, QWidget] = {}
        layout = QFormLayout(self)
        layout.setLabelAlignment(layout.labelAlignment())

        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            tip = (f.metadata or {}).get("doc", "")
            w = self._make_widget(f, value)
            if w is None:
                continue
            self.widgets[f.name] = w
            label = QLabel(f.name.replace("_", " "))
            if tip:
                label.setToolTip(tip)
                w.setToolTip(tip)
            layout.addRow(label, w)

    def _make_widget(self, f, value):
        options = _literal_options(f.type)
        if options:
            w = QComboBox()
            w.addItems(options)
            w.setCurrentText(str(value))
            return w
        if isinstance(value, bool):
            w = QCheckBox()
            w.setChecked(value)
            return w
        if isinstance(value, int):
            w = QSpinBox()
            w.setRange(-10_000_000, 10_000_000)
            w.setValue(value)
            return w
        if isinstance(value, float):
            w = QDoubleSpinBox()
            w.setRange(-1e9, 1e9)
            w.setDecimals(4)
            w.setSingleStep(0.1)
            w.setValue(value)
            return w
        if isinstance(value, (list, str)):
            w = QLineEdit()
            w.setText(_to_text(value))
            return w
        return None

    def apply(self) -> None:
        """Write the widget values back onto the dataclass."""
        for f in dataclasses.fields(self.obj):
            w = self.widgets.get(f.name)
            if w is None:
                continue
            current = getattr(self.obj, f.name)
            if isinstance(w, QComboBox):
                setattr(self.obj, f.name, w.currentText())
            elif isinstance(w, QCheckBox):
                setattr(self.obj, f.name, w.isChecked())
            elif isinstance(w, QSpinBox):
                setattr(self.obj, f.name, int(w.value()))
            elif isinstance(w, QDoubleSpinBox):
                setattr(self.obj, f.name, float(w.value()))
            elif isinstance(w, QLineEdit):
                setattr(self.obj, f.name, _from_text(w.text(), current))

    def refresh(self) -> None:
        """Pull values from the dataclass back into the widgets."""
        for f in dataclasses.fields(self.obj):
            w = self.widgets.get(f.name)
            if w is None:
                continue
            value = getattr(self.obj, f.name)
            if isinstance(w, QComboBox):
                w.setCurrentText(str(value))
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(value))
            elif isinstance(w, QSpinBox):
                w.setValue(int(value))
            elif isinstance(w, QDoubleSpinBox):
                w.setValue(float(value))
            elif isinstance(w, QLineEdit):
                w.setText(_to_text(value))


def _to_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(_to_text(v) for v in value)
    return str(value)


def _from_text(text: str, like):
    if isinstance(like, str):
        return text
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in text.replace("[", "").replace("]", "").split(",") if p.strip()]
    if like and isinstance(like[0], list):
        # nested list, e.g. a polygon: expect flat pairs
        flat = [float(p) for p in parts]
        return [[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)]
    out = []
    for p in parts:
        try:
            out.append(int(p) if float(p).is_integer() and "." not in p else float(p))
        except ValueError:
            out.append(p)
    return out
