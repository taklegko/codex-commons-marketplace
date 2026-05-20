from __future__ import annotations

from pathlib import Path


BACKUP_DIR_NAMES = {".icon-generator-backups", ".app-icon-generator-backups"}
BUILD_ARTIFACT_DIR_NAMES = {"build", "DerivedData", ".build"}
COMMON_IGNORED_DIR_NAMES = {
    ".git",
    ".gradle",
    ".idea",
    ".swiftpm",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "Pods",
    *BACKUP_DIR_NAMES,
    *BUILD_ARTIFACT_DIR_NAMES,
}
GENERATED_SOURCE_DIR_NAMES = {"IconSource"}
PROJECT_FILE_MARKERS = {
    "settings.gradle",
    "settings.gradle.kts",
    "build.gradle",
    "build.gradle.kts",
    "AndroidManifest.xml",
    "Package.swift",
    "package.json",
    "index.html",
    "next.config.js",
    "next.config.mjs",
    "vite.config.js",
    "vite.config.ts",
    "astro.config.mjs",
    "svelte.config.js",
    "nuxt.config.ts",
    "nuxt.config.js",
}


def is_backup_path(path: Path) -> bool:
    return any(part in BACKUP_DIR_NAMES for part in path.parts)


def is_build_artifact_path(path: Path) -> bool:
    return any(part in BUILD_ARTIFACT_DIR_NAMES for part in path.parts)


def is_generated_source_path(path: Path) -> bool:
    return any(part in GENERATED_SOURCE_DIR_NAMES for part in path.parts)


def is_ignored_scan_path(path: Path) -> bool:
    return any(part in COMMON_IGNORED_DIR_NAMES for part in path.parts)


def backup_history(root: Path, *, limit: int = 25) -> dict[str, object]:
    root = root.resolve()
    dirs = [
        path
        for path in root.rglob("*")
        if path.is_dir() and path.name in BACKUP_DIR_NAMES
    ]
    appiconsets = [
        path
        for path in root.rglob("*.appiconset")
        if is_backup_path(path)
    ]
    return {
        "count": len(appiconsets),
        "backupDirs": [str(path) for path in sorted(dirs)[:limit]],
        "samplePaths": [str(path) for path in sorted(appiconsets)[:limit]],
    }


def is_project_resource_path(path: Path) -> bool:
    return any(
        part.endswith((".xcassets", ".appiconset", ".imageset")) or part in {"App", "Resources", "res", "mipmap-anydpi-v26"}
        for part in path.parts
    )


def looks_like_project_root(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    try:
        children = list(path.iterdir())
    except OSError:
        return False
    if any(child.suffix in {".xcodeproj", ".xcworkspace"} for child in children):
        return True
    if any(child.name in PROJECT_FILE_MARKERS for child in children):
        return True
    if (path / "app" / "src" / "main" / "AndroidManifest.xml").exists():
        return True
    if (path / "src" / "main" / "AndroidManifest.xml").exists():
        return True
    return False


def nearest_project_root(path: Path) -> Path | None:
    probe = path.resolve()
    if probe.suffix:
        probe = probe.parent
    for candidate in [probe, *probe.parents]:
        if looks_like_project_root(candidate):
            return candidate
    return None


def is_unsafe_project_output_path(path: Path) -> bool:
    resolved = path.resolve()
    if is_backup_path(resolved) or is_build_artifact_path(resolved) or is_generated_source_path(resolved):
        return True
    if is_project_resource_path(resolved):
        return True
    return nearest_project_root(resolved) is not None


def assert_safe_generated_output_path(path: Path, *, allow_project_output: bool, label: str = "output") -> None:
    if allow_project_output:
        return
    if is_unsafe_project_output_path(path):
        raise ValueError(
            f"{label} must be written to the icon-generator run directory or another non-project location; "
            "do not write diagnostics to IconSource, app resources, backups, build output, or project root paths; "
            "pass --allow-project-output only when the user explicitly approved writing diagnostics into the project"
        )
