"""Screens, images, charts and documents."""

import base64
import json
import math
import os
import re
import subprocess
from io import BytesIO
from PIL import Image
from PyQt6.QtCore import (
    QBuffer, QIODevice, QPointF, QRectF, QSize, QUrl, Qt,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPolygonF, QTextCursor, QTextDocument,
)
from .config import (
    PROTECTED_PATHS,
)
from .store import (
    MAX_SKILL_CHARS, settings,
)
from .files import (
    _backup, name_inside, resolve_guarded,
)
from .theme import (
    THEMES, style_rendered_document,
)


def _hypr(*args):
    """Query Hyprland. Returns parsed JSON, or None if that isn't the compositor."""
    try:
        done = subprocess.run(["hyprctl", *args, "-j"],
                              capture_output=True, text=True, timeout=3)
        return json.loads(done.stdout) if done.returncode == 0 else None
    except Exception:
        return None


def target_window():
    """The most recently focused window that is not Bonsai itself.

    When you press Send, Bonsai holds focus - so capturing "the active window" would
    capture Bonsai looking at itself. Hyprland's focusHistoryID orders windows by how
    recently they were focused, so the first entry that isn't ours is what you were
    actually looking at."""
    mine = os.getpid()
    candidates = []
    for client in _hypr("clients") or []:
        try:
            width, height = client.get("size") or (0, 0)
        except (TypeError, ValueError):
            continue
        if client.get("pid") == mine or client.get("hidden") or width < 1 or height < 1:
            continue
        candidates.append(client)
    candidates.sort(key=lambda c: c.get("focusHistoryID", 9999))
    return candidates[0] if candidates else None


def window_region(window):
    """grim -g geometry for a window: native pixels, the most detail per token."""
    try:
        x, y = window["at"]
        width, height = window["size"]
    except (KeyError, TypeError, ValueError):
        return None
    return f"{x},{y} {width}x{height}" if width > 0 and height > 0 else None


def output_for(window):
    """Name of the monitor a window sits on, else whichever monitor has focus."""
    monitors = _hypr("monitors") or []
    if window is not None:
        for monitor in monitors:
            if monitor.get("id") == window.get("monitor"):
                return monitor.get("name")
    for monitor in monitors:
        if monitor.get("focused"):
            return monitor.get("name")
    return None


def capture_args(mode, tmp):
    """Build the grim call. Falls back to a whole-desktop grab whenever the compositor
    cannot be asked, so capture degrades rather than failing."""
    window = target_window() if mode in ("active-window", "active-monitor") else None
    if mode == "active-window":
        region = window_region(window) if window else None
        if region:
            return ["grim", "-g", region, tmp]
    if mode in ("active-window", "active-monitor"):
        output = output_for(window)
        if output:
            return ["grim", "-o", output, tmp]
    return ["grim", tmp]


def capture_screen(mode=None):
    tmp = "/tmp/bonsai_frame.png"
    config = settings()
    try:
        subprocess.run(capture_args(mode or config.get("capture_mode", "active-monitor"),
                                    tmp),
                       check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError("'grim' not found - install it or disable screen capture.")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"grim failed: {exc.stderr.strip()}")
    with Image.open(tmp) as img:
        # Downscale only as far as the token budget needs. The old fixed 1280x720 box
        # turned a 5120x1440 two-monitor grab into a 1280x360 strip, in which no text
        # survives - the model was guessing from blurred shapes.
        return encode_frame(img.copy())


PDF_MARGIN_MM = 16


def fit_images(document, max_width, max_height):
    """Scale images down to fit the page, keeping their proportions.

    Qt lays an image out at its natural pixel size, so a 2560-wide screenshot runs off
    the edge of an A4 page and a modest one still swallows it whole. Nothing warns; the
    document simply comes out wrong."""
    block = document.begin()
    while block.isValid():
        fragments = block.begin()
        while not fragments.atEnd():
            fragment = fragments.fragment()
            char_format = fragment.charFormat()
            if char_format.isImageFormat():
                image_format = char_format.toImageFormat()
                source = document.resource(QTextDocument.ResourceType.ImageResource,
                                           QUrl(image_format.name()))
                width = image_format.width() or (source.width() if source else 0)
                height = image_format.height() or (source.height() if source else 0)
                if width and height:
                    scale = min(max_width / width, max_height / height, 1.0)
                    image_format.setWidth(width * scale)
                    image_format.setHeight(height * scale)
                    cursor = QTextCursor(document)
                    cursor.setPosition(fragment.position())
                    cursor.setPosition(fragment.position() + fragment.length(),
                                       QTextCursor.MoveMode.KeepAnchor)
                    cursor.setCharFormat(image_format)
            fragments += 1
        block = block.next()


CHART_TYPES = ("bar", "line", "pie")


CHART_WIDTH, CHART_HEIGHT = 900, 560


CHART_MAX_POINTS = 24


# Ordered so neighbouring slices stay distinguishable in greyscale, which is where a
# pie chart printed out of a PDF usually ends up.
CHART_COLOURS = ["#2563eb", "#f59e0b", "#059669", "#dc2626", "#7c3aed",
                 "#0891b2", "#db2777", "#65a30d", "#4f46e5", "#ea580c"]


CHART_INK = "#111827"


CHART_MUTED = "#6b7280"


CHART_GRID = "#d1d5db"


CHART_PAPER = "#ffffff"


def parse_chart_data(raw):
    """Pull (label, value, shown) triples out of 'label=value' pairs.

    Values arrive carrying their units - '16.7ms', '$40', '92%' - because that is how
    the user said them. The number drives the geometry and the original text stays as
    the printed label, so the model never has to write each figure twice."""
    points, rejected = [], []
    # A comma followed by exactly three digits is a thousands separator, not the start
    # of the next data point - splitting on every comma read "$1,250" as the value 1.
    for chunk in re.split(r",(?!\d{3}(?!\d))|[\n;]+", raw or ""):
        chunk = chunk.strip().strip("-*").strip()
        if not chunk:
            continue
        pair = re.match(r"^(.*?)\s*[=:]\s*(.+)$", chunk)
        if not pair:
            rejected.append(chunk)
            continue
        label, shown = pair.group(1).strip(), pair.group(2).strip()
        number = re.search(r"-?\d[\d,]*(?:\.\d+)?", shown)
        if not label or not number:
            rejected.append(chunk)
            continue
        try:
            points.append((label, float(number.group(0).replace(",", "")), shown))
        except ValueError:
            rejected.append(chunk)
    return points, rejected


def chart_number(value):
    """A number a person would write, not a float repr: 12 rather than 12.0."""
    if abs(value) < 1e12 and float(value).is_integer():
        return str(int(value))
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def nice_step(span, target_lines):
    """A gridline interval that lands on 1, 2 or 5 times a power of ten."""
    if span <= 0:
        return 1.0
    rough = span / max(target_lines, 1)
    power = 10 ** math.floor(math.log10(rough))
    for multiple in (1, 2, 5, 10):
        if power * multiple >= rough:
            return power * multiple
    return power * 10


def _chart_title(painter, title, subtitle=""):
    font = QFont()
    font.setPixelSize(22)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(CHART_INK))
    painter.drawText(QRectF(24, 16, CHART_WIDTH - 48, 30),
                     int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                     title)
    if subtitle:
        font.setPixelSize(13)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(CHART_MUTED))
        painter.drawText(QRectF(24, 46, CHART_WIDTH - 48, 20),
                         int(Qt.AlignmentFlag.AlignHCenter), subtitle)


def _axes(painter, points, has_title):
    """Draw the grid and return (plot rect, value->y function).

    The value axis always includes zero: a bar chart whose baseline is 40 exaggerates
    every difference on it, which is the oldest way to draw a misleading chart."""
    values = [value for _, value, _ in points]
    top = max(values + [0.0])
    bottom = min(values + [0.0])
    if top == bottom:
        top, bottom = top + 1.0, bottom - 1.0
    step = nice_step(top - bottom, 6)
    top = math.ceil(top / step) * step
    bottom = math.floor(bottom / step) * step

    left = 96.0
    plot = QRectF(left, 80.0 if has_title else 48.0,
                  CHART_WIDTH - left - 36.0, 0.0)
    plot.setBottom(CHART_HEIGHT - 84.0)

    font = QFont()
    font.setPixelSize(12)
    painter.setFont(font)

    def y_of(value):
        share = (value - bottom) / (top - bottom)
        return plot.bottom() - share * plot.height()

    line = bottom
    while line <= top + step / 2:
        y = y_of(line)
        painter.setPen(QPen(QColor(CHART_INK if abs(line) < 1e-9 else CHART_GRID),
                            1.5 if abs(line) < 1e-9 else 1.0))
        painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        painter.setPen(QColor(CHART_MUTED))
        painter.drawText(QRectF(8, y - 10, plot.left() - 18, 20),
                         int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                         chart_number(line))
        line += step
    painter.setPen(QPen(QColor(CHART_INK), 1.5))
    painter.drawLine(QPointF(plot.left(), plot.top()), QPointF(plot.left(), plot.bottom()))
    return plot, y_of


def _x_labels(painter, plot, points, centres):
    """Category labels under the axis, rotated if they will not fit flat."""
    font = QFont()
    font.setPixelSize(12)
    painter.setFont(font)
    metrics = QFontMetrics(font)
    slot = plot.width() / max(len(points), 1)
    widest = max(metrics.horizontalAdvance(label) for label, _, _ in points)
    rotate = widest > slot - 8
    painter.setPen(QColor(CHART_INK))
    for (label, _, _), centre in zip(points, centres):
        if rotate:
            painter.save()
            painter.translate(centre + 4, plot.bottom() + 10)
            painter.rotate(-40)
            painter.drawText(QRectF(-190, -10, 190, 20),
                             int(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter),
                             metrics.elidedText(label, Qt.TextElideMode.ElideRight, 185))
            painter.restore()
        else:
            painter.drawText(QRectF(centre - slot / 2, plot.bottom() + 8, slot, 34),
                             int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                                 | Qt.TextFlag.TextWordWrap),
                             label)


def _draw_bars(painter, points, title, subtitle):
    _chart_title(painter, title, subtitle)
    plot, y_of = _axes(painter, points, bool(title))
    slot = plot.width() / len(points)
    width = min(slot * 0.62, 90.0)
    zero = y_of(0.0)
    centres = []
    value_font = QFont()
    value_font.setPixelSize(12)
    value_font.setBold(True)
    for index, (_, value, shown) in enumerate(points):
        centre = plot.left() + slot * (index + 0.5)
        centres.append(centre)
        y = y_of(value)
        bar = QRectF(centre - width / 2, min(y, zero), width, abs(y - zero))
        colour = QColor(CHART_COLOURS[index % len(CHART_COLOURS)])
        painter.fillRect(bar, QBrush(colour))
        painter.setFont(value_font)
        painter.setPen(QColor(CHART_INK))
        above = value >= 0
        painter.drawText(QRectF(centre - slot / 2, (bar.top() - 22) if above
                                else (bar.bottom() + 2), slot, 20),
                         int(Qt.AlignmentFlag.AlignHCenter), shown)
    _x_labels(painter, plot, points, centres)


def _draw_line(painter, points, title, subtitle):
    _chart_title(painter, title, subtitle)
    plot, y_of = _axes(painter, points, bool(title))
    step = plot.width() / max(len(points) - 1, 1)
    centres = [plot.left() + (step * index if len(points) > 1 else plot.width() / 2)
               for index in range(len(points))]
    path = QPolygonF([QPointF(x, y_of(value))
                      for x, (_, value, _) in zip(centres, points)])
    colour = QColor(CHART_COLOURS[0])
    painter.setPen(QPen(colour, 2.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPolyline(path)
    value_font = QFont()
    value_font.setPixelSize(12)
    value_font.setBold(True)
    painter.setBrush(QBrush(colour))
    painter.setPen(QPen(QColor(CHART_PAPER), 2))
    for point in path:
        painter.drawEllipse(point, 4.5, 4.5)
    painter.setFont(value_font)
    painter.setPen(QColor(CHART_INK))
    for point, (_, _, shown) in zip(path, points):
        painter.drawText(QRectF(point.x() - 60, point.y() - 26, 120, 20),
                         int(Qt.AlignmentFlag.AlignHCenter), shown)
    _x_labels(painter, plot, points, centres)


def _draw_pie(painter, points, title, subtitle):
    _chart_title(painter, title, subtitle)
    total = sum(value for _, value, _ in points)
    top = 96.0 if title else 60.0
    diameter = min(CHART_HEIGHT - top - 40.0, CHART_WIDTH * 0.52)
    pie = QRectF(40.0, top + (CHART_HEIGHT - top - diameter) / 2, diameter, diameter)
    label_font = QFont()
    label_font.setPixelSize(13)

    # Qt measures pie angles in sixteenths of a degree, counter-clockwise from 3
    # o'clock. Starting at 90 degrees puts the first slice at the top, where a reader
    # expects it.
    start = 90 * 16
    for index, (_, value, _) in enumerate(points):
        span = int(round(value / total * 360 * 16))
        painter.setBrush(QBrush(QColor(CHART_COLOURS[index % len(CHART_COLOURS)])))
        painter.setPen(QPen(QColor(CHART_PAPER), 2))
        painter.drawPie(pie, start, -span)
        start -= span

    legend_x = pie.right() + 36
    legend_y = pie.center().y() - len(points) * 14
    painter.setFont(label_font)
    metrics = QFontMetrics(label_font)
    room = int(CHART_WIDTH - legend_x - 78)
    for index, (label, value, shown) in enumerate(points):
        y = legend_y + index * 28
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(CHART_COLOURS[index % len(CHART_COLOURS)])))
        painter.drawRect(QRectF(legend_x, y + 2, 14, 14))
        painter.setPen(QColor(CHART_INK))
        text = f"{label} - {shown} ({value / total * 100:.1f}%)"
        painter.drawText(QRectF(legend_x + 22, y, CHART_WIDTH - legend_x - 44, 18),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         metrics.elidedText(text, Qt.TextElideMode.ElideRight, room))


def render_chart(kind, points, title, subtitle=""):
    """Paint a chart and hand back the finished image."""
    image = QImage(CHART_WIDTH, CHART_HEIGHT, QImage.Format.Format_RGB32)
    image.fill(QColor(CHART_PAPER))
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if kind == "pie":
            _draw_pie(painter, points, title, subtitle)
        elif kind == "line":
            _draw_line(painter, points, title, subtitle)
        else:
            _draw_bars(painter, points, title, subtitle)
    finally:
        painter.end()
    return image


CHART_FORMATS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def split_chart_request(raw):
    """Read the arguments in whatever order they arrive.

    The documented order is path | type | title | data, but the type gets dropped and
    the title and data get swapped often enough that insisting on the order would fail
    calls that said everything needed. Each part is recognised by what it looks like."""
    parts = [part.strip() for part in (raw or "").split("|")]
    target = parts[0] if parts else ""
    rest = [part for part in parts[1:] if part]
    kind = "bar"
    for part in list(rest):
        if part.lower() in CHART_TYPES:
            kind = part.lower()
            rest.remove(part)
            break
    data = ""
    for part in list(rest):
        if re.search(r"[^=:]+[=:]\s*[^\s=:]*\d", part):
            data = part
            rest.remove(part)
            break
    if not data and rest:
        data = rest.pop()
    return target, kind, (rest[0] if rest else ""), data


def make_chart(raw):
    """MAKE_CHART: <out.png> | <bar|line|pie> | <title> | label=value, ... """
    target, kind, title, data = split_chart_request(raw)
    if not target:
        return ("[MAKE_CHART needs an output path first: "
                "MAKE_CHART: <file.png> | bar | <title> | a=1, b=2]")
    points, rejected = parse_chart_data(data)
    if not points:
        return ("[MAKE_CHART found no data. Give it label=value pairs separated by "
                "commas, e.g. 'vsync=16.7ms, uncapped=4.2ms'."
                + (f" These were not pairs: {', '.join(rejected[:4])}" if rejected else "")
                + "]")
    if len(points) > CHART_MAX_POINTS:
        return (f"[MAKE_CHART got {len(points)} values; more than {CHART_MAX_POINTS} is "
                "unreadable at this size. Chart the top few, or split it in two.]")
    if kind == "pie" and any(value < 0 for _, value, _ in points):
        return "[A pie chart cannot show negative values. Use bar instead.]"
    if kind == "pie" and sum(value for _, value, _ in points) <= 0:
        return "[A pie chart needs values that add up to more than zero.]"

    path, err = resolve_guarded(target)
    if err:
        return err
    if path in PROTECTED_PATHS:
        return "[Refused: protected system directory.]"
    path = name_inside(path, title, "chart", ".png")
    if path.suffix.lower() not in CHART_FORMATS:
        path = path.with_suffix(".png")
    if not path.parent.exists():
        return f"[Refused: folder missing: {path.parent}. MKDIR it first.]"

    backup = _backup(path)
    try:
        image = render_chart(kind, points, title.strip(),
                             f"{len(rejected)} entries skipped" if rejected else "")
        if not image.save(str(path)):
            return f"[Qt could not write a {path.suffix} image. Try a .png path.]"
    except Exception as exc:
        return f"[Could not draw the chart: {exc}]"
    if not path.exists():
        return "[The chart was not written, and no error was raised.]"

    plotted = ", ".join(f"{label} {shown}" for label, _, shown in points[:8])
    note = f" Previous version backed up to '{backup}'." if backup else ""
    if rejected:
        note += (" These were ignored because they were not label=value pairs: "
                 + ", ".join(rejected[:4]) + ".")
    return (f"Wrote {path.stat().st_size} bytes to '{path}' - a {kind} chart"
            + (f" titled '{title.strip()}'" if title.strip() else "")
            + f" of {len(points)} values: {plotted}."
            + " Reference it in a PDF or note as ![](" + path.name + ")." + note)


LOOK_MAX_WIDTH = 1400


LOOK_PAGE_LIMIT = 20


LOOK_RASTER = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}


LOOK_MARKUP = {".md", ".markdown", ".html", ".htm"}


LOOK_ALIASES = {
    "screen": "screen", "desktop": "screen", "the screen": "screen",
    "window": "window", "active window": "window", "app": "window",
    "game": "window", "the window": "window",
}


try:
    from PyQt6.QtPdf import QPdfDocument
except ImportError:                       # Qt built without the PDF module
    QPdfDocument = None


def encode_frame(image):
    """Downscale and JPEG-encode a PIL image for the vision slot."""
    config = settings()
    image.thumbnail((config.get("capture_max_width", 1920),
                     config.get("capture_max_height", 1200)), Image.LANCZOS)
    buffer = BytesIO()
    # subsampling=0 keeps colour detail per pixel. The default 4:2:0 smears coloured
    # text - which is most of a terminal, an editor, or a chart's axis labels.
    image.convert("RGB").save(buffer, format="JPEG",
                              quality=config.get("capture_quality", 90), subsampling=0)
    return base64.b64encode(buffer.getvalue()).decode()


def qimage_to_pil(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(BytesIO(bytes(buffer.data()))).copy()


def _open_pdf(path):
    if QPdfDocument is None:
        raise RuntimeError("this Qt build has no PDF support, so PDFs cannot be viewed")
    # PyQt6 requires the parent argument; QPdfDocument() alone raises "not enough
    # arguments" rather than anything mentioning PDFs.
    document = QPdfDocument(None)
    document.load(str(path))
    if document.pageCount() < 1:
        raise RuntimeError("no pages could be read from it")
    return document


def _render_page(document, index, max_width):
    point = document.pagePointSize(index)
    scale = min(max_width / max(point.width(), 1), 2.5)
    size = QSize(max(int(point.width() * scale), 1),
                 max(int(point.height() * scale), 1))
    # A page renders as ink on a transparent background. Flattened straight to JPEG
    # that becomes ink on black - a uniformly black rectangle, which encodes fine and
    # reads as a rendered page right up until someone looks at it. Paper is white.
    paper = QImage(size, QImage.Format.Format_RGB32)
    paper.fill(QColor("#ffffff"))
    painter = QPainter(paper)
    try:
        painter.drawImage(0, 0, document.render(index, size))
    finally:
        painter.end()
    return paper


def render_pdf_page(path, number):
    """Render one page of a PDF to an image, and say how many there are."""
    document = _open_pdf(path)
    count = document.pageCount()
    index = min(max(int(number or 1) - 1, 0), count - 1)
    return _render_page(document, index, LOOK_MAX_WIDTH), count, index + 1


def render_pdf_pages(path, limit, max_width):
    """Every page up to `limit`, for scrolling through in the preview panel."""
    document = _open_pdf(path)
    count = document.pageCount()
    return ([_render_page(document, index, max_width)
             for index in range(min(count, limit))], count)


def render_markup_image(text, path, width=1000):
    """Lay text, markdown or HTML out the way a reader would see it."""
    document = QTextDocument()
    document.setBaseUrl(QUrl.fromLocalFile(f"{path.parent}/"))
    suffix = path.suffix.lower()
    if suffix in (".md", ".markdown"):
        document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
    elif suffix in (".html", ".htm"):
        document.setHtml(text)
    else:
        document.setPlainText(text)
    document.setTextWidth(width - 48)
    fit_images(document, width - 48, 2000)
    style_rendered_document(document, THEMES["light"])
    height = min(max(int(document.size().height()) + 48, 220), 5000)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.translate(24, 24)
        document.drawContents(painter)
    finally:
        painter.end()
    return image


def look_at(raw):
    """LOOK: <file | screen | window> - returns (note, base64 JPEG or None).

    The note goes into the tool results like any other; the image is handed to the
    vision slot the turn loop already carries, so the next step genuinely sees it
    rather than being told about it."""
    parts = [part.strip() for part in (raw or "").split("|")]
    target = parts[0]
    page = parts[1] if len(parts) > 1 else ""
    if not target:
        return ("[LOOK needs something to look at: a file path, 'screen', or 'window'.]",
                None)

    alias = LOOK_ALIASES.get(target.lower())
    if alias:
        try:
            if alias == "screen":
                return ("Here is the whole screen as it is right now.", capture_screen())
            frame = capture_screen(mode="active-window")
        except Exception as exc:
            return (f"[Could not capture the screen: {exc}]", None)
        return ("Here is the window that currently has focus. If you just launched "
                "something, this is it - check that it actually drew what you intended.",
                frame)

    path, err = resolve_guarded(target)
    if err:
        return err, None
    if path.is_dir():
        return (f"[{path} is a folder. LOOK takes one file - LIST_DIR it first.]", None)
    if not path.exists():
        return (f"[Nothing to look at: {path} does not exist.]", None)

    suffix = path.suffix.lower()
    try:
        if suffix in LOOK_RASTER:
            with Image.open(path) as opened:
                shape = f"{opened.width}x{opened.height}"
                picture = opened.convert("RGB")
            note = f"Here is '{path.name}' - a {shape} image."
        elif suffix == ".pdf":
            rendered, count, shown = render_pdf_page(path, page or 1)
            picture = qimage_to_pil(rendered)
            note = (f"Here is page {shown} of {count} of '{path.name}'."
                    + (f" To see another, call LOOK again as '{path} | <page number>'."
                       if count > 1 else ""))
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > MAX_SKILL_CHARS * 4:
                text = text[:MAX_SKILL_CHARS * 4] + "\n...[truncated for rendering]"
            picture = qimage_to_pil(render_markup_image(text, path))
            note = (f"Here is '{path.name}' laid out as a reader would see it."
                    + (" Scripts and stylesheets do not run in this view, so judge the "
                       "structure, not the finished page."
                       if suffix in (".html", ".htm") else ""))
    except Exception as exc:
        return (f"[Could not render {path.name}: {exc}]", None)

    try:
        return note, encode_frame(picture)
    except Exception as exc:
        return (f"[Rendered {path.name} but could not encode it: {exc}]", None)
