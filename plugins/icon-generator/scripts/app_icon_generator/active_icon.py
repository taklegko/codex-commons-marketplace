from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
from typing import Any

from PIL import Image

from .android import find_res_dir
from .image_io import resized_png
from .path_filters import assert_safe_generated_output_path, backup_history, is_backup_path, is_build_artifact_path, is_generated_source_path, is_ignored_scan_path
from .web import inspect_active_web_icon, is_web_project
from .xcode_inspect import XcodeTargetInfo, inspect_xcode_project


ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def inspect_active_icon(root: Path, *, platform: str = "auto", run_xcodebuild: bool = True) -> dict[str, Any]:
    root = root.resolve()
    warnings: list[str] = []
    source_candidates = source_candidate_report(root)
    build_artifacts = build_artifact_report(root)
    ignored_history = backup_history(root)

    selected_platform = platform
    if platform == "auto":
        if [path for path in root.rglob("*.xcodeproj") if not is_ignored_scan_path(path)]:
            selected_platform = "apple"
        elif find_android_manifest(root):
            selected_platform = "android"
        elif is_web_project(root):
            selected_platform = "web"
        else:
            selected_platform = "unknown"

    if selected_platform in {"ios", "macos", "apple"}:
        payload = inspect_active_apple_icon(root, run_xcodebuild=run_xcodebuild)
    elif selected_platform == "android":
        payload = inspect_active_android_icon(root)
    elif selected_platform == "web":
        payload = inspect_active_web_icon(root)
    else:
        payload = {
            "activeTarget": None,
            "activeAppIconName": None,
            "activeAppIconSetPath": None,
            "activeMasterPng": None,
            "confidence": 0.0,
            "warnings": ["No active icon target detected."],
        }

    source_options = build_source_options(payload, source_candidates)
    return {
        "root": str(root),
        **payload,
        "sourceCandidates": source_candidates,
        "sourceOptions": source_options,
        "sourceSelection": source_selection_payload(payload, source_options),
        "buildArtifacts": build_artifacts,
        "ignoredHistory": ignored_history,
        "warnings": [*payload.get("warnings", []), *warnings],
    }


def inspect_active_apple_icon(root: Path, *, run_xcodebuild: bool = True) -> dict[str, Any]:
    targets = inspect_xcode_project(root, run_xcodebuild=run_xcodebuild)
    warnings: list[str] = []
    if not targets:
        return {
            "activeTarget": None,
            "activeAppIconName": None,
            "activeAppIconSetPath": None,
            "activeMasterPng": None,
            "confidence": 0.0,
            "warnings": ["No Xcode project found."],
        }

    target = choose_active_xcode_target(targets)
    app_icon_name = target.app_icon_name
    confidence = target.confidence
    if not app_icon_name:
        app_icon_name = "AppIcon"
        confidence = min(confidence, 0.55)
        warnings.append("Active app icon name is missing from build settings; using AppIcon fallback.")

    project_root = Path(target.project_root)
    appiconset = find_named_appiconset(project_root, app_icon_name)
    icon_bundle = find_named_icon_composer_bundle(project_root, app_icon_name)
    if not appiconset and app_icon_name != "AppIcon":
        fallback = find_named_appiconset(project_root, "AppIcon")
        if fallback:
            appiconset = fallback
            confidence = min(confidence, 0.45)
            warnings.append(f"Active app icon set {app_icon_name}.appiconset was not found; using AppIcon.appiconset fallback.")
        if not icon_bundle:
            icon_bundle = find_named_icon_composer_bundle(project_root, "AppIcon")
    if not appiconset:
        if icon_bundle:
            master = choose_icon_composer_foreground(icon_bundle)
            if ".xcassets" in icon_bundle.parts:
                warnings.append("Active Icon Composer bundle is inside an asset catalog; Xcode 26 projects should use a .icon project resource outside .xcassets.")
            return {
                "activeTarget": target.to_dict(),
                "activeAppIconName": app_icon_name,
                "activeAppIconSetPath": None,
                "activeIconComposerPath": str(icon_bundle),
                "activeMasterPng": master,
                "inactiveAssetCatalogs": inactive_appiconsets(project_root, icon_bundle),
                "confidence": confidence if master else 0.45,
                "warnings": warnings if master else [*warnings, "Active Icon Composer foreground image was not found."],
            }
        return {
            "activeTarget": target.to_dict(),
            "activeAppIconName": app_icon_name,
            "activeAppIconSetPath": None,
            "activeIconComposerPath": str(icon_bundle) if icon_bundle else None,
            "activeMasterPng": None,
            "confidence": 0.0,
            "warnings": [*warnings, "Active AppIcon.appiconset was not found."],
        }

    master = choose_largest_appicon_png(appiconset)
    if icon_bundle:
        warnings.append(f"Same-name Icon Composer bundle also exists: {icon_bundle}. Legacy AppIcon.appiconset remains the active source until it is removed and the .icon is added to the Xcode project.")
    inactive = inactive_appiconsets(project_root, appiconset)
    return {
        "activeTarget": target.to_dict(),
        "activeAppIconName": app_icon_name,
        "activeAppIconSetPath": str(appiconset),
        "activeIconComposerPath": str(icon_bundle) if icon_bundle else None,
        "activeMasterPng": master,
        "inactiveAssetCatalogs": inactive,
        "confidence": confidence,
        "warnings": warnings,
    }


def choose_active_xcode_target(targets: list[XcodeTargetInfo]) -> XcodeTargetInfo:
    for target in targets:
        if target.app_icon_name and target.bundle_identifier and not target.bundle_identifier.endswith(("Tests", "UITests")):
            return target
    for target in targets:
        if target.bundle_identifier and not target.bundle_identifier.endswith(("Tests", "UITests")):
            return target
    return targets[0]


def find_named_appiconset(root: Path, app_icon_name: str) -> Path | None:
    target_name = f"{app_icon_name}.appiconset"
    matches = [
        path
        for path in sorted(root.rglob(target_name))
        if path.is_dir() and not is_backup_path(path) and not is_build_artifact_path(path)
    ]
    return matches[0] if matches else None


def find_named_icon_composer_bundle(root: Path, app_icon_name: str) -> Path | None:
    target_name = f"{app_icon_name}.icon"
    matches = [
        path
        for path in sorted(root.rglob(target_name))
        if path.is_dir() and not is_backup_path(path) and not is_build_artifact_path(path)
    ]
    outside_asset_catalog = [path for path in matches if ".xcassets" not in path.parts]
    return (outside_asset_catalog or matches)[0] if matches else None


def choose_largest_appicon_png(appiconset: Path) -> dict[str, Any] | None:
    contents = appiconset / "Contents.json"
    candidates: list[dict[str, Any]] = []
    if contents.exists():
        try:
            payload = json.loads(contents.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        for entry in payload.get("images", []):
            filename = entry.get("filename")
            if not filename:
                continue
            image_path = appiconset / filename
            metadata = png_metadata(image_path)
            if metadata:
                candidates.append({**metadata, "contentsEntry": entry})

    if not candidates:
        for image_path in sorted(appiconset.glob("*.png")):
            metadata = png_metadata(image_path)
            if metadata:
                candidates.append(metadata)
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item["width"]) * int(item["height"]))


def choose_icon_composer_foreground(bundle_path: Path) -> dict[str, Any] | None:
    assets = bundle_path / "Assets"
    candidates = [
        path
        for path in sorted(assets.glob("*"))
        if path.suffix.lower() == ".png" and not is_ignored_scan_path(path)
    ]
    metadata = [png_metadata(path) for path in candidates]
    metadata = [item for item in metadata if item]
    if not metadata:
        return None
    master = max(metadata, key=lambda item: int(item["width"]) * int(item["height"]))
    return {**master, "kind": "icon-composer-foreground", "bundlePath": str(bundle_path)}


def inactive_appiconsets(root: Path, active: Path) -> list[str]:
    return [
        str(path)
        for path in sorted(root.rglob("*.appiconset"))
        if path != active and not is_backup_path(path) and not is_build_artifact_path(path)
    ]


def inspect_active_android_icon(root: Path) -> dict[str, Any]:
    manifest = find_android_manifest(root)
    warnings: list[str] = []
    if not manifest:
        return {
            "activeTarget": None,
            "activeAppIconName": None,
            "activeAppIconSetPath": None,
            "activeMasterPng": None,
            "confidence": 0.0,
            "warnings": ["AndroidManifest.xml was not found."],
        }
    icon_name = android_manifest_icon_name(manifest)
    if not icon_name:
        icon_name = "ic_launcher"
        warnings.append("android:icon was not found; using ic_launcher fallback.")
    master = choose_android_master(root, icon_name, warnings)
    return {
        "activeTarget": {
            "platform": "android",
            "manifest": str(manifest),
            "resDir": str(find_res_dir(root)),
        },
        "activeAppIconName": icon_name,
        "activeAppIconSetPath": None,
        "activeMasterPng": master,
        "confidence": 0.85 if master else 0.45,
        "warnings": warnings,
    }


def find_android_manifest(root: Path) -> Path | None:
    preferred = [
        root / "app" / "src" / "main" / "AndroidManifest.xml",
        root / "android" / "app" / "src" / "main" / "AndroidManifest.xml",
        root / "src" / "main" / "AndroidManifest.xml",
    ]
    for path in preferred:
        if path.exists() and not is_ignored_scan_path(path):
            return path
    matches = [
        path
        for path in sorted(root.rglob("AndroidManifest.xml"))
        if not is_ignored_scan_path(path)
    ]
    return matches[0] if matches else None


def android_manifest_icon_name(manifest: Path) -> str | None:
    try:
        root = ET.parse(manifest).getroot()
    except ET.ParseError:
        return None
    application = root.find("application")
    if application is None:
        return None
    raw = application.attrib.get(f"{ANDROID_NS}icon") or application.attrib.get(f"{ANDROID_NS}roundIcon")
    return android_resource_name(raw)


def android_resource_name(raw: str | None) -> str | None:
    if not raw or not raw.startswith("@"):
        return None
    return raw.rsplit("/", 1)[-1]


def choose_android_master(root: Path, icon_name: str, warnings: list[str]) -> dict[str, Any] | None:
    res_dir = find_res_dir(root)
    adaptive = res_dir / "mipmap-anydpi-v26" / f"{icon_name}.xml"
    if adaptive.exists():
        return {"path": str(adaptive), "kind": "android-adaptive-icon", "width": 1024, "height": 1024}
    candidates = [
        path
        for path in sorted(res_dir.rglob(f"{icon_name}.png"))
        if not is_ignored_scan_path(path)
    ]
    metadata = [png_metadata(path) for path in candidates]
    metadata = [item for item in metadata if item]
    if metadata:
        warnings.append("Android current master is reconstructed from the largest launcher PNG.")
        return max(metadata, key=lambda item: int(item["width"]) * int(item["height"]))
    return None


def extract_current_master(root: Path, out: Path, *, platform: str = "auto", allow_project_output: bool = False) -> dict[str, Any]:
    root = root.resolve()
    out = out.resolve()
    assert_safe_generated_output_path(out, allow_project_output=allow_project_output, label="extract-current-master output")
    report = inspect_active_icon(root, platform=platform)
    master = report.get("activeMasterPng")
    if not master:
        raise ValueError("active master PNG was not found")

    source_path = Path(str(master["path"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    warnings = list(report.get("warnings", []))
    if str(master.get("kind")) == "android-adaptive-icon":
        render_android_adaptive_icon(root, source_path, out)
        warnings.append("Extracted preview is a 1024x1024 render of the adaptive Android icon.")
    else:
        with Image.open(source_path) as image:
            image = image.convert("RGBA")
            if image.size != (1024, 1024):
                image = resized_png(image, 1024, flatten=False)
                warnings.append("Extracted current master was normalized to 1024x1024.")
            image.save(out, format="PNG", optimize=True)

    return {
        "ok": True,
        "root": str(root),
        "out": str(out),
        "sha256": sha256_file(out),
        "sourcePath": str(source_path),
        "activeIcon": report,
        "warnings": warnings,
    }


def render_android_adaptive_icon(root: Path, adaptive_xml: Path, out: Path) -> None:
    res_dir = find_res_dir(root)
    bg_color = "#FFFFFF"
    foreground_path: Path | None = None
    try:
        xml_root = ET.parse(adaptive_xml).getroot()
    except ET.ParseError:
        xml_root = None
    if xml_root is not None:
        for child in xml_root:
            drawable = child.attrib.get(f"{ANDROID_NS}drawable")
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "background" and drawable:
                bg_color = resolve_android_color(res_dir, drawable) or bg_color
            if tag == "foreground" and drawable:
                foreground_path = resolve_android_png(res_dir, drawable)
    rgb = tuple(int(bg_color.lstrip("#")[index:index + 2], 16) for index in (0, 2, 4))
    canvas = Image.new("RGBA", (1024, 1024), (*rgb, 255))
    if foreground_path and foreground_path.exists():
        with Image.open(foreground_path) as foreground:
            icon = resized_png(foreground.convert("RGBA"), 1024, flatten=False)
            canvas.alpha_composite(icon)
    canvas.save(out, format="PNG", optimize=True)


def resolve_android_png(res_dir: Path, drawable: str) -> Path | None:
    name = android_resource_name(drawable)
    if not name:
        return None
    matches = [
        path
        for path in sorted(res_dir.rglob(f"{name}.png"))
        if not is_ignored_scan_path(path)
    ]
    return matches[0] if matches else None


def resolve_android_color(res_dir: Path, drawable: str) -> str | None:
    name = android_resource_name(drawable)
    if not name:
        return None
    xml_matches = [
        path
        for path in sorted(res_dir.rglob(f"{name}.xml"))
        if not is_ignored_scan_path(path)
    ]
    for path in xml_matches:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for item in root.iter():
            color = item.attrib.get(f"{ANDROID_NS}color")
            if color and re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                return color.upper()
    return None


def source_candidate_report(root: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.png")):
        if is_backup_path(path) or is_build_artifact_path(path):
            continue
        if not is_generated_source_path(path):
            continue
        metadata = png_metadata(path)
        if metadata:
            candidates.append(
                {
                    **metadata,
                    "classification": "historical-source-reference",
                    "usableByDefault": False,
                    "requiresExplicitUserSelection": True,
                    "reason": "Found under IconSource. Treat as history/reference only unless the user explicitly selects it.",
                }
            )
        if len(candidates) >= limit:
            break
    return candidates


def build_source_options(active_payload: dict[str, Any], source_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    active_master = active_payload.get("activeMasterPng")
    if isinstance(active_master, dict) and active_master.get("path"):
        options.append(
            {
                "role": "active-current",
                "classification": "authoritative-active-source",
                "recommended": True,
                "path": active_master["path"],
                "width": active_master.get("width"),
                "height": active_master.get("height"),
                "sha256": active_master.get("sha256"),
                "reason": "Chosen from the active app icon target, build settings, manifest, or active appiconset. Use this for targeted edits of the icon currently visible in the IDE.",
            }
        )

    return options


def source_selection_payload(active_payload: dict[str, Any], source_options: list[dict[str, Any]]) -> dict[str, Any]:
    has_active = any(option.get("role") == "active-current" for option in source_options)
    if has_active:
        status = "active-current-authoritative"
        requires_choice = False
        guidance = "For current-icon edits, use the active-current option. Old IconSource files are history/reference only and are not default choices."
    else:
        status = "no-source"
        requires_choice = True
        guidance = "No active icon source was found. Ask what icon the user wants or ask them to explicitly select a reference; do not choose IconSource/history files by default."
    return {
        "status": status,
        "requiresUserChoice": requires_choice,
        "guidance": guidance,
    }


def build_artifact_report(root: Path, *, limit: int = 50) -> list[str]:
    artifacts: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not is_build_artifact_path(path):
            continue
        if path.suffix.lower() in {".png", ".icns", ".icon"} or ".app" in path.parts:
            artifacts.append(str(path))
        if len(artifacts) >= limit:
            break
    return artifacts


def png_metadata(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.suffix.lower() != ".png":
        return None
    try:
        with Image.open(path) as image:
            return {
                "path": str(path),
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "sha256": sha256_file(path),
            }
    except OSError:
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
