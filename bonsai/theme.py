"""Colours, stylesheet, and how rendered documents are formatted."""

from PyQt6.QtGui import (
    QColor, QTextCursor, QTextFrameFormat, QTextTable,
)


# Two palettes, same keys, so every rule below is written once. Dark is the intended
# look; light is a straight inversion for daylight use.
THEMES = {
    "dark": {
        "bg": "#161618", "surface": "#1e1e20", "raised": "#2a2a2e",
        "border": "#313136", "text": "#e8e8ea", "muted": "#8b8b93",
        "faint": "#6a6a72", "accent": "#3b6ef6", "on_accent": "#ffffff",
        "hover": "#26262a", "field": "#232327",
    },
    "light": {
        "bg": "#ececee", "surface": "#ffffff", "raised": "#f3f3f6",
        "border": "#dcdce0", "text": "#1b1b1d", "muted": "#6c6c74",
        "faint": "#8b8b93", "accent": "#3b6ef6", "on_accent": "#ffffff",
        "hover": "#e4e4e8", "field": "#f7f7f9",
    },
}


NOTE_COLOURS = {"orange": "#d9a441", "red": "#e0655a"}


def stylesheet(dark=True):
    c = THEMES["dark" if dark else "light"]
    return f"""
QWidget {{
    background: {c['surface']}; color: {c['text']};
    font-family: "Inter", "SF Pro Text", "Segoe UI", "Cantarell", sans-serif;
    font-size: 13px;
}}
QToolTip {{ background: {c['raised']}; color: {c['text']};
            border: 1px solid {c['border']}; padding: 4px 6px; }}

#sidebar {{ background: {c['bg']}; border-right: 1px solid {c['border']}; }}
#sidebar QWidget {{ background: transparent; }}
#appName {{ font-size: 14px; font-weight: 600; padding: 2px; }}
#sectionLabel {{ color: {c['faint']}; font-size: 10px; font-weight: 700;
                 letter-spacing: 0.9px; padding: 2px; }}

#branchButton {{ background: transparent; color: {c['faint']}; border: none;
                 font-size: 13px; padding: 0; }}
#branchButton:hover {{ background: {c['raised']}; color: {c['text']};
                       border-radius: 11px; }}

#chatList {{ background: transparent; border: none; outline: none; }}
#chatList::item {{ color: {c['muted']}; padding: 7px 9px; border-radius: 7px; }}
#chatList::item:hover {{ background: {c['hover']}; color: {c['text']}; }}
#chatList::item:selected {{ background: {c['accent']}; color: {c['on_accent']}; }}

#messageArea {{ background: {c['surface']}; border: none; }}
#messageBody {{ background: {c['surface']}; }}
#userBubble {{ background: {c['raised']}; border: 1px solid {c['border']};
               border-radius: 12px; }}
#userBubble QLabel {{ background: transparent; }}
#assistantBlock, #assistantBlock QLabel {{ background: transparent; }}
#markdownView {{ background: transparent; border: none; color: {c['text']}; }}
#speaker {{ color: {c['faint']}; font-size: 11px; font-weight: 600;
            letter-spacing: 0.4px; }}
#note {{ color: {c['faint']}; font-size: 12px; background: transparent; }}

#previewView {{ background: {c['bg']}; border: 1px solid {c['border']};
                border-radius: 8px; }}

#attachBar {{ background: transparent; }}
#attachChip {{ background: {c['raised']}; border: 1px solid {c['border']};
               border-radius: 13px; }}
#attachName {{ background: transparent; color: {c['text']}; font-size: 12px; }}
#attachDrop {{ background: transparent; color: {c['muted']}; border: none;
               border-radius: 9px; font-size: 14px; padding: 0; }}
#attachDrop:hover {{ background: {c['accent']}; color: {c['on_accent']}; }}

#skillDrawer {{ background: {c['raised']}; border: 1px solid {c['border']};
                border-radius: 10px; }}
#skillDrawer QWidget {{ background: transparent; }}
#skillList {{ background: transparent; border: none; outline: none; }}
#skillList::item {{ color: {c['muted']}; padding: 6px 9px; border-radius: 7px; }}
#skillList::item:hover {{ background: {c['hover']}; color: {c['text']}; }}
#skillList::item:selected {{ background: {c['accent']}; color: {c['on_accent']}; }}

#briefingArea {{ background: {c['surface']}; border: none; }}
#briefingBody {{ background: {c['surface']}; }}
#briefCard {{ background: {c['raised']}; border: 1px solid {c['border']};
              border-radius: 10px; }}
#briefCard QLabel {{ background: transparent; }}
#briefTitle {{ font-size: 13px; font-weight: 600; }}
#briefMeta {{ color: {c['faint']}; font-size: 10px; }}
#briefSummary {{ color: {c['muted']}; font-size: 12px; }}
#briefTopic {{ font-size: 12px; font-weight: 700; color: {c['faint']};
               letter-spacing: 0.6px; }}
#kindTag {{ color: {c['accent']}; font-size: 10px; font-weight: 700;
            letter-spacing: 0.5px; }}
#notesPane {{ background: {c['bg']}; border-left: 1px solid {c['border']}; }}
#notesPane QWidget {{ background: transparent; }}
#notesEditor {{ background: {c['field']}; border: 1px solid {c['border']};
                border-radius: 10px; padding: 8px; color: {c['text']};
                font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
                font-size: 12px; }}
#notesPath {{ color: {c['faint']}; font-size: 10px; }}
#sideTabs::pane {{ border: none; background: transparent; }}
#sideTabs QTabBar::tab {{ background: transparent; color: {c['faint']};
                          padding: 5px 10px; margin-right: 2px;
                          border-radius: 7px; font-size: 11px; font-weight: 600; }}
#sideTabs QTabBar::tab:hover {{ color: {c['text']}; background: {c['hover']}; }}
#sideTabs QTabBar::tab:selected {{ color: {c['on_accent']}; background: {c['accent']}; }}
#taskList {{ background: {c['field']}; border: 1px solid {c['border']};
             border-radius: 10px; outline: none; padding: 4px; }}
#taskList::item {{ color: {c['text']}; padding: 4px 2px; border-radius: 5px; }}
#taskList::item:hover {{ background: {c['hover']}; }}
#taskList::item:selected {{ background: {c['raised']}; color: {c['text']}; }}
#topBar {{ background: {c['surface']}; border-bottom: 1px solid {c['border']}; }}
#topBar QWidget {{ background: transparent; }}
#chatTitle {{ font-size: 13px; font-weight: 600; }}
#statusText, #contextText {{ color: {c['faint']}; font-size: 11px; }}
/* The size is set here as well as on the widget: a QWidget font-size rule in this
   sheet overrides setFont(), so the two have to agree or the label is measured at one
   size and drawn at another. */
#bonsaiGrowth {{ color: {c['muted']}; background: transparent;
                 font-family: "DejaVu Sans Mono", monospace; font-size: 9px; }}

#toggleStrip {{ background: {c['surface']}; border-top: 1px solid {c['border']}; }}
#toggleStrip QWidget {{ background: transparent; }}

#composer {{ background: {c['field']}; border: 1px solid {c['border']};
             border-radius: 14px; }}
#composer QWidget {{ background: transparent; }}
#composer QTextEdit {{ background: transparent; border: none;
                       color: {c['text']}; font-size: 13px; }}

QLineEdit {{ background: {c['field']}; border: 1px solid {c['border']};
             border-radius: 8px; padding: 6px 9px; color: {c['text']}; }}
QLineEdit:focus {{ border: 1px solid {c['accent']}; }}

QPushButton {{ background: {c['raised']}; color: {c['text']};
               border: 1px solid {c['border']}; border-radius: 8px;
               padding: 6px 12px; }}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:disabled {{ color: {c['faint']}; background: transparent; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['on_accent']};
                       border: 1px solid {c['accent']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: #4a7bf8; }}
QPushButton#primary:disabled {{ background: {c['raised']}; color: {c['faint']};
                                border: 1px solid {c['border']}; }}
QPushButton#ghost {{ background: transparent; border: none; color: {c['muted']};
                     padding: 5px 8px; }}
QPushButton#ghost:hover {{ background: {c['hover']}; color: {c['text']}; }}

QCheckBox {{ color: {c['muted']}; spacing: 6px; font-size: 12px; }}
QCheckBox:hover {{ color: {c['text']}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px;
                        border: 1px solid {c['border']}; background: {c['field']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']};
                                border: 1px solid {c['accent']}; }}

QComboBox {{ background: {c['field']}; border: 1px solid {c['border']};
             border-radius: 8px; padding: 5px 8px; color: {c['text']}; }}
QComboBox QAbstractItemView {{ background: {c['raised']}; color: {c['text']};
                               border: 1px solid {c['border']};
                               selection-background-color: {c['accent']}; }}
QSpinBox, QDoubleSpinBox {{ background: {c['field']}; color: {c['text']};
                            border: 1px solid {c['border']}; border-radius: 8px;
                            padding: 4px 6px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px;
                               min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def markdown_css(c):
    """Styling for a rendered reply. Qt rich text takes a small CSS subset - no
    border-radius, no flexbox - so this stays to colours, borders and spacing."""
    return f"""
        body {{ color: {c['text']}; }}
        a {{ color: {c['accent']}; }}
        code {{ background-color: {c['field']}; color: {c['text']};
                font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; }}
        pre {{ background-color: {c['field']}; color: {c['text']};
               font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; }}
        table {{ border-collapse: collapse; background-color: {c['field']}; }}
        th {{ background-color: {c['raised']}; color: {c['text']};
              border: 1px solid {c['border']}; padding: 6px 10px; text-align: left; }}
        td {{ border: 1px solid {c['border']}; padding: 6px 10px; color: {c['muted']}; }}
    """


def style_rendered_document(document, c):
    """Colour the tables and code blocks Qt built from the markdown.

    A table becomes a QTextTable with its own frame and cell formats, and a fenced code
    block becomes a block with nonBreakableLines set - neither reads the document style
    sheet, so both come out in Qt's defaults (white table headers on a dark window)
    unless they are formatted here."""
    for frame in document.rootFrame().childFrames():
        if not isinstance(frame, QTextTable):
            continue
        table_format = frame.format()
        table_format.setBorder(1)
        table_format.setBorderBrush(QColor(c["border"]))
        table_format.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
        table_format.setCellPadding(7)
        table_format.setCellSpacing(0)
        table_format.setBackground(QColor(c["field"]))
        frame.setFormat(table_format)
        for column in range(frame.columns()):
            header = frame.cellAt(0, column)
            cell_format = header.format()
            cell_format.setBackground(QColor(c["raised"]))
            cell_format.setForeground(QColor(c["text"]))
            header.setFormat(cell_format)

    block = document.begin()
    while block.isValid():
        if block.blockFormat().nonBreakableLines():      # a fenced code block
            cursor = QTextCursor(block)
            block_format = block.blockFormat()
            block_format.setBackground(QColor(c["field"]))
            block_format.setLeftMargin(10)
            block_format.setRightMargin(10)
            block_format.setTopMargin(2)
            block_format.setBottomMargin(2)
            cursor.setBlockFormat(block_format)
        block = block.next()
