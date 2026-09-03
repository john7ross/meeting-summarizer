"""QSS theme built from the exact VS Code-style tokens of the Electron app.

Two palettes (dark/light) feed a single stylesheet template, so the native
window matches the original look and the 🌓 toggle behaves the same way.
"""
from __future__ import annotations

DARK = {
    "bg_primary": "#1e1e1e",
    "bg_secondary": "#252526",
    "bg_tertiary": "#2d2d30",
    "text_primary": "#cccccc",
    "text_secondary": "#858585",
    "accent": "#007acc",
    "accent_hover": "#005a9e",
    "border": "#3e3e42",
    "success": "#4ec9b0",
    "error": "#f48771",
    "warning": "#e0af68",
    "warning_bg": "#3a2f1c",
}

LIGHT = {
    "bg_primary": "#ffffff",
    "bg_secondary": "#f3f3f3",
    "bg_tertiary": "#e8e8e8",
    "text_primary": "#1e1e1e",
    "text_secondary": "#616161",
    "accent": "#0078d4",
    "accent_hover": "#106ebe",
    "border": "#d4d4d4",
    "success": "#107c10",
    "error": "#e81123",
    "warning": "#8a6116",
    "warning_bg": "#fdf6e3",
}

PALETTES = {"dark": DARK, "light": LIGHT}

_TEMPLATE = """
QWidget {{
    background: {bg_primary};
    color: {text_primary};
    font-family: 'Segoe UI', 'Roboto', sans-serif;
    font-size: 13px;
}}
QMainWindow, QScrollArea, #bodyContainer {{ background: {bg_primary}; }}
QToolBar {{
    background: {bg_secondary};
    border: none;
    border-bottom: 1px solid {border};
    padding: 6px 10px;
    spacing: 6px;
}}
/* Labels must never paint the window background (otherwise every caption looks
   like a borderless white button). ID-styled labels below keep their own bg. */
QLabel {{ background: transparent; }}
QCheckBox, QRadioButton {{ background: transparent; }}
#appTitle {{ font-size: 18px; font-weight: 600; color: {accent}; }}
QLabel#sectionTitle {{ font-size: 14px; font-weight: 600; color: {text_primary}; }}
QLabel#hint, QLabel#statusDetails {{ color: {text_secondary}; }}
QLabel#warning {{
    color: {warning};
    background: {warning_bg};
    border: 1px solid {warning};
    border-radius: 6px;
    padding: 8px 10px;
}}
QLabel#deviceIndicator {{ color: {text_secondary}; font-size: 12px; padding: 0 10px; }}
QFrame#section {{
    background: {bg_secondary};
    border: 1px solid {border};
    border-radius: 8px;
}}
QFrame#dropZone {{
    background: {bg_tertiary};
    border: 2px dashed {border};
    border-radius: 8px;
}}
QPushButton {{
    background: {bg_tertiary};
    color: {text_primary};
    border: 1px solid {border};
    padding: 7px 14px;
    border-radius: 6px;
}}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:disabled {{ color: {text_secondary}; }}
QPushButton[variant="primary"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                               stop:0 {accent}, stop:1 {accent_hover});
    color: #ffffff;
    border: none;
    padding: 8px 18px;
    font-weight: 600;
}}
QPushButton[variant="primary"]:disabled {{ background: {bg_tertiary}; }}
QPushButton[variant="icon"] {{
    background: transparent;
    border: 1px solid {border};
    padding: 5px 8px;
    font-size: 15px;
}}
QPushButton[variant="icon"]:hover {{ background: {bg_tertiary}; border-color: {accent}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
    background: {bg_primary};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 6px;
    selection-background-color: {accent};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {accent}; }}
/* A bare QWidget used only as a toolbar spacer must not paint the primary
   background (otherwise it shows as a white bar over the toolbar). */
#toolbarSpacer {{ background: transparent; border: none; }}
/* Checkbox indicator — without this the box has no visible border in light mode. */
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {border};
    border-radius: 4px;
    background: {bg_primary};
}}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
/* SpinBox steppers — styling the QSpinBox border disables the native buttons,
   so the up/down controls must be re-drawn explicitly or they vanish. */
QSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border;
    width: 18px;
    background: {bg_tertiary};
    border-left: 1px solid {border};
}}
QSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 6px; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 6px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {accent}; }}
QSpinBox::up-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid {text_primary};
}}
QSpinBox::down-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {text_primary};
}}
/* ComboBox arrow + popup list (popup was relying on the generic bg). */
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {text_primary};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {bg_primary};
    color: {text_primary};
    border: 1px solid {border};
    selection-background-color: {accent};
    selection-color: #ffffff;
    outline: none;
}}
/* Tab bar (Diagnostics) — make every tab clearly visible, not just the first. */
QTabWidget::pane {{ border: 1px solid {border}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{
    background: {bg_secondary};
    color: {text_secondary};
    border: 1px solid {border};
    padding: 7px 14px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{ background: {bg_primary}; color: {text_primary}; border-bottom-color: {bg_primary}; }}
QTabBar::tab:hover {{ color: {text_primary}; }}
QTableWidget {{
    background: {bg_primary};
    border: 1px solid {border};
    border-radius: 8px;
    gridline-color: {border};
}}
QHeaderView::section {{
    background: {bg_tertiary};
    color: {text_secondary};
    border: none;
    border-bottom: 1px solid {border};
    padding: 6px;
}}
QTableWidget::item:selected {{ background: {accent}; color: #ffffff; }}
QProgressBar {{
    background: {bg_tertiary};
    border: 1px solid {border};
    border-radius: 6px;
    height: 14px;
    text-align: center;
    color: {text_primary};
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                               stop:0 {accent}, stop:1 {success});
    border-radius: 5px;
}}
QScrollBar:vertical {{ background: {bg_secondary}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 6px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QGroupBox#analysisPanel {{
    background: {bg_secondary};
    border: 1px solid {border};
    border-radius: 8px;
    margin-top: 8px;
    padding: 8px 4px 8px 4px;
    font-size: 13px;
    font-weight: 600;
    color: {text_primary};
}}
QGroupBox#analysisPanel::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    color: {text_primary};
}}
QFrame#analysisCard {{
    background: {bg_tertiary};
    border: 1px solid {border};
    border-radius: 6px;
}}
QFrame#quoteCard {{
    background: {bg_tertiary};
    border-left: 3px solid {accent};
    border-radius: 0 6px 6px 0;
}}
QLabel#analysisCardTitle {{ font-weight: 600; color: {text_primary}; }}
QLabel#quoteText {{ font-style: italic; color: {text_primary}; }}
QLabel#quoteAuthor {{ color: {accent}; font-weight: 600; }}
QLabel#categoryBadge {{
    background: {accent};
    color: #ffffff;
    padding: 4px 12px;
    border-radius: 14px;
    font-weight: 600;
    font-size: 13px;
}}
QLabel#categoryTag {{
    background: {bg_tertiary};
    border: 1px solid {border};
    color: {text_primary};
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 12px;
}}
QLabel#techContext {{
    background: {bg_tertiary};
    border: 1px solid {border};
    color: {text_secondary};
    padding: 1px 6px;
    border-radius: 8px;
    font-size: 11px;
}}
QLabel#protocolText {{
    font-family: 'Consolas', monospace;
    font-size: 12px;
    color: {text_secondary};
    background: {bg_tertiary};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 8px;
}}
QFrame#analysisSep {{
    color: {border};
    max-height: 1px;
}}
QFrame#speakerCard {{
    background: {bg_secondary};
    border: 1px solid {border};
    border-radius: 8px;
}}
QLineEdit#speakerNameEdit {{
    background: {bg_primary};
    border: 1px solid {accent};
    border-radius: 6px;
    padding: 5px 8px;
    font-weight: 600;
}}
QLabel#utteranceTs {{
    color: {text_secondary};
    font-family: "Consolas", monospace;
    font-size: 11px;
}}
QPlainTextEdit#utteranceText {{
    background: {bg_primary};
    border: 1px solid {border};
    border-radius: 5px;
    padding: 3px 6px;
    font-size: 12px;
}}
QPlainTextEdit#utteranceText:focus {{ border-color: {accent}; }}
QPushButton#verNav {{
    background: {bg_secondary};
    border: 1px solid {border};
    border-radius: 5px;
    padding: 2px 4px;
    font-size: 11px;
}}
QPushButton#verNav:hover:enabled {{ border-color: {accent}; }}
QPushButton#verNav:disabled {{ color: {text_secondary}; }}
QComboBox#verCombo {{
    background: {bg_secondary};
    border: 1px solid {border};
    border-radius: 5px;
    padding: 2px 8px;
    min-width: 90px;
    font-size: 12px;
}}
QLineEdit#projectEdit {{
    background: {bg_primary};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 4px 8px;
}}
QLineEdit#projectEdit:focus {{ border-color: {accent}; }}
/* Drop-down menus (Regenerate: summary / analysis / both). Without this the
   popup inherits the plain window background and has no hover state at all. */
QMenu {{
    background: {bg_secondary};
    color: {text_primary};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {accent}; color: #ffffff; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 8px; }}
QPushButton::menu-indicator {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    right: 6px;
    width: 8px;
}}
/* Room for that indicator, so the arrow never sits on the label. */
QPushButton#regenButton {{ padding-right: 26px; }}
QTextBrowser#searchResults, QTextBrowser#ragResults,
QTextBrowser#ragLibrary, QTextBrowser#ragStats, QTextBrowser#runDetails {{
    background: {bg_primary};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 8px;
}}
QTextBrowser#searchResults mark, QTextBrowser#ragResults mark {{
    background: {accent};
    color: #ffffff;
}}
"""


def build_stylesheet(theme: str = "dark") -> str:
    palette = PALETTES.get(theme, DARK)
    return _TEMPLATE.format(**palette)


def cap_combo_width(combo, chars: int = 24, popup_width: int = 520) -> None:
    """Stop a combo box from sizing itself to its longest item.

    Qt makes a QComboBox as wide as the widest entry. When the entries carry user
    content - a meeting file name, an agent command line - a single long one
    silently widens the whole dialog: the settings form once demanded 2662 px and
    therefore ALWAYS showed a horizontal scrollbar, and the diagnostics tabs 2380.
    The popup may still be wide (it floats above the window); only the closed
    control is capped, and its text elides.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QComboBox
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(chars)
    view = combo.view()
    if view is not None:
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        view.setMinimumWidth(popup_width)


def fit_combo(combo, extra: int = 44, cap: int = 320) -> None:
    """Widen a combo box so its OWN items are never cut off.

    The counterpart of :func:`cap_combo_width`, for the fixed short lists the app
    writes itself. Qt caches a combo's size hint from the font in force when it is
    built, and the stylesheet lands afterwards and enlarges it - which is how the
    export selector shipped reading "Транскри" instead of "Транскрипция". Measure
    every item with the widget's real metrics, and measure again after the style
    has been polished. ``extra`` covers the drop-down arrow and frame padding.
    """
    from PySide6.QtCore import QTimer

    def _apply() -> None:
        texts = [combo.itemText(i) for i in range(combo.count())]
        if not texts:
            return
        metrics = combo.fontMetrics()
        needed = min(cap, max(metrics.horizontalAdvance(t) for t in texts) + extra)
        if combo.minimumWidth() < needed:
            combo.setMinimumWidth(needed)

    def _later() -> None:
        # The widget may be gone by the time this fires - a dialog closed straight
        # after construction is enough. Touching a deleted C++ object crashes the
        # process, so a destroyed target simply means there is nothing to measure.
        try:
            _apply()
        except RuntimeError:
            pass

    _apply()
    QTimer.singleShot(0, _later)


def fit_placeholder(field, extra: int = 28, cap: int = 520) -> None:
    """Widen a line edit so its own placeholder is never cut off.

    Hard-coded maximum widths were tuned against one font: at any larger UI font
    (or display scaling) the hint became "Проект (необяз…". Measure instead.
    """
    from PySide6.QtCore import QTimer

    def _apply() -> None:
        text = field.placeholderText()
        if not text:
            return
        # The stylesheet lands AFTER construction and usually enlarges the font,
        # so a width measured here and never revisited is measured against the
        # wrong metrics. Re-apply once the style has been polished.
        needed = min(cap, field.fontMetrics().horizontalAdvance(text) + extra)
        if field.minimumWidth() < needed:
            field.setMinimumWidth(needed)
        if field.maximumWidth() < needed:
            field.setMaximumWidth(max(needed, cap))

    def _later() -> None:
        # The widget may be gone by the time this fires - a dialog closed straight
        # after construction is enough. Touching a deleted C++ object crashes the
        # process, so a destroyed target simply means there is nothing to measure.
        try:
            _apply()
        except RuntimeError:
            pass

    _apply()
    QTimer.singleShot(0, _later)
