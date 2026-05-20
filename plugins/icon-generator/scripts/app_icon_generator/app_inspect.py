from __future__ import annotations

from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
from typing import Any

from PIL import Image

from .icon_composer import icon_composer_preflight
from .runtime_icons import discover_runtime_icons
from .xcode_inspect import find_runtime_icon_code, inspect_xcode_project


def inspect_built_app(app: Path) -> dict[str, Any]:
    app = app.resolve()
    resources = app / "Contents" / "Resources"
    info_plist = app / "Contents" / "Info.plist"
    bundle_id = read_bundle_identifier(info_plist)
    resource_files = [str(path) for path in sorted(resources.glob("*")) if path.is_file()] if resources.exists() else []
    icns_files = sorted(resources.glob("*.icns")) if resources.exists() else []
    icon_files = sorted(resources.glob("*.icon")) if resources.exists() else []

    return {
        "app": str(app),
        "ok": app.exists() and resources.exists(),
        "bundleIdentifier": bundle_id,
        "resourcesDir": str(resources),
        "resourceFiles": resource_files,
        "icns": [inspect_icns(path) for path in icns_files],
        "iconBundles": [str(path) for path in icon_files],
    }


def inspect_icns(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": str(path),
        "fileSize": path.stat().st_size if path.exists() else None,
        "ok": False,
        "representations": [],
        "errors": [],
    }
    if not path.exists():
        payload["errors"].append("missing icns file")
        return payload

    if not shutil.which("iconutil"):
        payload["errors"].append("iconutil not available")
        return payload

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "icon.iconset"
        result = subprocess.run(["iconutil", "-c", "iconset", str(path), "-o", str(out_dir)], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            payload["errors"].append(result.stderr.strip() or f"iconutil exited {result.returncode}")
            return payload

        reps = []
        for png in sorted(out_dir.glob("*.png")):
            try:
                with Image.open(png) as image:
                    reps.append(
                        {
                            "filename": png.name,
                            "pixelWidth": image.width,
                            "pixelHeight": image.height,
                            "mode": image.mode,
                            "hasAlpha": "A" in image.getbands(),
                        }
                    )
            except OSError as exc:
                payload["errors"].append(f"invalid representation {png.name}: {exc}")
        payload["representations"] = reps
        payload["ok"] = bool(reps)
        return payload


def find_running_apps(bundle_id: str, *, ps_output: str | None = None) -> dict[str, Any]:
    output = ps_output if ps_output is not None else read_ps_output()
    paths: list[str] = []
    for line in output.splitlines():
        if ".app/Contents/MacOS/" not in line:
            continue
        app_path = extract_app_path(line)
        if not app_path:
            continue
        info_plist = Path(app_path) / "Contents" / "Info.plist"
        if read_bundle_identifier(info_plist) == bundle_id:
            paths.append(app_path)

    unique_paths = sorted(set(paths))
    return {
        "bundleIdentifier": bundle_id,
        "runningAppPaths": unique_paths,
        "count": len(unique_paths),
        "hasDuplicates": len(unique_paths) > 1,
    }


def diagnose_macos_icon(root: Path, app: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    xcode = [target.to_dict() for target in inspect_xcode_project(root, run_xcodebuild=False)]
    runtime_icon_code = find_runtime_icon_code(root)
    runtime_report = discover_runtime_icons(root)
    built_app = inspect_built_app(app) if app else None
    bundle_id = built_app.get("bundleIdentifier") if built_app else first_bundle_id(xcode)

    running = find_running_apps(bundle_id) if bundle_id else None
    return {
        "root": str(root),
        "xcode": xcode,
        "runtimeIconCode": runtime_icon_code,
        "runtimeIconOverrides": [item.to_dict() for item in runtime_report.runtime_icon_overrides],
        "additionalIconResources": [item.to_dict() for item in runtime_report.additional_icon_resources],
        "builtApp": built_app,
        "runningApps": running,
        "iconComposerPreflight": icon_composer_preflight(),
        "diagnostic": "If a legacy PNG AppIcon.appiconset shows a light system frame on macOS 26, create an Icon Composer .icon bundle. PNG transparency or inset changes are not a reliable fix.",
    }


def read_bundle_identifier(info_plist: Path) -> str | None:
    try:
        with info_plist.open("rb") as file:
            payload = plistlib.load(file)
    except (OSError, plistlib.InvalidFileException):
        return None
    value = payload.get("CFBundleIdentifier")
    return str(value) if value else None


def read_ps_output() -> str:
    result = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=False)
    return result.stdout


def extract_app_path(line: str) -> str | None:
    marker = ".app/Contents/MacOS/"
    index = line.find(marker)
    if index == -1:
        return None
    prefix = line[: index + len(".app")]
    slash = prefix.find("/")
    if slash == -1:
        return None
    return prefix[slash:]


def first_bundle_id(xcode: list[dict[str, Any]]) -> str | None:
    for target in xcode:
        bundle_id = target.get("bundle_identifier") or target.get("bundleIdentifier")
        if bundle_id:
            return str(bundle_id)
    return None
