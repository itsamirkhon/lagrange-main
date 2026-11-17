"""Deterministic Pillow + NumPy visual corruption operators for LAGRANGE Rx."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

FONT_REGULAR = "/usr/share/fonts/TTF/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"
FONT_OBLIQUE = "/usr/share/fonts/TTF/DejaVuSans-Oblique.ttf"
FONT_COMIC = "/usr/share/fonts/TTF/TSCu_Comic.ttf"

STAMP_TEXT = "SYNTHETIC • УЧЕБНЫЙ ОБРАЗЕЦ • CARDIOIMPORT"
WATERMARK_TEXT = "УЧЕБНЫЙ ОБРАЗЕЦ / SYNTHETIC"


def _load_font(paths: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the first available TrueType font from paths, or Pillow default."""
    for font_path in paths:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, max(8, int(size)))
    return ImageFont.load_default()


def _perspective_coeffs(
    out_pts: list[tuple[float, float]],
    in_pts: list[tuple[float, float]],
) -> tuple[float, ...]:
    """Solve 8-parameter perspective transform mapping output coords to input coords."""
    rows: list[list[float]] = []
    rhs: list[float] = []
    for (x, y), (u, v) in zip(out_pts, in_pts):
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        rhs.extend([u, v])
    coeffs = np.linalg.solve(np.asarray(rows, dtype=np.float64), np.asarray(rhs, dtype=np.float64))
    return tuple(float(c) for c in coeffs)


def _apply_geometry_warp(
    rgb: Image.Image,
    rng: np.random.Generator,
    severe: bool,
) -> Image.Image:
    """Apply slight rotation and perspective keystone warp."""
    w, h = rgb.size
    paper_fill = (240, 238, 232)
    max_angle = 3.8 if severe else 1.6
    angle = float(rng.uniform(-max_angle, max_angle))
    rotated = rgb.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=paper_fill)

    jitter = 0.045 if severe else 0.018
    dx = w * jitter
    dy = h * jitter
    out_pts = [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
    in_pts = [
        (float(rng.uniform(-dx, dx)), float(rng.uniform(-dy, dy))),
        (w + float(rng.uniform(-dx, dx)), float(rng.uniform(-dy, dy))),
        (w + float(rng.uniform(-dx, dx)), h + float(rng.uniform(-dy, dy))),
        (float(rng.uniform(-dx, dx)), h + float(rng.uniform(-dy, dy))),
    ]
    coeffs = _perspective_coeffs(out_pts, in_pts)
    return rotated.transform(
        (w, h),
        Image.Transform.PERSPECTIVE,
        coeffs,
        resample=Image.Resampling.BICUBIC,
        fillcolor=paper_fill,
    )


def _apply_shadow_and_noise(
    rgb: Image.Image,
    rng: np.random.Generator,
    severe: bool,
) -> Image.Image:
    """Apply contrast loss, uneven shadow illumination, blur, sensor noise, and JPEG artifacts."""
    contrast_factor = float(rng.uniform(0.46, 0.60) if severe else rng.uniform(0.78, 0.90))
    contrasted = ImageEnhance.Contrast(rgb).enhance(contrast_factor)
    blur_radius = float(rng.uniform(2.4, 3.2) if severe else rng.uniform(0.6, 1.1))
    blurred = contrasted.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    arr = np.asarray(blurred, dtype=np.float32)
    h, w, _ = arr.shape
    x_grad = np.linspace(float(rng.uniform(0.68, 0.88)), 1.02, w, dtype=np.float32)
    y_grad = np.linspace(1.0, float(rng.uniform(0.72, 0.92)), h, dtype=np.float32)
    illum = np.outer(y_grad, x_grad)[:, :, None]
    lit = arr * illum

    if severe:
        band_y1 = int(h * 0.25)
        band_y2 = int(h * 0.55)
        lit[band_y1:band_y2, int(w * 0.15):int(w * 0.85)] *= 0.42

    sigma = 16.0 if severe else 5.0
    noisy = np.clip(lit + rng.normal(0.0, sigma, arr.shape), 0.0, 255.0).astype(np.uint8)
    jpeg_img = Image.fromarray(noisy, mode="RGB")

    buf = io.BytesIO()
    jpeg_img.save(buf, format="JPEG", quality=(22 if severe else 58))
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def apply_scan_photo_effects(
    img: Image.Image,
    rng: np.random.Generator,
    severe: bool = False,
) -> Image.Image:
    """Simulate mobile scan/photo degradations (R1): tilt, perspective, lighting, blur, noise, JPEG."""
    rgb = img.convert("RGB")
    warped = _apply_geometry_warp(rgb, rng, severe)
    return _apply_shadow_and_noise(warped, rng, severe)


def _draw_ring_text(
    overlay: Image.Image,
    center: tuple[int, int],
    radius: int,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    color: tuple[int, int, int, int],
) -> None:
    """Render characters spaced along a circular arc onto an RGBA overlay."""
    cx, cy = center
    count = max(1, len(text))
    step = 360.0 / count
    for idx, ch in enumerate(text):
        angle_deg = idx * step - 90.0
        rad = np.deg2rad(angle_deg)
        px = int(cx + radius * np.cos(rad))
        py = int(cy + radius * np.sin(rad))
        glyph = Image.new("RGBA", (36, 36), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((18, 18), ch, font=font, fill=color, anchor="mm")
        rotated = glyph.rotate(-(angle_deg + 90.0), resample=Image.Resampling.BICUBIC)
        overlay.alpha_composite(rotated, dest=(px - 18, py - 18))


def apply_stamp_overlay(
    img: Image.Image,
    bbox: tuple[int, int, int, int],
    rng: np.random.Generator,
) -> Image.Image:
    """Overlay an abstract synthetic circular seal across bbox digits (R2)."""
    base = img.convert("RGBA")
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) // 2 + int(rng.integers(-8, 9))
    cy = (y1 + y2) // 2 + int(rng.integers(-6, 7))
    radius = max(54, min(96, max(x2 - x1, y2 - y1) // 2 + 34))

    ink = (44, 58, 178, int(rng.integers(125, 160)))
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=ink, width=4)
    inner_r = int(radius * 0.72)
    draw.ellipse([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r], outline=ink, width=2)

    ring_font = _load_font((FONT_BOLD, FONT_REGULAR), max(9, radius // 7))
    _draw_ring_text(overlay, (cx, cy), int(radius * 0.86), STAMP_TEXT, ring_font, ink)

    center_font = _load_font((FONT_BOLD, FONT_REGULAR), max(10, radius // 6))
    draw.text((cx, cy - 6), "SYNTHETIC", font=center_font, fill=ink, anchor="mm")
    draw.text((cx, cy + 10), "CARDIOIMPORT", font=center_font, fill=ink, anchor="mm")

    return Image.alpha_composite(base, overlay).convert("RGB")


def apply_handwritten_correction(
    img: Image.Image,
    bbox: tuple[int, int, int, int],
    new_text: str,
    rng: np.random.Generator,
) -> Image.Image:
    """Strike through bbox text and write handwritten replacement nearby (R3)."""
    out = img.convert("RGB")
    draw = ImageDraw.Draw(out)
    x1, y1, x2, y2 = bbox
    mid_y = (y1 + y2) // 2
    ink_color = (20, 48, 148)
    jitter = int(rng.integers(-3, 4))
    draw.line([(x1 - 4, mid_y + jitter), (x2 + 4, mid_y - jitter)], fill=ink_color, width=3)

    font_order = (FONT_COMIC, FONT_OBLIQUE, FONT_REGULAR) if new_text.isascii() else (FONT_OBLIQUE, FONT_REGULAR)
    font = _load_font(font_order, max(16, (y2 - y1) + 2))
    pos_x = min(out.width - 160, x2 + 10) if x2 + 160 < out.width else max(10, x1)
    pos_y = max(6, y1 - max(22, (y2 - y1) + 4)) if x2 + 160 >= out.width else max(6, y1 - 2)
    draw.text((pos_x, pos_y), new_text, font=font, fill=ink_color)
    return out


def apply_messenger_crop(img: Image.Image, rng: np.random.Generator) -> Image.Image:
    """Clip right and/or bottom edges of a messenger screenshot (R4)."""
    w, h = img.size
    crop_right = int(w * float(rng.uniform(0.05, 0.12)))
    crop_bottom = int(h * float(rng.uniform(0.03, 0.09)))
    new_w = max(int(w * 0.78), w - crop_right)
    new_h = max(int(h * 0.80), h - crop_bottom)
    return img.crop((0, 0, new_w, new_h)).convert("RGB")


def ensure_watermark(
    img: Image.Image,
    text: str = WATERMARK_TEXT,
) -> Image.Image:
    """Guarantee a visible synthetic watermark across the image and footer."""
    base = img.convert("RGBA")
    w, h = base.size

    diag_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    diag_font = _load_font((FONT_BOLD, FONT_REGULAR), max(20, min(w, h) // 22))
    stamp = Image.new("RGBA", (max(w, h), 90), (0, 0, 0, 0))
    ImageDraw.Draw(stamp).text((stamp.width // 2, 45), text, font=diag_font, fill=(170, 40, 40, 88), anchor="mm")
    rotated = stamp.rotate(28, expand=True, resample=Image.Resampling.BICUBIC)
    diag_layer.alpha_composite(rotated, dest=((w - rotated.width) // 2, (h - rotated.height) // 2))

    footer_font = _load_font((FONT_BOLD, FONT_REGULAR), max(13, w // 55))
    footer_draw = ImageDraw.Draw(diag_layer)
    footer_draw.rectangle([0, max(0, h - 28), w, h], fill=(245, 245, 245, 195))
    footer_draw.text((w // 2, h - 14), text, font=footer_font, fill=(140, 30, 30, 220), anchor="mm")

    return Image.alpha_composite(base, diag_layer).convert("RGB")
