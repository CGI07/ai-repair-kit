"""Theme tokens, Qt palette, and widget styling."""

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

COLORS = {
    "light": {
        "canvas": "#f4f6fa", "surface": "#ffffff", "nav": "#edf1f7",
        "text": "#172438", "muted": "#4d5d75", "border": "#d5dde9",
        "accent": "#2449a3", "tint": "#e6edff", "hover": "#e0e7f2",
        "disabled": "#59677e", "disabled_bg": "#edf1f7",
        "notice": "#fff4d7", "notice_text": "#644500",
    },
    "dark": {
        "canvas": "#141b28", "surface": "#1c2636", "nav": "#17202f",
        "text": "#edf2fb", "muted": "#b8c6dd", "border": "#3b4b65",
        "accent": "#a9c1ff", "tint": "#263b68", "hover": "#253349",
        "disabled": "#a9b8d2", "disabled_bg": "#28354a",
        "notice": "#402e16", "notice_text": "#ffdc94",
    },
}

# Original line icons. The same geometry works with both palettes.
NAV_PATHS = (
    "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    "M3 12h4l3-7 4 14 3-7h4",
    "M4 8h16v12H4z M3 4h18v4H3z M10 12h4",
    "M4 6h16 M4 12h16 M4 18h16 M8 4v4 M15 10v4 M10 16v4",
    "M12 7h.01 M12 11v6 M21 12a9 9 0 1 1-18 0 9 9 0 1 1 18 0z",
)


def nav_icons(theme: str, pixel_ratio: float) -> list[QIcon]:
    color = COLORS[theme]["accent"]
    icons = []
    for path in NAV_PATHS:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        )
        renderer = QSvgRenderer(QByteArray(svg.encode("ascii")))
        pixels = round(20 * pixel_ratio)
        pixmap = QPixmap(pixels, pixels)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(pixel_ratio)
        icons.append(QIcon(pixmap))
    return icons


def apply_theme(app: QApplication, theme: str) -> None:
    c = COLORS[theme]
    palette = app.style().standardPalette()
    role = QPalette.ColorRole
    palette.setColor(role.Window, QColor(c["canvas"]))
    palette.setColor(role.Base, QColor(c["surface"]))
    palette.setColor(role.Button, QColor(c["surface"]))
    palette.setColor(role.PlaceholderText, QColor(c["muted"]))
    for text_role in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(text_role, QColor(c["text"]))
        palette.setColor(QPalette.ColorGroup.Disabled, text_role, QColor(c["disabled"]))
    palette.setColor(role.Highlight, QColor(c["accent"]))
    palette.setColor(role.HighlightedText, QColor(c["canvas"]))
    palette.setColor(role.ToolTipBase, QColor(c["surface"]))
    palette.setColor(role.ToolTipText, QColor(c["text"]))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QLabel {{ background: transparent; }}
        QLabel#brand {{ font-size: 18px; font-weight: 700; }}
        QLabel#pageTitle {{ font-size: 30px; font-weight: 700; }}
        QLabel#sectionTitle {{ font-size: 17px; font-weight: 700; }}
        QLabel#muted {{ color: {c["muted"]}; }}
        QLabel#badge {{ background: {c["tint"]}; color: {c["accent"]};
                        border-radius: 8px; padding: 4px 9px; font-weight: 700; }}
        QLabel#status {{ color: {c["muted"]}; padding: 7px 9px;
                         border-top: 1px solid {c["border"]}; }}
        QLabel#notice {{ color: {c["notice_text"]}; background: {c["notice"]};
                         border-radius: 8px; padding: 10px 12px; }}
        QFrame#sidebar {{ background: {c["nav"]}; border-radius: 14px; }}
        QFrame#card {{ background: {c["surface"]}; border: 1px solid {c["border"]};
                       border-radius: 12px; }}
        QFrame#hero {{ background: {c["tint"]}; border: 1px solid {c["border"]};
                       border-radius: 12px; }}
        QScrollArea, QWidget#page {{ background: {c["canvas"]}; border: none; }}
        QListWidget {{ background: transparent; border: none; outline: none; }}
        QListWidget::item {{ padding: 10px 8px; border: 2px solid transparent;
                            border-radius: 8px; margin: 2px 0; }}
        QListWidget::item:hover {{ background: {c["hover"]}; }}
        QListWidget::item:selected {{ background: {c["tint"]}; color: {c["accent"]}; }}
        QListWidget::item:selected:active:focus {{ border-color: {c["accent"]}; }}
        QPushButton, QComboBox {{
            background: {c["surface"]}; color: {c["text"]};
            border: 2px solid {c["border"]}; border-radius: 8px; padding: 6px 10px;
        }}
        QLineEdit {{
            background: {c["surface"]}; color: {c["text"]};
            border: 1px solid {c["border"]}; border-radius: 6px; padding: 5px 8px;
        }}
        QLineEdit:focus {{ border-color: {c["accent"]}; }}
        QLineEdit:disabled {{ background: {c["disabled_bg"]}; color: {c["disabled"]}; }}
        QPushButton:hover, QComboBox:hover {{ background: {c["hover"]}; }}
        QPushButton:focus, QComboBox:focus {{ border-color: {c["accent"]}; }}
        QPushButton:disabled {{ background: {c["disabled_bg"]}; color: {c["disabled"]}; }}
    """)
