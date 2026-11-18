"""Deterministic document renderers (PDF, PNG/JPG, neutral chat UI, CSV, EML)."""

from __future__ import annotations

import csv
from email.message import EmailMessage
from email.policy import SMTP
import hashlib
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from sim.generator.domain import WATERMARK_TEXT
from sim.generator.image_corruptions import ensure_watermark

def _dejavu_font(filename: str) -> str:
    """Find the same DejaVu TTF on Arch or Debian/Ubuntu; do not silently
    substitute a different font because that changes document geometry."""
    candidates = (
        Path("/usr/share/fonts/TTF") / filename,
        Path("/usr/share/fonts/truetype/dejavu") / filename,
    )
    for path in candidates:
        if path.is_file():
            return str(path)
    return str(candidates[0])


FONT_PATHS: dict[str, str] = {
    "regular": _dejavu_font("DejaVuSans.ttf"),
    "bold": _dejavu_font("DejaVuSans-Bold.ttf"),
    "oblique": _dejavu_font("DejaVuSans-Oblique.ttf"),
    "mono": _dejavu_font("DejaVuSansMono.ttf"),
}

REPORTLAB_FONTS: dict[str, str] = {
    "DejaVuSans": FONT_PATHS["regular"],
    "DejaVuSans-Bold": FONT_PATHS["bold"],
    "DejaVuSans-Oblique": FONT_PATHS["oblique"],
    "DejaVuSansMono": FONT_PATHS["mono"],
}

_MARGIN_X = 42.0
_TOP_Y = 800.0
_BOTTOM_SAFE_Y = 58.0


def register_reportlab_fonts() -> list[str]:
    """Register DejaVu TrueType fonts for ReportLab idempotently."""
    registered = set(pdfmetrics.getRegisteredFontNames())
    for name, font_path in REPORTLAB_FONTS.items():
        if name not in registered and Path(font_path).exists():
            pdfmetrics.registerFont(TTFont(name, font_path))
    pdfmetrics.registerFontFamily(
        "DejaVuSans",
        normal="DejaVuSans",
        bold="DejaVuSans-Bold",
        italic="DejaVuSans-Oblique",
        boldItalic="DejaVuSans-Bold",
    )
    return list(REPORTLAB_FONTS.keys())


def load_pil_font(style: str = "regular", size: int = 16) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a DejaVu Pillow TrueType font by style, falling back cleanly."""
    font_path = FONT_PATHS.get(style, FONT_PATHS["regular"])
    if Path(font_path).exists():
        return ImageFont.truetype(font_path, max(8, int(size)))
    return ImageFont.load_default()


def _draw_pdf_watermark(c: canvas.Canvas, watermark: str, page_w: float, page_h: float) -> None:
    """Draw diagonal translucent watermark and footer watermark on the current PDF page."""
    c.saveState()
    c.translate(page_w / 2.0, page_h / 2.0)
    c.rotate(35)
    c.setFillColor(colors.Color(0.70, 0.16, 0.16, alpha=0.16))
    c.setFont("DejaVuSans-Bold", 30)
    c.drawCentredString(0, 0, watermark)
    c.restoreState()

    c.saveState()
    c.setFillColor(colors.Color(0.60, 0.12, 0.12, alpha=0.80))
    c.setFont("DejaVuSans-Bold", 8.5)
    c.drawCentredString(page_w / 2.0, 18.0, watermark)
    c.restoreState()


def _draw_pdf_header(c: canvas.Canvas, header: dict[str, Any] | str | None, page_w: float, y: float) -> float:
    """Draw page header block and return next vertical cursor y."""
    if not header:
        return y
    info = {"title": header} if isinstance(header, str) else header
    title = str(info.get("title", ""))
    subtitle = str(info.get("subtitle", ""))
    meta = str(info.get("meta", ""))

    c.setFillColor(colors.HexColor("#1B365D"))
    c.setFont("DejaVuSans-Bold", 13)
    c.drawString(_MARGIN_X, y, title)
    y -= 17.0
    if subtitle:
        c.setFillColor(colors.HexColor("#334155"))
        c.setFont("DejaVuSans", 9.5)
        c.drawString(_MARGIN_X, y, subtitle)
        y -= 13.0
    if meta:
        c.setFillColor(colors.HexColor("#475569"))
        c.setFont("DejaVuSans-Oblique", 8.5)
        c.drawString(_MARGIN_X, y, meta)
        y -= 12.0
    c.setStrokeColor(colors.HexColor("#CBD5E1"))
    c.setLineWidth(0.8)
    c.line(_MARGIN_X, y, page_w - _MARGIN_X, y)
    return y - 14.0


def _draw_pdf_kv_rows(c: canvas.Canvas, kv_rows: list[tuple[str, Any]], x: float, y: float) -> float:
    """Render key-value pairs cleanly onto the PDF canvas."""
    for key, val in kv_rows:
        if y < _BOTTOM_SAFE_Y:
            break
        c.setFont("DejaVuSans-Bold", 9)
        c.setFillColor(colors.HexColor("#1E293B"))
        c.drawString(x, y, f"{key}:")
        c.setFont("DejaVuSans", 9)
        c.setFillColor(colors.HexColor("#0F172A"))
        c.drawString(x + 150.0, y, str(val))
        y -= 13.0
    return y - 4.0


def _draw_pdf_paragraphs(
    c: canvas.Canvas,
    paragraphs: list[str],
    x: float,
    y: float,
    max_w: float,
) -> float:
    """Wrap and draw paragraphs onto the PDF canvas."""
    c.setFont("DejaVuSans", 9.5)
    c.setFillColor(colors.HexColor("#0F172A"))
    for para in paragraphs:
        for line in simpleSplit(str(para), "DejaVuSans", 9.5, max_w):
            if y < _BOTTOM_SAFE_Y:
                return y
            c.drawString(x, y, line)
            y -= 12.5
        y -= 4.0
    return y


def _resolve_pdf_col_widths(table: dict[str, Any], ncols: int, max_w: float) -> list[float]:
    raw = table.get("col_widths")
    if raw and len(raw) == ncols:
        total = float(sum(raw))
        return [max_w * (float(w) / total) for w in raw]
    return [max_w / max(1, ncols)] * max(1, ncols)


def _draw_pdf_table_row(
    c: canvas.Canvas,
    cells: list[Any],
    x: float,
    y: float,
    widths: list[float],
    is_header: bool,
    shaded: bool,
) -> float:
    """Render one row of a PDF table and return next y."""
    row_h = 16.0
    total_w = sum(widths)
    bg = "#E8EEF5" if is_header else ("#F8FAFC" if shaded else "#FFFFFF")
    c.setFillColor(colors.HexColor(bg))
    c.setStrokeColor(colors.HexColor("#CBD5E1"))
    c.rect(x, y - row_h + 3.0, total_w, row_h, fill=1, stroke=1)

    c.setFont("DejaVuSans-Bold" if is_header else "DejaVuSans", 8.0)
    c.setFillColor(colors.HexColor("#0F172A"))
    cur_x = x
    for cell_val, col_w in zip(cells, widths):
        max_chars = max(4, int(col_w / 4.8))
        text = str(cell_val)[:max_chars]
        c.drawString(cur_x + 4.0, y - 9.0, text)
        cur_x += col_w
    return y - row_h


def _draw_pdf_table(c: canvas.Canvas, table: dict[str, Any], x: float, y: float, max_w: float) -> float:
    """Render a structured table onto the PDF canvas."""
    headers = list(table.get("headers", []))
    rows = list(table.get("rows", []))
    ncols = len(headers) if headers else (len(rows[0]) if rows else 1)
    widths = _resolve_pdf_col_widths(table, ncols, max_w)
    if headers and y >= _BOTTOM_SAFE_Y:
        y = _draw_pdf_table_row(c, headers, x, y, widths, is_header=True, shaded=False)
    for idx, row in enumerate(rows):
        if y < _BOTTOM_SAFE_Y:
            break
        y = _draw_pdf_table_row(c, list(row), x, y, widths, is_header=False, shaded=(idx % 2 == 1))
    return y - 8.0


def _draw_pdf_section(c: canvas.Canvas, section: dict[str, Any], x: float, y: float, max_w: float) -> float:
    """Render one page section (optional heading, kv_rows, paragraphs, table)."""
    heading = section.get("heading")
    if heading and y >= _BOTTOM_SAFE_Y:
        c.setFont("DejaVuSans-Bold", 10.5)
        c.setFillColor(colors.HexColor("#1E3A5F"))
        c.drawString(x, y, str(heading))
        y -= 14.0
    if section.get("kv_rows"):
        y = _draw_pdf_kv_rows(c, list(section["kv_rows"]), x, y)
    if section.get("paragraphs"):
        y = _draw_pdf_paragraphs(c, list(section["paragraphs"]), x, y, max_w)
    if section.get("table"):
        y = _draw_pdf_table(c, dict(section["table"]), x, y, max_w)
    return y - 4.0


def _draw_pdf_footer(c: canvas.Canvas, footer: str | None, page_w: float) -> None:
    if not footer:
        return
    c.setFont("DejaVuSans", 8.0)
    c.setFillColor(colors.HexColor("#64748B"))
    c.drawString(_MARGIN_X, 32.0, str(footer))
    c.line(_MARGIN_X, 42.0, page_w - _MARGIN_X, 42.0)


def _render_pdf_page(
    c: canvas.Canvas,
    page: dict[str, Any],
    page_w: float,
    page_h: float,
    max_w: float,
    watermark: str,
) -> None:
    y = _draw_pdf_header(c, page.get("header") or page.get("title"), page_w, _TOP_Y)
    for section in page.get("sections", []):
        if y < _BOTTOM_SAFE_Y:
            break
        y = _draw_pdf_section(c, section, _MARGIN_X, y, max_w)
    if page.get("paragraphs"):
        y = _draw_pdf_paragraphs(c, list(page["paragraphs"]), _MARGIN_X, y, max_w)
    if page.get("table"):
        _draw_pdf_table(c, dict(page["table"]), _MARGIN_X, y, max_w)
    _draw_pdf_footer(c, page.get("footer"), page_w)
    _draw_pdf_watermark(c, watermark, page_w, page_h)
    c.showPage()


def render_pdf(
    path: Path | str,
    pages: list[dict[str, Any]],
    watermark: str = WATERMARK_TEXT,
) -> None:
    """Render structured pages deterministically to an A4 PDF with watermark on every page."""
    register_reportlab_fonts()
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = A4
    max_w = page_w - 2.0 * _MARGIN_X

    c = canvas.Canvas(str(out_path), pagesize=A4, invariant=1)
    c.setTitle("LAGRANGE Rx Synthetic Document")
    c.setSubject(watermark)
    c.setAuthor("ООО «CardioImport Distribution» (SYNTHETIC)")
    c.setCreator("LAGRANGE Rx Simulator")

    for page in pages or [{"header": {"title": "Пустой документ"}, "sections": []}]:
        _render_pdf_page(c, page, page_w, page_h, max_w, watermark)
    c.save()


def _wrap_pil_text(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_image_line(draw: ImageDraw.ImageDraw, item: dict[str, Any], y: int, width: int) -> int:
    """Draw one styled line dict onto a document PIL canvas and return next y."""
    style = str(item.get("style", "body"))
    default_sizes = {"heading": 28, "bold": 22, "mono": 20, "body": 21}
    font_size = int(item.get("font_size", default_sizes.get(style, 21)))
    font_key = "bold" if style in ("heading", "bold") else ("mono" if style == "mono" else "regular")
    font = load_pil_font(font_key, font_size)

    color = tuple(item.get("color", (20, 32, 52)))
    x = int(item.get("x", 64))
    pos_y = int(item.get("y", y))
    text = str(item.get("text", ""))
    max_w = max(120, width - x - 64)

    for wrapped in _wrap_pil_text(draw, text, font, max_w):
        draw.text((x, pos_y), wrapped, font=font, fill=color)
        pos_y += int(font_size * 1.45)
    return pos_y + (8 if style == "heading" else 4)


def _save_image_deterministic(img: Image.Image, path: Path) -> None:
    """Save PIL image deterministically as JPEG or PNG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        img.convert("RGB").save(path, format="JPEG", quality=88, optimize=False)
    else:
        img.convert("RGB").save(path, format="PNG", compress_level=6)


def render_document_image(
    path: Path | str,
    title: str,
    lines: list[dict[str, Any]],
    width: int = 1200,
    height: int = 1600,
    watermark: str = WATERMARK_TEXT,
    postprocess_fn: Callable[[Image.Image], Image.Image] | None = None,
) -> None:
    """Render a synthetic paper document onto an image, apply optional R1/R2/R3 effects, and watermark."""
    img = Image.new("RGB", (width, height), (252, 251, 247))
    draw = ImageDraw.Draw(img)

    draw.rectangle([40, 40, width - 40, height - 44], outline=(195, 202, 212), width=2)
    draw.rectangle([40, 40, width - 40, 128], fill=(235, 240, 248))
    title_font = load_pil_font("bold", 30)
    draw.text((64, 68), str(title), font=title_font, fill=(24, 52, 92))
    draw.line([(40, 128), (width - 40, 128)], fill=(140, 160, 188), width=3)

    y = 156
    for item in lines:
        if y > height - 80:
            break
        y = _draw_image_line(draw, item, y, width)

    if postprocess_fn is not None:
        img = postprocess_fn(img)
    img = ensure_watermark(img, text=watermark)
    _save_image_deterministic(img, Path(path))


def _draw_chat_header(draw: ImageDraw.ImageDraw, width: int, title: str, subtitle: str) -> int:
    """Draw a neutral corporate messenger header bar without third-party brand logos."""
    draw.rectangle([0, 0, width, 86], fill=(47, 72, 88))
    draw.ellipse([22, 18, 70, 66], fill=(86, 125, 146))
    draw.text((37, 30), "LC", font=load_pil_font("bold", 18), fill=(255, 255, 255))
    draw.text((88, 18), title, font=load_pil_font("bold", 22), fill=(255, 255, 255))
    draw.text((88, 50), subtitle, font=load_pil_font("regular", 15), fill=(210, 224, 232))
    return 110


def _layout_chat_bubble(
    draw: ImageDraw.ImageDraw,
    msg: dict[str, Any],
    y: int,
    width: int,
) -> dict[str, Any]:
    """Compute pixel layout for one chat bubble so rendering and bbox lookup stay identical."""
    side = str(msg.get("side", "left"))
    text = str(msg.get("text", ""))
    body_font = load_pil_font("regular", 19)
    max_bubble_w = int(width * (0.88 if msg.get("wide") else 0.66))
    wrapped = _wrap_pil_text(draw, text, body_font, max_bubble_w - 36)
    bubble_h = 34 + len(wrapped) * 26 + 22
    x1 = 28 if side == "left" else width - max_bubble_w - 28
    return {
        "side": side,
        "sender": str(msg.get("sender", "")),
        "ts": str(msg.get("time", "")),
        "body_font": body_font,
        "wrapped": wrapped,
        "x1": x1,
        "x2": x1 + max_bubble_w,
        "y1": y,
        "y2": y + bubble_h,
        "text_x": x1 + 16,
        "text_y": y + 34,
    }


def _draw_chat_bubble(draw: ImageDraw.ImageDraw, msg: dict[str, Any], y: int, width: int) -> int:
    """Draw a single chat bubble (left or right aligned) and return next vertical y."""
    ly = _layout_chat_bubble(draw, msg, y, width)
    is_left = ly["side"] == "left"
    fill = (255, 255, 255) if is_left else (220, 238, 251)
    border = (205, 214, 224) if is_left else (175, 206, 232)

    draw.rounded_rectangle([ly["x1"], ly["y1"], ly["x2"], ly["y2"]], radius=14, fill=fill, outline=border, width=2)
    draw.text((ly["text_x"], ly["y1"] + 10), ly["sender"], font=load_pil_font("bold", 15), fill=(36, 76, 114))
    cur_y = ly["text_y"]
    for line in ly["wrapped"]:
        draw.text((ly["text_x"], cur_y), line, font=ly["body_font"], fill=(24, 30, 38))
        cur_y += 26
    draw.text((ly["x2"] - 64, ly["y2"] - 22), ly["ts"], font=load_pil_font("regular", 13), fill=(110, 122, 136))
    return ly["y2"] + 18


def compute_chat_token_bbox(
    messages: list[dict[str, Any]],
    msg_index: int,
    token: str,
    width: int = 960,
) -> tuple[int, int, int, int]:
    """Compute exact pixel bounding box (x1, y1, x2, y2) of token inside message msg_index."""
    img = Image.new("RGB", (width, 1600), (236, 239, 244))
    draw = ImageDraw.Draw(img)
    y = 110
    for idx, msg in enumerate(messages):
        ly = _layout_chat_bubble(draw, msg, y, width)
        if idx == msg_index:
            cur_y = ly["text_y"]
            for line in ly["wrapped"]:
                pos = line.find(token)
                if pos >= 0:
                    dx1 = int(draw.textlength(line[:pos], font=ly["body_font"]))
                    dx2 = int(draw.textlength(line[: pos + len(token)], font=ly["body_font"]))
                    return (ly["text_x"] + dx1, cur_y, ly["text_x"] + dx2, cur_y + 24)
                cur_y += 26
            return (ly["x2"] - 140, ly["text_y"], ly["x2"] - 16, ly["text_y"] + 24)
        y = ly["y2"] + 18
    raise IndexError(f"msg_index {msg_index} out of range for {len(messages)} messages")


def _resolve_table_img_widths(headers: list[str], col_widths: list[int] | None, table_w: int) -> list[int]:
    if col_widths and len(col_widths) == len(headers):
        total = float(sum(col_widths))
        return [int(table_w * (w / total)) for w in col_widths]
    col_w = table_w // max(1, len(headers))
    return [col_w] * max(1, len(headers))


def compute_table_image_bboxes(
    headers: list[str],
    rows: list[list[Any]],
    meta_count: int,
    width: int = 1200,
    col_widths: list[int] | None = None,
) -> dict[str, tuple[int, int, int, int]]:
    """Compute exact pixel bounding boxes for tabular delivery-note cells and total row."""
    table_x = 56
    table_w = width - 112
    widths = _resolve_table_img_widths(headers, col_widths, table_w)
    table_top = 152 + meta_count * 34 + 16
    row_h = 52
    bboxes: dict[str, tuple[int, int, int, int]] = {}
    for r_idx, _ in enumerate(rows):
        y1 = table_top + 48 + r_idx * row_h
        y2 = y1 + row_h
        cur_x = table_x
        for c_idx, cw in enumerate(widths):
            bboxes[f"row_{r_idx}_col_{c_idx}"] = (cur_x, y1, cur_x + cw, y2)
            cur_x += cw
    qty_col = min(5, max(0, len(widths) - 3))
    if rows:
        bboxes["row_0_qty"] = bboxes[f"row_0_col_{qty_col}"]
    total_y = table_top + 48 + len(rows) * row_h + 24
    bboxes["total"] = (table_x, total_y, table_x + table_w, total_y + 44)
    return bboxes


def _draw_table_img_row(
    draw: ImageDraw.ImageDraw,
    cells: list[Any],
    x: int,
    y: int,
    widths: list[int],
    row_h: int,
    is_header: bool,
    shaded: bool,
) -> None:
    """Draw one bordered row onto a tabular PIL document image."""
    bg = (224, 234, 246) if is_header else ((247, 249, 252) if shaded else (255, 255, 255))
    font = load_pil_font("bold" if is_header else "regular", 16 if is_header else 15)
    cur_x = x
    for val, cw in zip(cells, widths):
        draw.rectangle([cur_x, y, cur_x + cw, y + row_h], fill=bg, outline=(165, 175, 190), width=1)
        draw.text((cur_x + 8, y + (14 if is_header else 16)), str(val)[:28], font=font, fill=(20, 30, 46))
        cur_x += cw


def render_table_document_image(
    path: Path | str,
    title: str,
    meta_lines: list[str],
    headers: list[str],
    rows: list[list[Any]],
    footer_lines: list[str] | None = None,
    col_widths: list[int] | None = None,
    width: int = 1200,
    height: int = 1600,
    watermark: str = WATERMARK_TEXT,
    postprocess_fn: Callable[[Image.Image], Image.Image] | None = None,
) -> dict[str, tuple[int, int, int, int]]:
    """Render a full tabular warehouse waybill/note image with deterministic cell bboxes."""
    img = Image.new("RGB", (width, height), (252, 251, 247))
    draw = ImageDraw.Draw(img)
    draw.rectangle([36, 36, width - 36, height - 42], outline=(185, 195, 208), width=2)
    draw.rectangle([36, 36, width - 36, 126], fill=(232, 239, 248))
    draw.text((58, 64), str(title), font=load_pil_font("bold", 26), fill=(22, 48, 86))

    y = 152
    meta_font = load_pil_font("regular", 19)
    for mline in meta_lines:
        draw.text((58, y), str(mline), font=meta_font, fill=(32, 42, 56))
        y += 34

    table_x = 56
    widths = _resolve_table_img_widths(headers, col_widths, width - 112)
    table_top = y + 16
    _draw_table_img_row(draw, headers, table_x, table_top, widths, 48, is_header=True, shaded=False)
    for r_idx, row in enumerate(rows):
        _draw_table_img_row(draw, row, table_x, table_top + 48 + r_idx * 52, widths, 52, is_header=False, shaded=(r_idx % 2 == 1))

    foot_y = table_top + 48 + len(rows) * 52 + 28
    foot_font = load_pil_font("bold", 20)
    for f_idx, fline in enumerate(footer_lines or []):
        font = foot_font if f_idx == 0 else load_pil_font("regular", 18)
        draw.text((58, foot_y), str(fline), font=font, fill=(20, 32, 52))
        foot_y += 42

    if postprocess_fn is not None:
        img = postprocess_fn(img)
    img = ensure_watermark(img, text=watermark)
    _save_image_deterministic(img, Path(path))
    return compute_table_image_bboxes(headers, rows, len(meta_lines), width=width, col_widths=col_widths)


def render_chat_screenshot(
    path: Path | str,
    messages: list[dict[str, Any]],
    watermark: str = WATERMARK_TEXT,
    crop_fn: Callable[[Image.Image], Image.Image] | None = None,
) -> None:
    """Render a neutral messenger chat screenshot PNG with optional R4 crop and watermark."""
    width = 960
    height = max(720, 160 + len(messages) * 145)
    img = Image.new("RGB", (width, height), (236, 239, 244))
    draw = ImageDraw.Draw(img)

    y = _draw_chat_header(
        draw,
        width,
        "Corporate Logistics Chat (SYNTHETIC)",
        "Учебная переписка по ставкам перевозки",
    )
    for msg in messages:
        if y > height - 90:
            break
        y = _draw_chat_bubble(draw, msg, y, width)

    if crop_fn is not None:
        img = crop_fn(img)
    img = ensure_watermark(img, text=watermark)
    _save_image_deterministic(img, Path(path))


def _format_csv_cell(val: Any, decimal_comma: bool) -> str:
    """Format one CSV cell with optional decimal comma for floating-point values."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, float) and decimal_comma:
        return f"{val}".replace(".", ",")
    return str(val)


def write_csv_doc(
    path: Path | str,
    headers: list[str],
    rows: list[list[Any]],
    encoding: str = "cp1251",
    delimiter: str = ";",
    decimal_comma: bool = True,
) -> None:
    """Write a deterministic delimited CSV document with specified encoding and decimal style."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding=encoding, errors="replace", newline="") as fh:
        writer = csv.writer(fh, delimiter=delimiter, lineterminator="\n")
        writer.writerow([str(h) for h in headers])
        for row in rows:
            writer.writerow([_format_csv_cell(v, decimal_comma) for v in row])


_MIME_TYPES: dict[str, tuple[str, str]] = {
    ".pdf": ("application", "pdf"),
    ".csv": ("text", "csv"),
    ".txt": ("text", "plain"),
    ".png": ("image", "png"),
    ".jpg": ("image", "jpeg"),
    ".jpeg": ("image", "jpeg"),
}


def _mime_subtype_for_path(attachment_path: Path) -> tuple[str, str]:
    return _MIME_TYPES.get(attachment_path.suffix.lower(), ("application", "octet-stream"))


def write_eml_doc(
    path: Path | str,
    subject: str,
    sender: str,
    recipient: str,
    date_str: str,
    body: str,
    attachment_path: Path | str | None = None,
) -> None:
    """Write a deterministic RFC-5322/MIME .eml email file without random boundaries or clock time."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256(f"{subject}|{sender}|{recipient}|{date_str}".encode("utf-8")).hexdigest()
    msg = EmailMessage(policy=SMTP)
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Date"] = date_str
    msg["Message-ID"] = f"<{digest[:20]}@cardioimport.synthetic>"
    msg.set_content(body, subtype="plain", charset="utf-8")

    if attachment_path is not None:
        att = Path(attachment_path)
        maintype, subtype = _mime_subtype_for_path(att)
        msg.make_mixed()
        msg.set_boundary(f"----=_LagrangeRx_{digest[:16]}")
        msg.add_attachment(
            att.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=att.name,
        )

    out_path.write_bytes(msg.as_bytes())
