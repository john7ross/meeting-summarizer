"""Processing-profile timeline (Diagnostics → Processing profile).

A Gantt-style timeline: every stage and sub-step gets its OWN row, ordered by
start time, its bar positioned/sized by when it ran and how long it took. Unlike a
nested flame graph, there are no empty rows under leaf stages — for a sequential
pipeline (unload → extract → chunks → summary → analysis features) this reads as a
clean top-to-bottom schedule. A time ruler runs across the top; short bars get
their label drawn beside them so nothing is unreadable.

Fed by ``core.trace.layout`` (bars carry ``offset``/``width``/``depth``/``ms``).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

# Distinct, readable-on-dark fills; picked per span-name so a stage keeps its
# colour across renders. Text is drawn dark for contrast on these.
_PALETTE = ["#4e79a7", "#59a14f", "#e15759", "#f28e2b", "#76b7b2",
            "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#86bcb6"]


def _colour(name: str) -> QColor:
    return QColor(_PALETTE[(hash(name) & 0x7FFFFFFF) % len(_PALETTE)])


def _fmt(ms: float) -> str:
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"


class FlameGraphWidget(QWidget):
    ROW_H = 26
    AXIS_H = 20     # top strip for the time ruler
    GAP = 2
    TICKS = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._total_ms = 1.0
        self.setMouseTracking(True)
        self.setMinimumHeight(self.AXIS_H + self.ROW_H)

    def set_layout(self, lay) -> None:
        bars = list(lay.get("bars", [])) if lay else []
        # One row per span, top-to-bottom by start time; on ties the longer
        # (parent) span comes first, so a stage sits just above its sub-steps.
        self._rows = sorted(bars, key=lambda b: (b["offset"], -b["width"]))
        self._total_ms = (lay.get("total_ms") or 1.0) if lay else 1.0
        self.setMinimumHeight(self.AXIS_H + len(self._rows) * self.ROW_H + 2 * self.GAP)
        self.update()

    def _rect_for(self, i: int, b: dict) -> QRectF:
        w = float(self.width())
        x = b["offset"] * w
        bw = max(2.0, b["width"] * w)
        y = self.AXIS_H + i * self.ROW_H + self.GAP
        return QRectF(x, y, bw, self.ROW_H - 2 * self.GAP)

    def _paint_axis(self, p: QPainter, fm: QFontMetrics) -> None:
        """Time ruler + gridlines so bars can be read against wall-clock time."""
        w = float(self.width())
        h = self.height()
        total_s = self._total_ms / 1000.0
        for k in range(self.TICKS + 1):
            frac = k / self.TICKS
            x = frac * w
            p.setPen(QColor(255, 255, 255, 22))     # faint gridline down the graph
            p.drawLine(int(x), self.AXIS_H, int(x), h)
            p.setPen(QColor(150, 150, 150))
            label = _fmt(total_s * frac * 1000.0)
            tw = fm.horizontalAdvance(label)
            tx = 2 if k == 0 else (w - tw - 2 if k == self.TICKS else x - tw / 2)
            p.drawText(QRectF(tx, 0, tw + 2, self.AXIS_H),
                       Qt.AlignVCenter | Qt.AlignLeft, label)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        fm = QFontMetrics(self.font())
        if self._rows:
            self._paint_axis(p, fm)
        w = float(self.width())
        for i, b in enumerate(self._rows):
            r = self._rect_for(i, b)
            p.fillRect(r, _colour(b["name"]))
            p.setPen(QColor(0, 0, 0, 60))
            p.drawRect(r)
            text = f'{b["name"]}  {_fmt(b["ms"])}'
            tw = fm.horizontalAdvance(text)
            if r.width() >= tw + 10:                 # label fits inside the bar
                p.setPen(QColor("#10161c"))
                p.drawText(r.adjusted(5, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
            else:                                    # short bar → label to its right
                p.setPen(QColor(210, 210, 210))
                lx = r.right() + 5
                avail = int(w - lx - 4)
                if avail > 20:
                    p.drawText(QRectF(lx, r.top(), avail, r.height()),
                               Qt.AlignVCenter | Qt.AlignLeft,
                               fm.elidedText(text, Qt.ElideRight, avail))
        p.end()

    def _hit(self, pos) -> "dict | None":
        for i, b in enumerate(self._rows):
            r = self._rect_for(i, b)
            # match the bar or its to-the-right label band (same row)
            if r.top() <= pos.y() <= r.bottom() and pos.x() >= r.left():
                return b
        return None

    def mouseMoveEvent(self, event) -> None:
        b = self._hit(event.position())
        if b is None:
            self.setToolTip("")
            return
        share = 100 * b["ms"] / self._total_ms if self._total_ms else 0
        self.setToolTip(f'{b["name"]} — {b["ms"] / 1000:.2f}s ({share:.1f}%)')
