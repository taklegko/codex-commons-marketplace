from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from shutil import copy2
from time import strftime
from typing import Iterable

from PIL import Image


class IconImageError(ValueError):
    """Raised when a source image cannot safely be used as an app icon."""


@dataclass(frozen=True)
class WriteResult:
    changed_files: list[str] = field(default_factory=list)
    backup_files: list[str] = field(default_factory=list)
    removed_files: list[str] = field(default_factory=list)


def load_master_icon(source: Path, *, allow_crop: bool = False) -> Image.Image:
    if source.suffix.lower() != ".png":
        raise IconImageError(f"Source image must be a PNG: {source}")

    try:
        image = Image.open(source)
        image.load()
    except OSError as exc:
        raise IconImageError(f"Source image is not a valid PNG: {source}") from exc

    image = image.convert("RGBA")
    width, height = image.size
    if width != height:
        if not allow_crop:
            raise IconImageError(
                f"Source image must be square; got {width}x{height}. "
                "Ask for approval and retry with --allow-crop."
            )
        image = center_crop_square(image)

    return image


def center_crop_square(image: Image.Image) -> Image.Image:
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return image.crop((left, top, left + side, top + side))


def resized_png(
    image: Image.Image,
    size: int,
    *,
    flatten: bool = False,
    background: tuple[int, int, int] | None = None,
) -> Image.Image:
    resized = image.resize((size, size), Image.Resampling.LANCZOS)
    if not flatten:
        return resized

    rgb_background = background or infer_edge_color(image)
    canvas = Image.new("RGBA", resized.size, (*rgb_background, 255))
    canvas.alpha_composite(resized)
    return canvas.convert("RGB")


def write_png(
    image: Image.Image,
    path: Path,
    *,
    root: Path,
    backup: bool,
    backup_root: Path | None = None,
    timestamp: str | None = None,
    changed_files: list[str],
    backup_files: list[str],
) -> None:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    payload = buffer.getvalue()
    if path.exists() and path.read_bytes() == payload:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    backup_existing(path, root=root, backup=backup, backup_root=backup_root, timestamp=timestamp, backup_files=backup_files)
    path.write_bytes(payload)
    changed_files.append(str(path))


def write_text(
    text: str,
    path: Path,
    *,
    root: Path,
    backup: bool,
    backup_root: Path | None = None,
    timestamp: str | None = None,
    changed_files: list[str],
    backup_files: list[str],
) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    backup_existing(path, root=root, backup=backup, backup_root=backup_root, timestamp=timestamp, backup_files=backup_files)
    path.write_text(text, encoding="utf-8")
    changed_files.append(str(path))


def backup_existing(
    path: Path,
    *,
    root: Path,
    backup: bool,
    backup_root: Path | None = None,
    timestamp: str | None,
    backup_files: list[str],
) -> None:
    if not backup or not path.exists():
        return

    destination_root = backup_root or (root / ".icon-generator-backups")
    destination_root = destination_root / (timestamp or strftime("%Y%m%d-%H%M%S"))
    relative = path.resolve().relative_to(root.resolve())
    backup_path = destination_root / relative
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    copy2(path, backup_path)
    backup_files.append(str(backup_path))


def remove_with_backup(
    path: Path,
    *,
    root: Path,
    backup: bool,
    backup_root: Path | None = None,
    timestamp: str | None = None,
    backup_files: list[str],
    removed_files: list[str],
) -> None:
    if not path.exists():
        return
    backup_existing(path, root=root, backup=backup, backup_root=backup_root, timestamp=timestamp, backup_files=backup_files)
    path.unlink()
    removed_files.append(str(path))


def infer_edge_color(image: Image.Image) -> tuple[int, int, int]:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    samples: list[tuple[int, int, int, int]] = []

    for x in range(width):
        samples.append(rgba.getpixel((x, 0)))
        samples.append(rgba.getpixel((x, height - 1)))
    for y in range(height):
        samples.append(rgba.getpixel((0, y)))
        samples.append(rgba.getpixel((width - 1, y)))

    return average_visible_rgb(samples) or (255, 255, 255)


def average_visible_rgb(samples: Iterable[tuple[int, int, int, int]]) -> tuple[int, int, int] | None:
    red = green = blue = count = 0
    for r, g, b, alpha in samples:
        if alpha < 16:
            continue
        red += r
        green += g
        blue += b
        count += 1

    if count == 0:
        return None
    return red // count, green // count, blue // count


def rgb_to_hex(color: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*color)
