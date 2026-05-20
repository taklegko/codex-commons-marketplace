from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import plistlib
import re
import shutil
import subprocess
from typing import Any

from .path_filters import is_backup_path, is_ignored_scan_path
from .runtime_icons import discover_runtime_icons


BUILD_SETTING_KEYS = {
    "ASSETCATALOG_COMPILER_APPICON_NAME",
    "ASSETCATALOG_COMPILER_STANDALONE_ICON_BEHAVIOR",
    "GENERATE_INFOPLIST_FILE",
    "INFOPLIST_FILE",
    "INFOPLIST_KEY_CFBundleIconFile",
    "IPHONEOS_DEPLOYMENT_TARGET",
    "MACOSX_DEPLOYMENT_TARGET",
    "PRODUCT_BUNDLE_IDENTIFIER",
    "PRODUCT_NAME",
    "SDKROOT",
    "SUPPORTED_PLATFORMS",
    "TARGETED_DEVICE_FAMILY",
}


@dataclass(frozen=True)
class XcodeTargetInfo:
    project_path: str
    project_root: str
    target_name: str | None
    platform: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    bundle_identifier: str | None = None
    app_icon_name: str | None = None
    app_icon_set_path: str | None = None
    sdk_root: str | None = None
    supported_platforms: list[str] = field(default_factory=list)
    info_plist_file: str | None = None
    standalone_icon_behavior: str | None = None
    build_settings: dict[str, str] = field(default_factory=dict)
    runtime_icon_overrides: list[dict[str, Any]] = field(default_factory=list)
    additional_icon_resources: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_xcode_project(root: Path, *, run_xcodebuild: bool = True) -> list[XcodeTargetInfo]:
    root = root.resolve()
    projects = [
        path
        for path in sorted(root.rglob("*.xcodeproj"))
        if not is_backup_path(path)
    ]
    return [
        inspect_single_project(project, workspace_root=root, run_xcodebuild=run_xcodebuild)
        for project in projects
    ]


def inspect_single_project(project_path: Path, *, workspace_root: Path, run_xcodebuild: bool) -> XcodeTargetInfo:
    project_path = project_path.resolve()
    project_root = project_path.parent
    pbxproj = project_path / "project.pbxproj"
    warnings: list[str] = []
    settings = parse_pbxproj_build_settings(pbxproj) if pbxproj.exists() else {}

    if run_xcodebuild and shutil.which("xcodebuild"):
        xcodebuild_settings, xcodebuild_warning = read_xcodebuild_settings(project_path)
        if xcodebuild_warning:
            warnings.append(xcodebuild_warning)
        if xcodebuild_settings:
            settings = {**settings, **xcodebuild_settings}

    platform, platform_evidence, confidence = infer_platform_from_settings(settings, project_path)
    app_icon_name = settings.get("ASSETCATALOG_COMPILER_APPICON_NAME") or settings.get("INFOPLIST_KEY_CFBundleIconFile")
    if app_icon_name and app_icon_name.endswith(".icns"):
        app_icon_name = Path(app_icon_name).stem

    app_icon_set_path = find_app_icon_set(project_root, app_icon_name)
    if app_icon_set_path:
        platform_evidence.append(f"app icon set: {app_icon_set_path}")
        idioms = appiconset_idioms(app_icon_set_path)
        if "mac" in idioms and platform != "macos":
            warnings.append("Build settings and AppIcon.appiconset idiom conflict; using macOS because the active app icon set has idiom=mac.")
            platform = "macos"
            confidence = min(confidence, 0.85)
            platform_evidence.append(f"AppIcon.appiconset idiom=mac")
        elif "mac" in idioms and confidence < 0.9:
            platform = "macos"
            confidence = 0.85
            platform_evidence.append(f"AppIcon.appiconset idiom=mac")
        elif {"iphone", "ipad", "ios-marketing"} & idioms and platform == "macos":
            warnings.append("Build settings and AppIcon.appiconset idiom conflict; keeping macOS because build settings are authoritative.")
            platform_evidence.append(f"AppIcon.appiconset idiom={','.join(sorted(idioms))}")
        elif {"iphone", "ipad", "ios-marketing"} & idioms and confidence < 0.9:
            platform = "ios"
            confidence = 0.85
            platform_evidence.append(f"AppIcon.appiconset idiom={','.join(sorted(idioms))}")

    info_plist_file = settings.get("INFOPLIST_FILE")
    bundle_identifier = settings.get("PRODUCT_BUNDLE_IDENTIFIER")
    if bundle_identifier:
        platform_evidence.append(f"PRODUCT_BUNDLE_IDENTIFIER={bundle_identifier}")
    if info_plist_file:
        platform_evidence.append(f"INFOPLIST_FILE={info_plist_file}")
    runtime_report = discover_runtime_icons(project_root)

    return XcodeTargetInfo(
        project_path=str(project_path),
        project_root=str(project_root),
        target_name=settings.get("PRODUCT_NAME") or project_path.stem,
        platform=platform,
        confidence=confidence,
        evidence=sorted(set(platform_evidence)),
        bundle_identifier=bundle_identifier,
        app_icon_name=app_icon_name,
        app_icon_set_path=str(app_icon_set_path) if app_icon_set_path else None,
        sdk_root=settings.get("SDKROOT"),
        supported_platforms=parse_supported_platforms(settings.get("SUPPORTED_PLATFORMS")),
        info_plist_file=info_plist_file,
        standalone_icon_behavior=settings.get("ASSETCATALOG_COMPILER_STANDALONE_ICON_BEHAVIOR"),
        build_settings=settings,
        runtime_icon_overrides=[item.to_dict() for item in runtime_report.runtime_icon_overrides],
        additional_icon_resources=[item.to_dict() for item in runtime_report.additional_icon_resources],
        warnings=warnings,
    )


def parse_pbxproj_build_settings(pbxproj: Path) -> dict[str, str]:
    text = pbxproj.read_text(encoding="utf-8", errors="replace")
    settings: dict[str, str] = {}
    for key in BUILD_SETTING_KEYS:
        matches = re.findall(rf"\b{re.escape(key)}\s*=\s*(.+?);", text)
        if matches:
            values = [normalize_pbxproj_value(match) for match in matches]
            settings[key] = choose_pbxproj_setting_value(key, values)
    return settings


def choose_pbxproj_setting_value(key: str, values: list[str]) -> str:
    if not values:
        return ""
    lowered = [value.lower() for value in values]
    if key == "SDKROOT":
        for value, lower in zip(values, lowered):
            if "macosx" in lower:
                return value
        for value, lower in zip(values, lowered):
            if "iphoneos" in lower or "iphonesimulator" in lower:
                return value
    if key == "SUPPORTED_PLATFORMS":
        for value, lower in zip(values, lowered):
            if "macosx" in lower:
                return value
        for value, lower in zip(values, lowered):
            if "iphoneos" in lower or "iphonesimulator" in lower:
                return value
    return values[0]


def normalize_pbxproj_value(value: str) -> str:
    value = value.strip().rstrip(";").strip()
    if value.startswith("(") and value.endswith(")"):
        items = [item.strip().strip('"') for item in value.strip("()").split(",")]
        return " ".join(item for item in items if item)
    return value.strip('"')


def read_xcodebuild_settings(project_path: Path) -> tuple[dict[str, str], str | None]:
    command = ["xcodebuild", "-project", str(project_path), "-showBuildSettings", "-json"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {}, f"xcodebuild settings unavailable: {exc}"

    if result.returncode != 0:
        message = result.stderr.strip().splitlines()
        return {}, "xcodebuild settings unavailable: " + (message[-1] if message else f"exit {result.returncode}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {}, f"xcodebuild settings JSON invalid: {exc}"

    if not isinstance(payload, list) or not payload:
        return {}, "xcodebuild settings returned no targets"

    app_payload = choose_app_target_payload(payload)
    raw_settings = app_payload.get("buildSettings", {})
    return {
        key: str(raw_settings[key])
        for key in BUILD_SETTING_KEYS
        if key in raw_settings and raw_settings[key] is not None
    }, None


def choose_app_target_payload(payload: list[dict[str, Any]]) -> dict[str, Any]:
    for item in payload:
        settings = item.get("buildSettings", {})
        bundle_id = str(settings.get("PRODUCT_BUNDLE_IDENTIFIER", ""))
        if bundle_id and not bundle_id.endswith("Tests") and not bundle_id.endswith("UITests"):
            return item
    return payload[0]


def infer_platform_from_settings(settings: dict[str, str], project_path: Path) -> tuple[str, list[str], float]:
    evidence: list[str] = []
    sdk_root = settings.get("SDKROOT", "").lower()
    supported = " ".join(parse_supported_platforms(settings.get("SUPPORTED_PLATFORMS"))).lower()
    targeted_family = settings.get("TARGETED_DEVICE_FAMILY", "")
    macos_deployment = settings.get("MACOSX_DEPLOYMENT_TARGET", "")
    iphone_deployment = settings.get("IPHONEOS_DEPLOYMENT_TARGET", "")

    if sdk_root:
        evidence.append(f"SDKROOT={settings['SDKROOT']}")
    if supported:
        evidence.append(f"SUPPORTED_PLATFORMS={settings.get('SUPPORTED_PLATFORMS')}")
    if targeted_family:
        evidence.append(f"TARGETED_DEVICE_FAMILY={targeted_family}")
    if macos_deployment:
        evidence.append(f"MACOSX_DEPLOYMENT_TARGET={macos_deployment}")
    if iphone_deployment:
        evidence.append(f"IPHONEOS_DEPLOYMENT_TARGET={iphone_deployment}")

    if "macosx" in sdk_root or "macosx" in supported or macos_deployment:
        return "macos", evidence, 0.95
    if any(token in sdk_root or token in supported for token in ("iphoneos", "iphonesimulator")):
        return "ios", evidence, 0.95

    lowered_path = str(project_path).lower()
    if "macos" in lowered_path or "macapp" in lowered_path:
        evidence.append(f"path implies macOS: {project_path}")
        return "macos", evidence, 0.55
    evidence.append(f"path/default implies iOS: {project_path}")
    return "ios", evidence, 0.35


def parse_supported_platforms(value: str | None) -> list[str]:
    if not value:
        return []
    cleaned = value.replace("(", " ").replace(")", " ").replace(",", " ").replace('"', " ")
    return [part for part in cleaned.split() if part]


def find_app_icon_set(project_root: Path, app_icon_name: str | None) -> Path | None:
    names = [app_icon_name] if app_icon_name else []
    names.append("AppIcon")
    for name in names:
        if not name:
            continue
        matches = [
            path
            for path in sorted(project_root.rglob(f"{name}.appiconset"))
            if not is_backup_path(path)
        ]
        if matches:
            return matches[0]
    return None


def appiconset_idioms(appiconset: Path) -> set[str]:
    contents = appiconset / "Contents.json"
    if not contents.exists():
        return set()
    try:
        payload = json.loads(contents.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return {
        str(item.get("idiom"))
        for item in payload.get("images", [])
        if item.get("idiom")
    }


def find_runtime_icon_code(root: Path) -> list[str]:
    matches: list[str] = []
    for path in root.rglob("*.swift"):
        if is_ignored_scan_path(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "NSApplication.shared.applicationIconImage" in text:
            matches.append(str(path))
    return sorted(matches)


def read_info_plist(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as file:
            return plistlib.load(file)
    except (OSError, plistlib.InvalidFileException):
        return {}
