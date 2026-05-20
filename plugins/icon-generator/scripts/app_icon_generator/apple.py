from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from time import strftime

from .image_io import WriteResult, infer_edge_color, load_master_icon, remove_with_backup, resized_png, write_png, write_text
from .path_filters import is_backup_path


@dataclass(frozen=True)
class AppleIconSpec:
    idiom: str
    point_size: str
    scale: str
    pixels: int
    filename: str


IOS_ICON_SPECS = [
    AppleIconSpec("iphone", "20x20", "2x", 40, "Icon-App-20x20@2x.png"),
    AppleIconSpec("iphone", "20x20", "3x", 60, "Icon-App-20x20@3x.png"),
    AppleIconSpec("iphone", "29x29", "2x", 58, "Icon-App-29x29@2x.png"),
    AppleIconSpec("iphone", "29x29", "3x", 87, "Icon-App-29x29@3x.png"),
    AppleIconSpec("iphone", "40x40", "2x", 80, "Icon-App-40x40@2x.png"),
    AppleIconSpec("iphone", "40x40", "3x", 120, "Icon-App-40x40@3x.png"),
    AppleIconSpec("iphone", "60x60", "2x", 120, "Icon-App-60x60@2x.png"),
    AppleIconSpec("iphone", "60x60", "3x", 180, "Icon-App-60x60@3x.png"),
    AppleIconSpec("ipad", "20x20", "1x", 20, "Icon-App-20x20@1x.png"),
    AppleIconSpec("ipad", "20x20", "2x", 40, "Icon-App-20x20@2x~ipad.png"),
    AppleIconSpec("ipad", "29x29", "1x", 29, "Icon-App-29x29@1x.png"),
    AppleIconSpec("ipad", "29x29", "2x", 58, "Icon-App-29x29@2x~ipad.png"),
    AppleIconSpec("ipad", "40x40", "1x", 40, "Icon-App-40x40@1x.png"),
    AppleIconSpec("ipad", "40x40", "2x", 80, "Icon-App-40x40@2x~ipad.png"),
    AppleIconSpec("ipad", "76x76", "1x", 76, "Icon-App-76x76@1x.png"),
    AppleIconSpec("ipad", "76x76", "2x", 152, "Icon-App-76x76@2x.png"),
    AppleIconSpec("ipad", "83.5x83.5", "2x", 167, "Icon-App-83.5x83.5@2x.png"),
    AppleIconSpec("ios-marketing", "1024x1024", "1x", 1024, "Icon-App-1024x1024@1x.png"),
]

MACOS_ICON_SPECS = [
    AppleIconSpec("mac", "16x16", "1x", 16, "icon_16x16.png"),
    AppleIconSpec("mac", "16x16", "2x", 32, "icon_16x16@2x.png"),
    AppleIconSpec("mac", "32x32", "1x", 32, "icon_32x32.png"),
    AppleIconSpec("mac", "32x32", "2x", 64, "icon_32x32@2x.png"),
    AppleIconSpec("mac", "128x128", "1x", 128, "icon_128x128.png"),
    AppleIconSpec("mac", "128x128", "2x", 256, "icon_128x128@2x.png"),
    AppleIconSpec("mac", "256x256", "1x", 256, "icon_256x256.png"),
    AppleIconSpec("mac", "256x256", "2x", 512, "icon_256x256@2x.png"),
    AppleIconSpec("mac", "512x512", "1x", 512, "icon_512x512.png"),
    AppleIconSpec("mac", "512x512", "2x", 1024, "icon_512x512@2x.png"),
]


def generate_apple_icons(
    root: Path,
    source: Path,
    *,
    platform: str,
    backup: bool,
    backup_root: Path | None = None,
    allow_crop: bool = False,
) -> WriteResult:
    if platform not in {"ios", "macos", "both"}:
        raise ValueError("platform must be ios, macos, or both")

    image = load_master_icon(source, allow_crop=allow_crop)
    background = infer_edge_color(image)
    changed_files: list[str] = []
    backup_files: list[str] = []
    removed_files: list[str] = []
    timestamp = strftime("%Y%m%d-%H%M%S")

    if platform in {"ios", "both"}:
        appiconset = find_appiconset(root, platform="ios", fallback_name="AppIcon.appiconset")
        write_appiconset(
            image,
            appiconset,
            IOS_ICON_SPECS,
            root=root,
            backup=backup,
            backup_root=backup_root,
            timestamp=timestamp,
            background=background,
            changed_files=changed_files,
            backup_files=backup_files,
            removed_files=removed_files,
        )

    if platform in {"macos", "both"}:
        fallback = "AppIcon.appiconset" if platform == "macos" else "MacAppIcon.appiconset"
        appiconset = find_appiconset(root, platform="macos", fallback_name=fallback)
        write_appiconset(
            image,
            appiconset,
            MACOS_ICON_SPECS,
            root=root,
            backup=backup,
            backup_root=backup_root,
            timestamp=timestamp,
            background=background,
            changed_files=changed_files,
            backup_files=backup_files,
            removed_files=removed_files,
        )

    return WriteResult(changed_files=changed_files, backup_files=backup_files, removed_files=removed_files)


def write_appiconset(
    image,
    appiconset: Path,
    specs: list[AppleIconSpec],
    *,
    root: Path,
    backup: bool,
    backup_root: Path | None,
    timestamp: str,
    background: tuple[int, int, int],
    changed_files: list[str],
    backup_files: list[str],
    removed_files: list[str],
) -> None:
    cleanup_incompatible_icons(
        appiconset,
        specs,
        root=root,
        backup=backup,
        backup_root=backup_root,
        timestamp=timestamp,
        backup_files=backup_files,
        removed_files=removed_files,
    )

    for spec in specs:
        icon = resized_png(image, spec.pixels, flatten=True, background=background)
        write_png(
            icon,
            appiconset / spec.filename,
            root=root,
            backup=backup,
            backup_root=backup_root,
            timestamp=timestamp,
            changed_files=changed_files,
            backup_files=backup_files,
        )

    contents = {
        "images": [
            {
                "idiom": spec.idiom,
                "size": spec.point_size,
                "scale": spec.scale,
                "filename": spec.filename,
            }
            for spec in specs
        ],
        "info": {
            "author": "xcode",
            "version": 1,
        },
    }
    write_text(
        json.dumps(contents, indent=2) + "\n",
        appiconset / "Contents.json",
        root=root,
        backup=backup,
        backup_root=backup_root,
        timestamp=timestamp,
        changed_files=changed_files,
        backup_files=backup_files,
    )


def find_appiconset(root: Path, *, platform: str, fallback_name: str) -> Path:
    root = root.resolve()
    active = active_appiconset_for_platform(root, platform)
    if active:
        return active

    for appiconset in sorted(root.rglob("*.appiconset")):
        if is_backup_path(appiconset):
            continue
        contents = appiconset / "Contents.json"
        if not contents.exists():
            if appiconset.name == fallback_name:
                return appiconset
            continue
        try:
            payload = json.loads(contents.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        idioms = {image.get("idiom") for image in payload.get("images", [])}
        if platform == "macos" and "mac" in idioms:
            return appiconset
        if platform == "ios" and ({"iphone", "ipad", "ios-marketing"} & idioms):
            return appiconset

    catalog = find_asset_catalog(root, platform=platform)
    return catalog / fallback_name


def active_appiconset_for_platform(root: Path, platform: str) -> Path | None:
    try:
        from .active_icon import inspect_active_icon
    except ImportError:
        return None
    report = inspect_active_icon(root, platform="apple", run_xcodebuild=False)
    path_value = report.get("activeAppIconSetPath")
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.exists() or is_backup_path(path):
        return None
    idioms = appiconset_idioms(path)
    if platform == "macos" and "mac" in idioms:
        return path
    if platform == "ios" and ({"iphone", "ipad", "ios-marketing"} & idioms):
        return path
    if platform == "both":
        return None
    return None


def appiconset_idioms(appiconset: Path) -> set[str]:
    contents = appiconset / "Contents.json"
    if not contents.exists():
        return set()
    try:
        payload = json.loads(contents.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return {str(image.get("idiom")) for image in payload.get("images", []) if image.get("idiom")}


def find_asset_catalog(root: Path, *, platform: str) -> Path:
    catalogs = [
        path
        for path in sorted(root.rglob("*.xcassets"))
        if not is_backup_path(path)
    ]
    if catalogs:
        preferred = [
            path
            for path in catalogs
            if (platform == "macos" and "macos" in str(path).lower())
            or (platform == "ios" and "macos" not in str(path).lower())
        ]
        return (preferred or catalogs)[0]
    return root / "Assets.xcassets"


def cleanup_incompatible_icons(
    appiconset: Path,
    specs: list[AppleIconSpec],
    *,
    root: Path,
    backup: bool,
    backup_root: Path | None,
    timestamp: str,
    backup_files: list[str],
    removed_files: list[str],
) -> None:
    expected_names = {spec.filename for spec in specs}
    target_is_macos = any(spec.idiom == "mac" for spec in specs)
    incompatible_prefixes = ("Icon-App-",) if target_is_macos else ("icon_",)

    for path in sorted(appiconset.glob("*.png")):
        if path.name in expected_names:
            continue
        if path.name.startswith(incompatible_prefixes):
            remove_with_backup(
                path,
                root=root,
                backup=backup,
                backup_root=backup_root,
                timestamp=timestamp,
                backup_files=backup_files,
                removed_files=removed_files,
            )
