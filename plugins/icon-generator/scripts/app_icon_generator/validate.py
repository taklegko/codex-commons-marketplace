from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from PIL import Image

from .active_icon import inspect_active_icon
from .android import ADAPTIVE_FOREGROUND_SIZE, LEGACY_LAUNCHER_SPECS, find_res_dir
from .detect_project import detect_project
from .path_filters import backup_history, is_backup_path, is_ignored_scan_path
from .runtime_icons import backup_dirs, discover_runtime_icons, runtime_resources_match_source
from .web import has_web_favicon_files, validate_web_favicons


def validate_project(root: Path) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    detection = detect_project(root)
    active_icon = inspect_active_icon(root, platform="auto", run_xcodebuild=False)

    apple_sets = validate_apple_appiconsets(root, errors)
    runtime_report = discover_runtime_icons(root)
    runtime_warnings = validate_runtime_icons(root, runtime_report, active_icon)
    warnings.extend(runtime_warnings)
    warnings.extend(validate_backup_gitignore(root))
    should_validate_android = "android" in detection.platforms or has_android_icon_files(root)
    android = validate_android_icons(root, errors, warnings) if should_validate_android else {
        "resDir": str(find_res_dir(root)),
        "found": False,
        "checked": [],
    }
    should_validate_web = "web" in detection.platforms or has_web_favicon_files(root)
    web = validate_web_favicons(root, errors, warnings) if should_validate_web else {
        "found": False,
        "checked": [],
    }

    return {
        "root": str(root),
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "runtimeIconOverrides": [item.to_dict() for item in runtime_report.runtime_icon_overrides],
        "additionalIconResources": [item.to_dict() for item in runtime_report.additional_icon_resources],
        "activeIcon": active_icon,
        "inactiveAssetCatalogs": active_icon.get("inactiveAssetCatalogs", []),
        "sourceCandidates": active_icon.get("sourceCandidates", []),
        "buildArtifacts": active_icon.get("buildArtifacts", []),
        "ignoredHistory": active_icon.get("ignoredHistory", backup_history(root)),
        "appleAppIconSets": apple_sets,
        "android": android,
        "web": web,
    }


def validate_runtime_icons(root: Path, runtime_report, active_icon: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if runtime_report.runtime_icon_overrides:
        warnings.append(
            "Runtime app icon override detected; updating AppIcon.appiconset alone may not change the visible app icon."
        )
    if runtime_report.additional_icon_resources:
        names = ", ".join(resource.name for resource in runtime_report.additional_icon_resources)
        warnings.append(f"Additional icon-like runtime resources found: {names}. Ask before syncing them.")
        source = representative_apple_icon_source(root, active_icon)
        warnings.extend(runtime_resources_match_source(root, source, runtime_report.additional_icon_resources))
    return warnings


def representative_apple_icon_source(root: Path, active_icon: dict[str, Any] | None = None) -> Path | None:
    if active_icon:
        master = active_icon.get("activeMasterPng")
        if isinstance(master, dict):
            path = Path(str(master.get("path", "")))
            if path.exists() and not is_backup_path(path):
                return path
    candidates = [
        "icon_512x512@2x.png",
        "Icon-App-1024x1024@1x.png",
        "icon_512x512.png",
    ]
    for appiconset in sorted(root.rglob("*.appiconset")):
        if is_backup_path(appiconset) or is_ignored_scan_path(appiconset):
            continue
        for filename in candidates:
            path = appiconset / filename
            if path.exists():
                return path
    return None


def validate_backup_gitignore(root: Path) -> list[str]:
    warnings: list[str] = []
    ignored = read_gitignore(root)
    for backup_dir in backup_dirs(root):
        pattern = f"{backup_dir.name}/"
        if backup_dir.name not in ignored and pattern not in ignored:
            warnings.append(
                "Safety backup directory is present but not listed in .gitignore: "
                f"{backup_dir.name}/. It is a copy of old active files before overwrite; "
                "it does not affect the active icon and is not used as a source."
            )
    return warnings


def read_gitignore(root: Path) -> set[str]:
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return set()
    return {
        line.strip()
        for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def has_android_icon_files(root: Path) -> bool:
    return any(
        path.name.startswith("ic_launcher")
        for path in root.rglob("*")
        if path.is_file() and not is_ignored_scan_path(path)
    )


def validate_apple_appiconsets(root: Path, errors: list[str]) -> list[str]:
    appiconsets = [
        path
        for path in sorted(root.rglob("*.appiconset"))
        if not is_backup_path(path) and not is_ignored_scan_path(path)
    ]
    valid_sets: list[str] = []

    for appiconset in appiconsets:
        contents_path = appiconset / "Contents.json"
        if not contents_path.exists():
            errors.append(f"Missing Contents.json in {appiconset}")
            continue
        try:
            contents = json.loads(contents_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid Contents.json in {appiconset}: {exc}")
            continue

        for entry in contents.get("images", []):
            filename = entry.get("filename")
            size = entry.get("size")
            scale = entry.get("scale")
            if not filename or not size or not scale:
                errors.append(f"Incomplete image entry in {contents_path}: {entry}")
                continue
            expected_pixels = apple_pixels(size, scale)
            image_path = appiconset / filename
            validate_png_size(image_path, expected_pixels, errors)

        valid_sets.append(str(appiconset))

    return valid_sets


def validate_android_icons(root: Path, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    res_dir = find_res_dir(root)
    found = res_dir.exists()
    checked: list[str] = []

    if not found:
        warnings.append(f"Android res directory was not found: {res_dir}")
        return {"resDir": str(res_dir), "found": False, "checked": checked}

    for density, pixels in LEGACY_LAUNCHER_SPECS.items():
        path = res_dir / density / "ic_launcher.png"
        validate_png_size(path, pixels, errors)
        checked.append(str(path))

    foreground = res_dir / "drawable" / "ic_launcher_foreground.png"
    validate_png_size(foreground, ADAPTIVE_FOREGROUND_SIZE, errors)
    checked.append(str(foreground))

    for xml_path in [
        res_dir / "drawable" / "ic_launcher_background.xml",
        res_dir / "mipmap-anydpi-v26" / "ic_launcher.xml",
    ]:
        validate_xml(xml_path, errors)
        checked.append(str(xml_path))

    return {"resDir": str(res_dir), "found": True, "checked": checked}


def apple_pixels(size: str, scale: str) -> int:
    point_size = float(size.split("x", 1)[0])
    scale_factor = int(scale.removesuffix("x"))
    return int(point_size * scale_factor)


def validate_png_size(path: Path, expected_pixels: int, errors: list[str]) -> None:
    if not path.exists():
        errors.append(f"Missing PNG: {path}")
        return

    try:
        with Image.open(path) as image:
            if image.size != (expected_pixels, expected_pixels):
                errors.append(f"Wrong PNG size for {path}: expected {expected_pixels}x{expected_pixels}, got {image.size[0]}x{image.size[1]}")
    except OSError as exc:
        errors.append(f"Invalid PNG {path}: {exc}")


def validate_xml(path: Path, errors: list[str]) -> None:
    if not path.exists():
        errors.append(f"Missing XML: {path}")
        return

    try:
        ET.parse(path)
    except ET.ParseError as exc:
        errors.append(f"Invalid XML {path}: {exc}")
