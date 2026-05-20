from __future__ import annotations

from pathlib import Path
from time import strftime

from .image_io import WriteResult, infer_edge_color, load_master_icon, resized_png, rgb_to_hex, write_png, write_text
from .path_filters import is_ignored_scan_path


LEGACY_LAUNCHER_SPECS = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}

ADAPTIVE_FOREGROUND_SIZE = 432


def generate_android_icons(
    root: Path,
    source: Path,
    *,
    backup: bool,
    backup_root: Path | None = None,
    allow_crop: bool = False,
) -> WriteResult:
    root = root.resolve()
    res_dir = find_res_dir(root)
    image = load_master_icon(source, allow_crop=allow_crop)
    background = infer_edge_color(image)
    background_hex = rgb_to_hex(background)
    changed_files: list[str] = []
    backup_files: list[str] = []
    timestamp = strftime("%Y%m%d-%H%M%S")

    for density, pixels in LEGACY_LAUNCHER_SPECS.items():
        icon = resized_png(image, pixels, flatten=False)
        write_png(
            icon,
            res_dir / density / "ic_launcher.png",
            root=root,
            backup=backup,
            backup_root=backup_root,
            timestamp=timestamp,
            changed_files=changed_files,
            backup_files=backup_files,
        )

    foreground = resized_png(image, ADAPTIVE_FOREGROUND_SIZE, flatten=False)
    write_png(
        foreground,
        res_dir / "drawable" / "ic_launcher_foreground.png",
        root=root,
        backup=backup,
        backup_root=backup_root,
        timestamp=timestamp,
        changed_files=changed_files,
        backup_files=backup_files,
    )
    write_text(
        adaptive_background_xml(background_hex),
        res_dir / "drawable" / "ic_launcher_background.xml",
        root=root,
        backup=backup,
        backup_root=backup_root,
        timestamp=timestamp,
        changed_files=changed_files,
        backup_files=backup_files,
    )
    write_text(
        adaptive_icon_xml(),
        res_dir / "mipmap-anydpi-v26" / "ic_launcher.xml",
        root=root,
        backup=backup,
        backup_root=backup_root,
        timestamp=timestamp,
        changed_files=changed_files,
        backup_files=backup_files,
    )

    return WriteResult(changed_files=changed_files, backup_files=backup_files)


def find_res_dir(root: Path) -> Path:
    preferred = [
        root / "app" / "src" / "main" / "res",
        root / "android" / "app" / "src" / "main" / "res",
        root / "src" / "main" / "res",
    ]
    for path in preferred:
        if path.exists():
            return path

    discovered = [
        path
        for path in sorted(root.rglob("src/main/res"))
        if not is_ignored_scan_path(path)
    ]
    if discovered:
        return discovered[0]

    return root / "app" / "src" / "main" / "res"


def adaptive_icon_xml() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
</adaptive-icon>
"""


def adaptive_background_xml(color: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">
    <solid android:color="{color}" />
</shape>
"""
