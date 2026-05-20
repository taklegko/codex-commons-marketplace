from __future__ import annotations

from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
from time import monotonic, sleep, strftime
from typing import Any

from .image_io import WriteResult
from .path_filters import is_backup_path, is_build_artifact_path, is_ignored_scan_path


def icon_composer_preflight(*, check_package: bool = True) -> dict[str, Any]:
    node = shutil.which("node")
    npx = shutil.which("npx")
    icon_composer_app = find_icon_composer_app()
    ictool = find_ictool()
    node_version = read_command_version([node, "--version"]) if node else None
    npx_version = read_command_version([npx, "--version"]) if npx else None
    xcode_version = read_command_version(["xcodebuild", "-version"]) if shutil.which("xcodebuild") else None
    package_check = check_icon_composer_package() if node and npx and check_package else {
        "checked": False,
        "ready": bool(node and npx),
        "error": None,
    }

    mcp_ready = bool(node and npx and package_check["ready"])
    ictool_ready = bool(icon_composer_app and ictool)
    selected_backend = "mcp" if mcp_ready and ictool_ready else "ictool" if ictool_ready else None
    can_use_icon_composer = ictool_ready
    ok = can_use_icon_composer
    liquid_glass_ready = ictool_ready
    install_message = None
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    if not ictool_ready:
        blocking_reasons.append("Icon Composer.app or ictool was not found.")
        install_message = (
            "Icon Composer is required for macOS 26 Liquid Glass .icon output. "
            "Install Xcode 26 with bundled Icon Composer, install standalone Icon Composer, or run `brew install --cask icon-composer`."
        )
    if not node or not npx:
        warnings.append("Node.js 18+ and npx are not available; icon-composer-mcp is unavailable, but local ictool fallback is enough when Icon Composer is installed.")
    elif not package_check["ready"]:
        warnings.append("icon-composer-mcp could not start; using local .icon writer with ictool fallback when possible.")

    return {
        "ok": ok,
        "canUseIconComposer": can_use_icon_composer,
        "installRequired": not can_use_icon_composer,
        "blockingReasons": blocking_reasons,
        "mcpReady": mcp_ready,
        "ictoolReady": ictool_ready,
        "selectedBackend": selected_backend,
        "liquidGlassReady": liquid_glass_ready,
        "node": node,
        "nodeVersion": node_version,
        "npx": npx,
        "npxVersion": npx_version,
        "packageCheck": package_check,
        "xcodeVersion": xcode_version,
        "iconComposerApp": str(icon_composer_app) if icon_composer_app else None,
        "ictool": str(ictool) if ictool else None,
        "installMessage": install_message,
        "installOptions": icon_composer_install_options(),
        "fallbackOptions": icon_composer_fallback_options(),
        "warnings": warnings,
    }


def icon_composer_install_guide() -> dict[str, Any]:
    return {
        "ok": True,
        "manualOnly": True,
        "summary": "Icon Composer is required for macOS 26 Liquid Glass .icon output. This guide does not install anything automatically.",
        "installOptions": icon_composer_install_options(),
        "steps": [
            "Install or confirm Xcode 26 with bundled Icon Composer at /Applications/Xcode.app/Contents/Applications/Icon Composer.app.",
            "Alternatively install standalone Icon Composer, for example with: brew install --cask icon-composer.",
            "After installation, run: python -m app_icon_generator.cli icon-composer-doctor --json.",
            "Wait until doctor reports canUseIconComposer=true, ictoolReady=true, and liquidGlassReady=true.",
            "Then repeat plan-apply with --mode icon-composer or --mode auto for a macOS 26 target.",
        ],
        "verificationCommand": "python -m app_icon_generator.cli wait-icon-composer-ready --timeout 300 --interval 5 --json",
        "fallbackOptions": icon_composer_fallback_options(),
    }


def wait_icon_composer_ready(*, timeout: float, interval: float) -> dict[str, Any]:
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    if interval < 0:
        raise ValueError("interval must be non-negative")

    started = monotonic()
    attempts = 0
    last_payload: dict[str, Any] | None = None
    while True:
        attempts += 1
        last_payload = icon_composer_preflight(check_package=False)
        ready = bool(last_payload.get("canUseIconComposer") and last_payload.get("liquidGlassReady"))
        elapsed = monotonic() - started
        if ready:
            return {
                "ok": True,
                "ready": True,
                "attempts": attempts,
                "elapsedSeconds": round(elapsed, 3),
                "preflight": last_payload,
            }
        if elapsed >= timeout:
            return {
                "ok": False,
                "ready": False,
                "timedOut": True,
                "attempts": attempts,
                "elapsedSeconds": round(elapsed, 3),
                "preflight": last_payload,
                "installGuide": icon_composer_install_guide(),
            }
        sleep(min(interval, max(timeout - elapsed, 0)))


def icon_composer_install_options() -> list[dict[str, str]]:
    return [
        {
            "id": "xcode-bundled",
            "label": "Use Xcode-bundled Icon Composer",
            "path": "/Applications/Xcode.app/Contents/Applications/Icon Composer.app",
            "note": "Install or update Xcode 26, then verify ictool is available.",
        },
        {
            "id": "standalone-app",
            "label": "Install standalone Icon Composer",
            "path": "/Applications/Icon Composer.app",
            "note": "Use Apple's standalone Icon Composer distribution when available.",
        },
        {
            "id": "homebrew-cask",
            "label": "Install with Homebrew",
            "command": "brew install --cask icon-composer",
            "note": "Run this manually in a terminal if you choose the Homebrew path.",
        },
    ]


def icon_composer_fallback_options() -> list[dict[str, str]]:
    return [
        {
            "id": "legacy-appiconset",
            "label": "Generate legacy AppIcon.appiconset PNG assets",
            "mode": "legacy-appiconset",
            "warning": "This creates flat PNG assets and will not produce macOS 26 Liquid Glass .icon output.",
        },
        {
            "id": "stop",
            "label": "Stop without applying icon assets",
            "mode": "none",
            "warning": "No project files will be changed.",
        },
    ]


def generate_icon_composer_bundle(
    root: Path,
    foreground: Path,
    *,
    bg_color: str,
    bundle_name: str,
    backup: bool,
    backup_root: Path | None = None,
    dark_bg_color: str | None = None,
) -> dict[str, Any]:
    preflight = icon_composer_preflight()
    if not preflight.get("canUseIconComposer", preflight.get("ok")):
        raise ValueError(preflight["installMessage"])

    root = root.resolve()
    bundle_path = find_icon_composer_bundle_path(root, bundle_name)
    output_dir = bundle_path.parent
    backup_files: list[str] = []
    removed_files: list[str] = []
    timestamp = strftime("%Y%m%d-%H%M%S")

    backup_path_tree(bundle_path, root=root, backup=backup, backup_root=backup_root, timestamp=timestamp, backup_files=backup_files)

    output_dir.mkdir(parents=True, exist_ok=True)
    backend = preflight["selectedBackend"]
    if backend == "mcp":
        command = [
            "npx",
            "-y",
            "-p",
            "icon-composer-mcp",
            "icon-composer",
            "create",
            str(foreground.resolve()),
            str(output_dir),
            "--bg-color",
            bg_color,
            "--bundle-name",
            bundle_name,
        ]
        if dark_bg_color:
            command.extend(["--dark-bg-color", dark_bg_color])

        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or f"icon-composer exited {result.returncode}"
            raise ValueError(message)
    else:
        create_icon_bundle_locally(
            bundle_path,
            foreground,
            bg_color=bg_color,
            dark_bg_color=dark_bg_color,
        )

    normalized_icon_json = normalize_icon_json_scale(bundle_path, scale=1)
    legacy_appiconset = find_legacy_appiconset(root, bundle_name)
    if legacy_appiconset:
        remove_tree_with_backup(
            legacy_appiconset,
            root=root,
            backup=backup,
            backup_root=backup_root,
            timestamp=timestamp,
            backup_files=backup_files,
            removed_files=removed_files,
        )
    project_update = ensure_icon_bundle_in_xcode_project(
        root,
        bundle_path,
        backup=backup,
        backup_root=backup_root,
        timestamp=timestamp,
        backup_files=backup_files,
    )

    changed_files = [str(path) for path in sorted(bundle_path.rglob("*")) if path.is_file()]
    if project_update.get("changed"):
        changed_files.append(str(project_update["projectFile"]))
    write_result = WriteResult(changed_files=changed_files, backup_files=backup_files, removed_files=removed_files)
    return {
        "ok": True,
        "mode": "icon-composer",
        "bundlePath": str(bundle_path),
        "backend": backend,
        "outputDir": str(output_dir),
        "preflight": preflight,
        "legacyAppIconSetRemoved": str(legacy_appiconset) if legacy_appiconset else None,
        "projectUpdate": project_update,
        "normalizedIconJson": normalized_icon_json,
        "changedFiles": write_result.changed_files,
        "backupFiles": write_result.backup_files,
        "removedFiles": write_result.removed_files,
        "diagnostic": "For macOS 26 Liquid Glass, use the .icon bundle; PNG appiconset resizing cannot remove the system legacy frame.",
    }


def find_icon_composer_bundle_path(root: Path, bundle_name: str = "AppIcon") -> Path:
    root = root.resolve()
    existing = [
        path
        for path in sorted(root.rglob(f"{bundle_name}.icon"))
        if path.is_dir() and not is_ignored_scan_path(path) and ".xcassets" not in path.parts
    ]
    if existing:
        return existing[0]

    legacy = find_legacy_appiconset(root, bundle_name)
    if legacy and legacy.parent.suffix == ".xcassets":
        return legacy.parent.parent / f"{bundle_name}.icon"

    for candidate in (
        root / "App" / "Resources",
        root / "Resources",
        root / "App",
    ):
        if candidate.exists():
            return candidate / f"{bundle_name}.icon"
    return root / f"{bundle_name}.icon"


def find_legacy_appiconset(root: Path, bundle_name: str = "AppIcon") -> Path | None:
    root = root.resolve()
    matches = [
        path
        for path in sorted(root.rglob(f"{bundle_name}.appiconset"))
        if path.is_dir() and not is_backup_path(path) and not is_build_artifact_path(path)
    ]
    return matches[0] if matches else None


def backup_path_tree(
    path: Path,
    *,
    root: Path,
    backup: bool,
    backup_root: Path | None,
    timestamp: str,
    backup_files: list[str],
) -> Path | None:
    if not backup or not path.exists():
        return None
    destination_root = backup_root or (root / ".icon-generator-backups")
    backup_path = destination_root / timestamp / path.resolve().relative_to(root.resolve())
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        shutil.copytree(path, backup_path, dirs_exist_ok=True)
        backup_files.extend(str(item) for item in sorted(backup_path.rglob("*")) if item.is_file())
    else:
        shutil.copy2(path, backup_path)
        backup_files.append(str(backup_path))
    return backup_path


def remove_tree_with_backup(
    path: Path,
    *,
    root: Path,
    backup: bool,
    backup_root: Path | None,
    timestamp: str,
    backup_files: list[str],
    removed_files: list[str],
) -> None:
    if not path.exists():
        return
    backup_path_tree(path, root=root, backup=backup, backup_root=backup_root, timestamp=timestamp, backup_files=backup_files)
    if path.is_dir():
        removed_files.extend(str(item) for item in sorted(path.rglob("*")) if item.is_file())
        shutil.rmtree(path)
    else:
        path.unlink()
        removed_files.append(str(path))


def normalize_icon_json_scale(bundle_path: Path, *, scale: float = 1) -> dict[str, Any]:
    icon_json = bundle_path / "icon.json"
    if not icon_json.exists():
        return {"checked": True, "changed": False, "path": str(icon_json), "warning": "icon.json is missing"}
    try:
        payload = json.loads(icon_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"checked": True, "changed": False, "path": str(icon_json), "warning": f"invalid icon.json: {exc}"}

    changed = False
    layer_count = 0
    for group in payload.get("groups", []):
        for layer in group.get("layers", []):
            position = layer.setdefault("position", {})
            layer_count += 1
            if position.get("scale") != scale:
                position["scale"] = scale
                changed = True
            if "translation-in-points" not in position:
                position["translation-in-points"] = [0, 0]
                changed = True

    if changed:
        icon_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"checked": True, "changed": changed, "path": str(icon_json), "scale": scale, "layerCount": layer_count}


def ensure_icon_bundle_in_xcode_project(
    root: Path,
    bundle_path: Path,
    *,
    backup: bool,
    backup_root: Path | None,
    timestamp: str,
    backup_files: list[str],
) -> dict[str, Any]:
    pbxproj = find_project_pbxproj(root)
    if not pbxproj:
        return {"changed": False, "projectFile": None, "warnings": ["No Xcode project.pbxproj was found."]}

    rel_path = bundle_path.resolve().relative_to(root.resolve()).as_posix()
    file_name = bundle_path.name
    text = pbxproj.read_text(encoding="utf-8", errors="replace")
    original = text
    warnings: list[str] = []
    canonical_path = canonical_icon_reference_path(root, bundle_path, text)
    file_refs_before = list_icon_file_references(text, file_name)
    canonical_ref = choose_canonical_icon_file_ref(file_refs_before, canonical_path)
    file_ref_id = canonical_ref["id"] if canonical_ref else stable_pbx_id(f"PBXFileReference:{rel_path}")
    duplicate_ref_ids = [item["id"] for item in file_refs_before if item["id"] != file_ref_id]

    text, inserted_ref = upsert_icon_file_reference(text, file_ref_id, file_name, canonical_path)
    if not inserted_ref:
        warnings.append("PBXFileReference section was not found; AppIcon.icon could not be added to the project navigator.")

    for duplicate_id in duplicate_ref_ids:
        text = remove_pbx_entry_by_id(text, duplicate_id)
    text = remove_group_child_references(text, duplicate_ref_ids)

    build_files_before = list_icon_build_files(text, file_name)
    build_file_id = build_files_before[0]["id"] if build_files_before else stable_pbx_id(f"PBXBuildFile:{rel_path}")
    duplicate_build_file_ids = [item["id"] for item in build_files_before if item["id"] != build_file_id]
    text = remove_pbx_entries_by_ids(text, duplicate_build_file_ids)
    text = remove_resource_build_file_references(text, duplicate_build_file_ids)

    text, inserted_build = upsert_icon_build_file(text, build_file_id, file_ref_id, file_name)
    if not inserted_build:
        warnings.append("PBXBuildFile section was not found; AppIcon.icon could not be added to resources.")

    text = rewrite_icon_build_file_refs(text, file_name, file_ref_id)
    if build_file_id not in resources_build_phase_files(text):
        text, inserted = insert_resource_build_file(text, build_file_id, file_name)
        if not inserted:
            warnings.append("PBXResourcesBuildPhase files list was not found; AppIcon.icon may not be copied into the built app.")

    text, group_inserted = insert_resources_group_child(text, file_ref_id, file_name)
    if not group_inserted:
        warnings.append("Resources PBXGroup was not found; AppIcon.icon was added to the build phase but may not appear in the Resources group.")

    if text == original:
        return {
            "changed": False,
            "projectFile": str(pbxproj),
            "iconBundleReference": rel_path,
            "canonicalIconReferencePath": canonical_path,
            "resourceBuildFileId": build_file_id,
            "fileReferenceId": file_ref_id,
            "removedDuplicateFileReferenceIds": duplicate_ref_ids,
            "removedDuplicateBuildFileIds": duplicate_build_file_ids,
            "warnings": warnings,
        }

    backup_path_tree(pbxproj, root=root, backup=backup, backup_root=backup_root, timestamp=timestamp, backup_files=backup_files)
    pbxproj.write_text(text, encoding="utf-8")
    return {
        "changed": True,
        "projectFile": str(pbxproj),
        "iconBundleReference": rel_path,
        "canonicalIconReferencePath": canonical_path,
        "resourceBuildFileId": build_file_id,
        "fileReferenceId": file_ref_id,
        "removedDuplicateFileReferenceIds": duplicate_ref_ids,
        "removedDuplicateBuildFileIds": duplicate_build_file_ids,
        "warnings": warnings,
    }


def find_project_pbxproj(root: Path) -> Path | None:
    matches = [
        path
        for path in sorted(root.rglob("*.xcodeproj/project.pbxproj"))
        if not is_ignored_scan_path(path)
    ]
    return matches[0] if matches else None


def stable_pbx_id(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest().upper()[:24]


def canonical_icon_reference_path(root: Path, bundle_path: Path, text: str) -> str:
    relative = bundle_path.resolve().relative_to(root.resolve()).as_posix()
    if resources_group_id(text) and bundle_path.parent.name == "Resources":
        return bundle_path.name
    return relative


def resources_group_id(text: str) -> str | None:
    match = re.search(r"([A-F0-9]{24}) /\* Resources \*/ = \{.*?isa = PBXGroup;.*?children = \(", text, flags=re.DOTALL)
    return match.group(1) if match else None


def list_icon_file_references(text: str, file_name: str) -> list[dict[str, str | None]]:
    escaped_name = re.escape(file_name)
    refs: list[dict[str, str | None]] = []
    pattern = rf"^\s*([A-F0-9]{{24}}) /\* {escaped_name} \*/ = \{{isa = PBXFileReference; (?P<body>.*?)\}};\n?"
    for match in re.finditer(pattern, text, flags=re.MULTILINE):
        body = match.group("body")
        refs.append(
            {
                "id": match.group(1),
                "path": pbx_field_value(body, "path"),
                "lastKnownFileType": pbx_field_value(body, "lastKnownFileType"),
                "entry": match.group(0),
            }
        )
    return refs


def list_icon_build_files(text: str, file_name: str) -> list[dict[str, str | None]]:
    escaped_name = re.escape(file_name)
    files: list[dict[str, str | None]] = []
    pattern = rf"^\s*([A-F0-9]{{24}}) /\* {escaped_name} in Resources \*/ = \{{isa = PBXBuildFile; (?P<body>.*?)\}};\n?"
    for match in re.finditer(pattern, text, flags=re.MULTILINE):
        file_ref_match = re.search(r"fileRef = ([A-F0-9]{24}) /\*", match.group("body"))
        files.append({"id": match.group(1), "fileRef": file_ref_match.group(1) if file_ref_match else None, "entry": match.group(0)})
    return files


def pbx_field_value(body: str, field: str) -> str | None:
    match = re.search(rf"\b{re.escape(field)} = (\"[^\"]+\"|[^;]+);", body)
    if not match:
        return None
    return match.group(1).strip().strip('"')


def choose_canonical_icon_file_ref(refs: list[dict[str, str | None]], canonical_path: str) -> dict[str, str | None] | None:
    for ref in refs:
        if ref.get("path") == canonical_path and ref.get("lastKnownFileType") == "folder.iconcomposer.icon":
            return ref
    return refs[0] if refs else None


def upsert_icon_file_reference(text: str, file_ref_id: str, file_name: str, canonical_path: str) -> tuple[str, bool]:
    entry = f'\t\t{file_ref_id} /* {file_name} */ = {{isa = PBXFileReference; lastKnownFileType = folder.iconcomposer.icon; path = {quote_pbx_path(canonical_path)}; sourceTree = "<group>"; }};\n'
    if re.search(rf"^\s*{re.escape(file_ref_id)} /\* {re.escape(file_name)} \*/ = \{{isa = PBXFileReference; .*?\}};\n?", text, flags=re.MULTILINE):
        return re.sub(rf"^\s*{re.escape(file_ref_id)} /\* {re.escape(file_name)} \*/ = \{{isa = PBXFileReference; .*?\}};\n?", entry, text, count=1, flags=re.MULTILINE), True
    return insert_pbx_entry(text, "PBXFileReference", entry)


def upsert_icon_build_file(text: str, build_file_id: str, file_ref_id: str, file_name: str) -> tuple[str, bool]:
    entry = f"\t\t{build_file_id} /* {file_name} in Resources */ = {{isa = PBXBuildFile; fileRef = {file_ref_id} /* {file_name} */; }};\n"
    if re.search(rf"^\s*{re.escape(build_file_id)} /\* {re.escape(file_name)} in Resources \*/ = \{{isa = PBXBuildFile; .*?\}};\n?", text, flags=re.MULTILINE):
        return re.sub(rf"^\s*{re.escape(build_file_id)} /\* {re.escape(file_name)} in Resources \*/ = \{{isa = PBXBuildFile; .*?\}};\n?", entry, text, count=1, flags=re.MULTILINE), True
    return insert_pbx_entry(text, "PBXBuildFile", entry)


def quote_pbx_path(path: str) -> str:
    return path if re.fullmatch(r"[A-Za-z0-9_.-]+", path) else json.dumps(path)


def remove_pbx_entries_by_ids(text: str, ids: list[str]) -> str:
    for entry_id in ids:
        text = remove_pbx_entry_by_id(text, entry_id)
    return text


def remove_pbx_entry_by_id(text: str, entry_id: str) -> str:
    return re.sub(rf"^\s*{re.escape(entry_id)} /\* .*?\*/ = \{{.*?\}};\n?", "", text, flags=re.MULTILINE)


def remove_group_child_references(text: str, ids: list[str]) -> str:
    for entry_id in ids:
        text = re.sub(rf"^\s*{re.escape(entry_id)} /\* .*?\*/,\n?", "", text, flags=re.MULTILINE)
    return text


def remove_resource_build_file_references(text: str, ids: list[str]) -> str:
    return remove_group_child_references(text, ids)


def rewrite_icon_build_file_refs(text: str, file_name: str, file_ref_id: str) -> str:
    escaped_name = re.escape(file_name)
    pattern = rf"(^\s*[A-F0-9]{{24}} /\* {escaped_name} in Resources \*/ = \{{isa = PBXBuildFile; fileRef = )([A-F0-9]{{24}})( /\* {escaped_name} \*/; \}};)"
    return re.sub(pattern, rf"\g<1>{file_ref_id}\g<3>", text, flags=re.MULTILINE)


def insert_pbx_entry(text: str, section: str, entry: str) -> tuple[str, bool]:
    marker = f"/* Begin {section} section */\n"
    if marker not in text:
        return text, False
    return text.replace(marker, marker + entry, 1), True


def resources_build_phase_files(text: str) -> str:
    match = re.search(r"isa = PBXResourcesBuildPhase;.*?files = \((.*?)\);", text, flags=re.DOTALL)
    return match.group(1) if match else ""


def insert_resource_build_file(text: str, build_file_id: str, file_name: str) -> tuple[str, bool]:
    pattern = r"(isa = PBXResourcesBuildPhase;.*?files = \(\n)"
    replacement = rf"\1\t\t\t\t{build_file_id} /* {file_name} in Resources */,\n"
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    return new_text, bool(count)


def insert_resources_group_child(text: str, file_ref_id: str, file_name: str) -> tuple[str, bool]:
    if f"{file_ref_id} /* {file_name} */," in text:
        return text, True
    group_pattern = r"([A-F0-9]{24} /\* Resources \*/ = \{.*?children = \(\n)(.*?\n\t\t\t\);.*?\n\t\t\};)"
    match = re.search(group_pattern, text, flags=re.DOTALL)
    if not match:
        return text, False
    insert = f"\t\t\t\t{file_ref_id} /* {file_name} */,\n"
    start, rest = match.groups()
    return text[: match.start()] + start + insert + rest + text[match.end() :], True


def inspect_icon_composer_project_state(root: Path, bundle_name: str = "AppIcon") -> dict[str, Any]:
    root = root.resolve()
    bundle_path = find_icon_composer_bundle_path(root, bundle_name)
    legacy = find_legacy_appiconset(root, bundle_name)
    foreground = icon_composer_foreground_path(bundle_path)
    icon_json = bundle_path / "icon.json"
    layer_scales: list[Any] = []
    icon_json_error = None
    if icon_json.exists():
        try:
            payload = json.loads(icon_json.read_text(encoding="utf-8"))
            for group in payload.get("groups", []):
                for layer in group.get("layers", []):
                    layer_scales.append(layer.get("position", {}).get("scale"))
        except json.JSONDecodeError as exc:
            icon_json_error = str(exc)
    pbxproj = find_project_pbxproj(root)
    rel_path = bundle_path.resolve().relative_to(root).as_posix() if bundle_path.exists() else str(bundle_path)
    pbx_text = pbxproj.read_text(encoding="utf-8", errors="replace") if pbxproj and pbxproj.exists() else ""
    canonical_path = canonical_icon_reference_path(root, bundle_path, pbx_text) if pbx_text else bundle_path.name
    icon_file_refs = list_icon_file_references(pbx_text, bundle_path.name) if pbx_text else []
    wrong_refs = [
        ref
        for ref in icon_file_refs
        if ref.get("path") != canonical_path or ref.get("lastKnownFileType") != "folder.iconcomposer.icon"
    ]
    canonical_refs = [
        ref
        for ref in icon_file_refs
        if ref.get("path") == canonical_path and ref.get("lastKnownFileType") == "folder.iconcomposer.icon"
    ]
    primary_ref = canonical_refs[0] if canonical_refs else icon_file_refs[0] if icon_file_refs else None
    duplicate_refs = [
        ref
        for ref in icon_file_refs
        if primary_ref and ref.get("id") != primary_ref.get("id")
    ]
    navigator_references_clean = len(icon_file_refs) == 1 and len(canonical_refs) == 1
    referenced = bool(canonical_refs)
    in_resources = f"{bundle_path.name} in Resources" in pbx_text
    inside_xcassets = ".xcassets" in bundle_path.parts

    errors: list[str] = []
    warnings: list[str] = []
    if not bundle_path.exists():
        errors.append(f"Icon Composer bundle is missing: {bundle_path}")
    if inside_xcassets:
        errors.append(f"Icon Composer bundle is inside an asset catalog; expected a project resource outside .xcassets: {bundle_path}")
    if not foreground or not foreground.exists():
        errors.append(f"Icon Composer foreground image is missing under {bundle_path / 'Assets'}")
    if not icon_json.exists():
        errors.append(f"icon.json is missing: {icon_json}")
    if icon_json_error:
        errors.append(f"icon.json is invalid: {icon_json_error}")
    if layer_scales and any(scale != 1 for scale in layer_scales):
        errors.append(f"Icon Composer layer scale must be 1 for full-composition foreground images; got {layer_scales}")
    if legacy:
        errors.append(f"Same-name legacy AppIcon.appiconset still exists and can win over .icon: {legacy}")
    if not pbxproj:
        errors.append("Xcode project.pbxproj was not found.")
    elif not referenced:
        errors.append(f"{bundle_path.name} does not have a canonical PBXFileReference in project.pbxproj.")
    elif not in_resources:
        errors.append(f"{bundle_path.name} is not in the PBXResourcesBuildPhase.")
    if duplicate_refs:
        errors.append(f"Duplicate PBXFileReference entries for {bundle_path.name}: {[ref['id'] for ref in duplicate_refs]}")
    if wrong_refs:
        errors.append(f"Non-canonical PBXFileReference entries for {bundle_path.name}: {wrong_refs}")
    if pbxproj and referenced and not in_resources:
        warnings.append("The .icon may appear in the project but not be copied into the built .app resources.")

    return {
        "ok": not errors,
        "bundlePath": str(bundle_path),
        "bundleInsideAssetCatalog": inside_xcassets,
        "foregroundPath": str(foreground) if foreground else None,
        "foregroundExists": bool(foreground and foreground.exists()),
        "iconJsonPath": str(icon_json),
        "iconJsonExists": icon_json.exists(),
        "layerScales": layer_scales,
        "legacyAppIconSetPath": str(legacy) if legacy else None,
        "legacyAppIconSetPresent": legacy is not None,
        "projectFile": str(pbxproj) if pbxproj else None,
        "projectReferencesIconBundle": referenced,
        "projectResourcesContainIconBundle": in_resources,
        "canonicalIconReferencePath": canonical_path,
        "iconComposerReferences": icon_file_refs,
        "duplicateIconComposerReferences": duplicate_refs,
        "wrongIconReferencePaths": wrong_refs,
        "navigatorReferencesClean": navigator_references_clean,
        "errors": errors,
        "warnings": warnings,
    }


def icon_composer_foreground_path(bundle_path: Path) -> Path | None:
    assets_dir = bundle_path / "Assets"
    for candidate in (assets_dir / "foreground.png", assets_dir / "foreground.svg"):
        if candidate.exists():
            return candidate
    matches = sorted(path for path in assets_dir.glob("*") if path.suffix.lower() in {".png", ".svg"})
    return matches[0] if matches else None


def find_icon_composer_app() -> Path | None:
    candidates = [
        Path("/Applications/Icon Composer.app"),
        Path("/Applications/Xcode.app/Contents/Applications/Icon Composer.app"),
        Path.home() / "Applications" / "Icon Composer.app",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def find_ictool() -> Path | None:
    candidates = [
        Path("/Applications/Xcode.app/Contents/Developer/usr/bin/ictool"),
        Path("/Applications/Xcode.app/Contents/Applications/Icon Composer.app/Contents/Executables/ictool"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    path = shutil.which("ictool")
    if path:
        return Path(path)
    return None


def create_icon_bundle_locally(
    bundle_path: Path,
    foreground: Path,
    *,
    bg_color: str,
    dark_bg_color: str | None = None,
) -> None:
    if foreground.suffix.lower() not in {".png", ".svg"}:
        raise ValueError("Icon Composer foreground must be PNG or SVG")

    assets_dir = bundle_path / "Assets"
    if bundle_path.exists():
        shutil.rmtree(bundle_path)
    assets_dir.mkdir(parents=True, exist_ok=True)

    foreground_name = f"foreground{foreground.suffix.lower()}"
    shutil.copy2(foreground, assets_dir / foreground_name)

    manifest: dict[str, Any] = {
        "groups": [
            {
                "name": "Foreground",
                "specular": True,
                "shadow": {
                    "kind": "layer-color",
                    "opacity": 0.5,
                },
                "blur-material": None,
                "layers": [
                    {
                        "image-name": foreground_name,
                        "name": "glyph",
                        "glass": True,
                        "position": {
                            "scale": 1.75,
                            "translation-in-points": [0, 0],
                        },
                    }
                ],
            }
        ],
        "supported-platforms": {
            "squares": "shared",
            "circles": ["watchOS"],
        },
    }
    if dark_bg_color:
        manifest["fill-specializations"] = [
            {"value": solid_fill(bg_color)},
            {"appearance": "dark", "value": solid_fill(dark_bg_color)},
        ]
    else:
        manifest["fill"] = solid_fill(bg_color)

    (bundle_path / "icon.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def solid_fill(hex_color: str) -> dict[str, str]:
    return {"solid": hex_to_icon_color(hex_color)}


def hex_to_icon_color(hex_color: str, color_space: str = "srgb") -> str:
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) == 3:
        cleaned = "".join(char * 2 for char in cleaned)
    if len(cleaned) != 6:
        raise ValueError(f"invalid hex color: {hex_color}")
    try:
        red = int(cleaned[0:2], 16) / 255
        green = int(cleaned[2:4], 16) / 255
        blue = int(cleaned[4:6], 16) / 255
    except ValueError as exc:
        raise ValueError(f"invalid hex color: {hex_color}") from exc
    return f"{color_space}:{red:.5f},{green:.5f},{blue:.5f},1.00000"


def check_icon_composer_package() -> dict[str, Any]:
    command = ["npx", "-y", "-p", "icon-composer-mcp", "icon-composer", "--help"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=35, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"checked": True, "ready": False, "error": str(exc)}

    if result.returncode == 0:
        return {"checked": True, "ready": True, "error": None}

    output = (result.stderr or result.stdout).strip()
    first_lines = "\n".join(output.splitlines()[:8])
    return {
        "checked": True,
        "ready": False,
        "error": first_lines or f"icon-composer exited {result.returncode}",
    }


def read_command_version(command: list[str | None]) -> str | None:
    if not command[0]:
        return None
    try:
        result = subprocess.run([str(part) for part in command], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip()
    return output or None
