from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw

from .image_io import infer_edge_color, load_master_icon, resized_png, rgb_to_hex


PREVIEW_SIZES = [1024, 128, 64, 32]


def qa_source(source: Path, out: Path) -> dict[str, Any]:
    image = load_master_icon(source, allow_crop=False)
    out.mkdir(parents=True, exist_ok=True)
    contact_sheet = out / f"{source.stem}-qa-contact-sheet.png"
    make_qa_contact_sheet(source, contact_sheet)

    alpha = image.getchannel("A")
    bbox = alpha.point(lambda value: 255 if value > 16 else 0).getbbox()
    corner_pixels = [
        image.getpixel((0, 0)),
        image.getpixel((image.width - 1, 0)),
        image.getpixel((0, image.height - 1)),
        image.getpixel((image.width - 1, image.height - 1)),
    ]
    edge_alpha = average_edge_alpha(image)
    edge_sides = edge_side_stats(image)
    edge_color = infer_edge_color(image)
    luminance_min, luminance_max = luminance_range(image)
    contrast_range = luminance_max - luminance_min
    border = uniform_border_thickness(image)

    content_margin = None
    content_coverage = 0.0
    if bbox:
        left, top, right, bottom = bbox
        content_margin = {
            "left": left,
            "top": top,
            "right": image.width - right,
            "bottom": image.height - bottom,
        }
        content_area = (right - left) * (bottom - top)
        content_coverage = round(content_area / float(image.width * image.height), 4)

    warnings = source_warnings(image, edge_alpha, corner_pixels, content_margin, contrast_range, border)
    return {
        "source": str(source),
        "ok": not warnings,
        "size": {"width": image.width, "height": image.height},
        "mode": image.mode,
        "edgeAlphaAverage": round(edge_alpha, 2),
        "edgeSides": edge_sides,
        "cornerAlpha": [pixel[3] for pixel in corner_pixels],
        "cornerRGBA": [list(pixel) for pixel in corner_pixels],
        "edgeColor": rgb_to_hex(edge_color),
        "uniformBorderThickness": border,
        "contentBoundingBox": bbox,
        "contentMargin": content_margin,
        "contentCoverage": content_coverage,
        "contrastRange": round(contrast_range, 2),
        "likelyVisibleFrameRisk": edge_alpha > 245 and content_coverage > 0.9,
        "bakedRoundedTileRisk": baked_rounded_tile_risk(image, corner_pixels),
        "blackFieldRisk": black_or_white_field_risk(edge_color, edge_alpha, border, dark=True),
        "whiteFieldRisk": black_or_white_field_risk(edge_color, edge_alpha, border, dark=False),
        "fullBleedSuitability": full_bleed_suitability(image, border, edge_alpha, content_margin),
        "contactSheet": str(contact_sheet),
        "warnings": warnings,
    }


def make_qa_contact_sheet(source: Path, out: Path) -> str:
    image = load_master_icon(source, allow_crop=False)
    out.parent.mkdir(parents=True, exist_ok=True)

    tile = 180
    label_h = 24
    backgrounds = [
        ("light", (246, 246, 244)),
        ("dark", (26, 28, 32)),
        ("dock", (126, 136, 148)),
        ("mask", (238, 238, 236)),
        ("edge", (250, 250, 250)),
    ]
    sheet = Image.new("RGB", (tile * len(PREVIEW_SIZES), (tile + label_h) * len(backgrounds)), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)

    for row, (label, bg) in enumerate(backgrounds):
        for col, size in enumerate(PREVIEW_SIZES):
            x = col * tile
            y = row * (tile + label_h)
            if label == "edge":
                preview = render_edge_strip(image, tile)
            else:
                preview = render_preview_tile(image, size, tile, bg, rounded=(label == "mask"))
            sheet.paste(preview, (x, y))
            draw.text((x + 8, y + tile + 5), f"{label} {size}px", fill=(20, 20, 20))

    sheet.save(out, format="PNG", optimize=True)
    return str(out)


def make_contact_sheet(sources: list[Path], out: Path) -> str:
    if not sources:
        raise ValueError("at least one source is required")

    tile = 220
    label_h = 28
    sheet = Image.new("RGB", (tile * len(sources), tile + label_h), (250, 250, 250))
    draw = ImageDraw.Draw(sheet)

    for index, source in enumerate(sources):
        image = load_master_icon(source, allow_crop=False)
        preview = render_preview_tile(image, 160, tile, (244, 244, 242), rounded=True)
        x = index * tile
        sheet.paste(preview, (x, 0))
        draw.text((x + 8, tile + 6), source.name[:28], fill=(20, 20, 20))

    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, format="PNG", optimize=True)
    return str(out)


def render_preview_tile(
    image: Image.Image,
    preview_size: int,
    tile: int,
    bg: tuple[int, int, int],
    *,
    rounded: bool,
) -> Image.Image:
    canvas = Image.new("RGB", (tile, tile), bg)
    icon = resized_png(image, preview_size, flatten=False)
    if rounded:
        mask = Image.new("L", icon.size, 0)
        draw = ImageDraw.Draw(mask)
        radius = max(4, int(preview_size * 0.22))
        draw.rounded_rectangle((0, 0, preview_size - 1, preview_size - 1), radius=radius, fill=255)
        icon.putalpha(ImageChops.multiply(icon.getchannel("A"), mask))

    x = (tile - preview_size) // 2
    y = (tile - preview_size) // 2
    canvas.paste(icon, (x, y), icon)
    return canvas


def average_edge_alpha(image: Image.Image) -> float:
    alpha = image.getchannel("A")
    values: list[int] = []
    for x in range(image.width):
        values.append(alpha.getpixel((x, 0)))
        values.append(alpha.getpixel((x, image.height - 1)))
    for y in range(image.height):
        values.append(alpha.getpixel((0, y)))
        values.append(alpha.getpixel((image.width - 1, y)))
    return sum(values) / len(values)


def edge_side_stats(image: Image.Image) -> dict[str, dict[str, object]]:
    rgba = image.convert("RGBA")
    sides = {
        "top": [rgba.getpixel((x, 0)) for x in range(rgba.width)],
        "bottom": [rgba.getpixel((x, rgba.height - 1)) for x in range(rgba.width)],
        "left": [rgba.getpixel((0, y)) for y in range(rgba.height)],
        "right": [rgba.getpixel((rgba.width - 1, y)) for y in range(rgba.height)],
    }
    payload: dict[str, dict[str, object]] = {}
    for side, pixels in sides.items():
        alpha = sum(pixel[3] for pixel in pixels) / len(pixels)
        visible = [pixel for pixel in pixels if pixel[3] > 16] or pixels
        color = (
            sum(pixel[0] for pixel in visible) // len(visible),
            sum(pixel[1] for pixel in visible) // len(visible),
            sum(pixel[2] for pixel in visible) // len(visible),
        )
        payload[side] = {"alphaAverage": round(alpha, 2), "color": rgb_to_hex(color)}
    return payload


def uniform_border_thickness(image: Image.Image, *, tolerance: int = 10, limit: int = 256) -> dict[str, int]:
    rgba = image.convert("RGBA")
    corner_color = infer_edge_color(rgba)
    return {
        "top": scan_uniform_rows(rgba, corner_color, tolerance, limit, top=True),
        "bottom": scan_uniform_rows(rgba, corner_color, tolerance, limit, top=False),
        "left": scan_uniform_cols(rgba, corner_color, tolerance, limit, left=True),
        "right": scan_uniform_cols(rgba, corner_color, tolerance, limit, left=False),
    }


def scan_uniform_rows(image: Image.Image, color: tuple[int, int, int], tolerance: int, limit: int, *, top: bool) -> int:
    count = 0
    max_rows = min(limit, image.height)
    for offset in range(max_rows):
        y = offset if top else image.height - 1 - offset
        if uniform_pixels([image.getpixel((x, y)) for x in range(image.width)], color, tolerance):
            count += 1
        else:
            break
    return count


def scan_uniform_cols(image: Image.Image, color: tuple[int, int, int], tolerance: int, limit: int, *, left: bool) -> int:
    count = 0
    max_cols = min(limit, image.width)
    for offset in range(max_cols):
        x = offset if left else image.width - 1 - offset
        if uniform_pixels([image.getpixel((x, y)) for y in range(image.height)], color, tolerance):
            count += 1
        else:
            break
    return count


def uniform_pixels(pixels: list[tuple[int, int, int, int]], color: tuple[int, int, int], tolerance: int) -> bool:
    visible = [pixel for pixel in pixels if pixel[3] > 245]
    if len(visible) < max(1, int(len(pixels) * 0.95)):
        return False
    for red, green, blue, _alpha in visible:
        if max(abs(red - color[0]), abs(green - color[1]), abs(blue - color[2])) > tolerance:
            return False
    return True


def baked_rounded_tile_risk(image: Image.Image, corner_pixels: list[tuple[int, int, int, int]]) -> bool:
    if not any(pixel[3] < 32 for pixel in corner_pixels):
        return False
    center_edges = [
        image.getpixel((image.width // 2, 0))[3],
        image.getpixel((image.width // 2, image.height - 1))[3],
        image.getpixel((0, image.height // 2))[3],
        image.getpixel((image.width - 1, image.height // 2))[3],
    ]
    return all(alpha > 220 for alpha in center_edges)


def black_or_white_field_risk(
    edge_color: tuple[int, int, int],
    edge_alpha: float,
    border: dict[str, int],
    *,
    dark: bool,
) -> bool:
    if edge_alpha < 245 or max(border.values(), default=0) < 8:
        return False
    luminance = 0.2126 * edge_color[0] + 0.7152 * edge_color[1] + 0.0722 * edge_color[2]
    return luminance < 24 if dark else luminance > 232


def full_bleed_suitability(
    image: Image.Image,
    border: dict[str, int],
    edge_alpha: float,
    content_margin: dict[str, int] | None,
) -> str:
    if image.width != 1024 or image.height != 1024:
        return "needs-1024-normalization"
    if max(border.values(), default=0) >= 16:
        return "risk-uniform-border"
    if content_margin and min(content_margin.values()) < 16:
        return "risk-mask-crop"
    if edge_alpha < 16:
        return "transparent-edge"
    return "full-bleed-ok"


def render_edge_strip(image: Image.Image, tile: int) -> Image.Image:
    rgba = image.convert("RGBA")
    strip = Image.new("RGB", (tile, tile), (250, 250, 250))
    crop = 24
    samples = [
        rgba.crop((0, 0, rgba.width, crop)),
        rgba.crop((0, rgba.height - crop, rgba.width, rgba.height)),
        rgba.crop((0, 0, crop, rgba.height)).resize((rgba.width, crop), Image.Resampling.NEAREST),
        rgba.crop((rgba.width - crop, 0, rgba.width, rgba.height)).resize((rgba.width, crop), Image.Resampling.NEAREST),
    ]
    y = 16
    for sample in samples:
        preview = sample.resize((tile - 32, 34), Image.Resampling.NEAREST).convert("RGB")
        strip.paste(preview, (16, y))
        y += 40
    return strip


def luminance_range(image: Image.Image) -> tuple[float, float]:
    rgb = image.convert("RGB").resize((64, 64), Image.Resampling.LANCZOS)
    values = []
    for r, g, b in rgb.getdata():
        values.append(0.2126 * r + 0.7152 * g + 0.0722 * b)
    return min(values), max(values)


def source_warnings(
    image: Image.Image,
    edge_alpha: float,
    corner_pixels: list[tuple[int, int, int, int]],
    content_margin: dict[str, int] | None,
    contrast_range: float,
    border: dict[str, int],
) -> list[str]:
    warnings: list[str] = []
    if image.width != 1024 or image.height != 1024:
        warnings.append(f"source is {image.width}x{image.height}; normalize master to 1024x1024 before platform export")
    if edge_alpha > 245 and all(pixel[3] > 245 for pixel in corner_pixels):
        warnings.append("source edges are fully opaque; verify this is intentional for the target platform")
    if max(border.values(), default=0) >= 16:
        warnings.append("source has a likely uniform outer field; verify this is not an icon inside an icon")
    if baked_rounded_tile_risk(image, corner_pixels):
        warnings.append("source may contain a baked rounded tile inside the canvas")
    if content_margin and min(content_margin.values()) < 32:
        warnings.append("significant content is close to the edge; small sizes or system masks may crop it")
    if contrast_range < 35:
        warnings.append("low luminance contrast; icon may be hard to read at 32px")
    return warnings
