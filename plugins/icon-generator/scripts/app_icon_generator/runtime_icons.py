from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from time import strftime
from typing import Any

from PIL import Image, ImageChops

from .image_io import WriteResult, backup_existing, load_master_icon, resized_png, write_png
from .path_filters import BACKUP_DIR_NAMES, COMMON_IGNORED_DIR_NAMES, is_ignored_scan_path


IGNORED_PARTS = {*COMMON_IGNORED_DIR_NAMES}


@dataclass(frozen=True)
class RuntimeIconOverride:
    source_file: str
    kind: str
    resource_names: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdditionalIconResource:
    path: str
    name: str
    source: str
    referenced_by: list[str] = field(default_factory=list)
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeIconReport:
    runtime_icon_overrides: list[RuntimeIconOverride] = field(default_factory=list)
    additional_icon_resources: list[AdditionalIconResource] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtimeIconOverrides": [item.to_dict() for item in self.runtime_icon_overrides],
            "additionalIconResources": [item.to_dict() for item in self.additional_icon_resources],
        }


def discover_runtime_icons(root: Path) -> RuntimeIconReport:
    root = root.resolve()
    overrides: list[RuntimeIconOverride] = []
    referenced_names: dict[str, list[str]] = {}

    for swift in sorted(root.rglob("*.swift")):
        if is_ignored(swift):
            continue
        try:
            text = swift.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        constants = find_string_constants(text)
        evidence: list[str] = []
        names: list[str] = []
        if "NSApplication.shared.applicationIconImage" in text:
            evidence.append("NSApplication.shared.applicationIconImage")

        for expression, extension in find_bundle_resource_calls(text):
            if extension != "png":
                continue
            resolved = resolve_resource_expression(expression, constants)
            evidence.append(f"Bundle.main.url(forResource: {expression}, withExtension: \"{extension}\")")
            if resolved:
                names.append(resolved)
                referenced_names.setdefault(resolved, []).append(str(swift))

        if evidence:
            overrides.append(
                RuntimeIconOverride(
                    source_file=str(swift),
                    kind="macos-runtime-application-icon",
                    resource_names=sorted(set(names)),
                    evidence=sorted(set(evidence)),
                )
            )

    resources = discover_additional_icon_resources(root, referenced_names)
    return RuntimeIconReport(runtime_icon_overrides=overrides, additional_icon_resources=resources)


def find_string_constants(text: str) -> dict[str, str]:
    constants: dict[str, str] = {}
    pattern = re.compile(r"(?:private\s+)?(?:static\s+)?let\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([^\"]+)\"")
    for name, value in pattern.findall(text):
        constants[name] = value
    return constants


def find_bundle_resource_calls(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"Bundle\.main\.url\(\s*forResource:\s*([^,\n\)]+)\s*,\s*withExtension:\s*\"([^\"]+)\"",
        re.MULTILINE,
    )
    return [(expression.strip(), extension.strip()) for expression, extension in pattern.findall(text)]


def resolve_resource_expression(expression: str, constants: dict[str, str]) -> str | None:
    expression = expression.strip()
    if expression.startswith("\"") and expression.endswith("\""):
        return expression.strip("\"")
    return constants.get(expression)


def discover_additional_icon_resources(root: Path, referenced_names: dict[str, list[str]]) -> list[AdditionalIconResource]:
    resources: dict[Path, AdditionalIconResource] = {}

    for name, references in referenced_names.items():
        for path in find_resource_png(root, name):
            resources[path] = resource_payload(path, source="runtime-reference", referenced_by=references)

    for name in discover_pbx_icon_png_names(root):
        for path in find_resource_png(root, Path(name).stem):
            existing = resources.get(path)
            references = existing.referenced_by if existing else []
            resources[path] = resource_payload(path, source="xcode-resource", referenced_by=references)

    return [resources[path] for path in sorted(resources)]


def discover_pbx_icon_png_names(root: Path) -> set[str]:
    names: set[str] = set()
    for pbxproj in root.rglob("project.pbxproj"):
        if is_ignored(pbxproj):
            continue
        try:
            text = pbxproj.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.findall(r"([A-Za-z0-9_.+ -]*Icon[A-Za-z0-9_.+ -]*\.png)", text):
            names.add(match.strip())
    return names


def find_resource_png(root: Path, name: str) -> list[Path]:
    target = f"{name}.png"
    return [
        path
        for path in sorted(root.rglob(target))
        if path.is_file() and not is_ignored(path)
    ]


def resource_payload(path: Path, *, source: str, referenced_by: list[str]) -> AdditionalIconResource:
    width = height = None
    try:
        with Image.open(path) as image:
            width, height = image.size
    except OSError:
        pass
    return AdditionalIconResource(
        path=str(path),
        name=path.name,
        source=source,
        referenced_by=sorted(set(referenced_by)),
        width=width,
        height=height,
    )


def sync_runtime_icon(
    root: Path,
    source: Path,
    *,
    resource: str,
    size: int,
    backup: bool,
    backup_root: Path | None = None,
) -> WriteResult:
    root = root.resolve()
    source_image = load_master_icon(source, allow_crop=False)
    matches = find_runtime_resource_path(root, resource)
    if not matches:
        raise ValueError(f"runtime icon resource was not found: {resource}")
    if len(matches) > 1:
        raise ValueError(f"runtime icon resource is ambiguous: {resource}; matches: {', '.join(str(path) for path in matches)}")

    output = resized_png(source_image, size, flatten=False)
    changed_files: list[str] = []
    backup_files: list[str] = []
    write_png(
        output,
        matches[0],
        root=root,
        backup=backup,
        backup_root=backup_root,
        timestamp=strftime("%Y%m%d-%H%M%S"),
        changed_files=changed_files,
        backup_files=backup_files,
    )
    return WriteResult(changed_files=changed_files, backup_files=backup_files, removed_files=[])


def find_runtime_resource_path(root: Path, resource: str) -> list[Path]:
    resource_path = Path(resource)
    if resource_path.is_absolute():
        return [resource_path] if resource_path.exists() else []
    if resource_path.suffix:
        matches = [path for path in root.rglob(resource_path.name) if path.is_file() and not is_ignored(path)]
    else:
        matches = find_resource_png(root, resource)
    return sorted(matches)


def runtime_resources_match_source(root: Path, source: Path | None, resources: list[AdditionalIconResource]) -> list[str]:
    if not source or not source.exists():
        return []
    try:
        source_image = load_master_icon(source, allow_crop=False)
    except ValueError:
        return []

    warnings: list[str] = []
    for resource in resources:
        path = Path(resource.path)
        if not path.exists():
            warnings.append(f"runtime icon resource is missing: {path}")
            continue
        try:
            with Image.open(path) as image:
                compare = resized_png(source_image, image.width, flatten=False)
                if image.convert("RGBA").size != compare.size:
                    warnings.append(f"runtime icon resource has unexpected size: {path}")
                elif ImageChops.difference(image.convert("RGB"), compare.convert("RGB")).getbbox():
                    warnings.append(f"runtime icon resource may be out of sync with source icon: {path}")
        except OSError as exc:
            warnings.append(f"runtime icon resource is not a valid PNG: {path}: {exc}")
    return warnings


def is_ignored(path: Path) -> bool:
    return is_ignored_scan_path(path) or any(part in IGNORED_PARTS for part in path.parts)


def backup_dirs(root: Path) -> list[Path]:
    return [path for path in [root / name for name in sorted(BACKUP_DIR_NAMES)] if path.exists()]
