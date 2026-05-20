from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .active_icon import inspect_active_icon
from .path_filters import COMMON_IGNORED_DIR_NAMES, is_ignored_scan_path
from .web import detect_web_target
from .xcode_inspect import inspect_xcode_project


IGNORED_DIRS = {*COMMON_IGNORED_DIR_NAMES}


@dataclass(frozen=True)
class ProjectTarget:
    platform: str
    root: str
    evidence: list[str]
    confidence: float = 1.0
    target_name: str | None = None
    bundle_identifier: str | None = None
    app_icon_name: str | None = None
    app_icon_set_path: str | None = None
    sdk_root: str | None = None
    supported_platforms: list[str] | None = None
    info_plist_file: str | None = None
    runtime_icon_overrides: list[dict[str, object]] | None = None
    additional_icon_resources: list[dict[str, object]] | None = None
    active_icon: dict[str, object] | None = None
    warnings: list[str] | None = None


@dataclass(frozen=True)
class ProjectDetection:
    root: str
    platforms: list[str]
    targets: list[ProjectTarget]

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "platforms": self.platforms,
            "targets": [asdict(target) for target in self.targets],
        }


def detect_project(root: Path) -> ProjectDetection:
    root = root.resolve()
    targets: list[ProjectTarget] = []
    targets.extend(detect_android_targets(root))
    targets.extend(detect_apple_targets(root))
    targets.extend(detect_web_targets(root))

    platforms = sorted({target.platform for target in targets})
    return ProjectDetection(root=str(root), platforms=platforms, targets=targets)


def detect_android_targets(root: Path) -> list[ProjectTarget]:
    candidates: dict[Path, list[str]] = {}

    for directory in walk_dirs(root, max_depth=4):
        evidence: list[str] = []
        for filename in ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts"):
            marker = directory / filename
            if marker.exists():
                evidence.append(str(marker))

        manifest = directory / "app" / "src" / "main" / "AndroidManifest.xml"
        res_dir = directory / "app" / "src" / "main" / "res"
        if manifest.exists():
            evidence.append(str(manifest))
        if res_dir.exists():
            evidence.append(str(res_dir))

        direct_manifest = directory / "src" / "main" / "AndroidManifest.xml"
        direct_res_dir = directory / "src" / "main" / "res"
        if direct_manifest.exists():
            evidence.append(str(direct_manifest))
        if direct_res_dir.exists():
            evidence.append(str(direct_res_dir))

        if evidence:
            target_root = infer_android_project_root(directory, root)
            candidates.setdefault(target_root, []).extend(evidence)

    return [
        ProjectTarget(
            platform="android",
            root=str(path),
            evidence=sorted(set(evidence)),
            active_icon=detect_active_icon(path, platform="android"),
        )
        for path, evidence in sorted(candidates.items(), key=lambda item: str(item[0]))
    ]


def detect_apple_targets(root: Path) -> list[ProjectTarget]:
    xcode_infos = inspect_xcode_project(root, run_xcodebuild=False)
    if xcode_infos:
        return [
            ProjectTarget(
                platform=info.platform,
                root=info.project_root,
                evidence=sorted(set([info.project_path, *info.evidence])),
                confidence=info.confidence,
                target_name=info.target_name,
                bundle_identifier=info.bundle_identifier,
                app_icon_name=info.app_icon_name,
                app_icon_set_path=info.app_icon_set_path,
                sdk_root=info.sdk_root,
                supported_platforms=info.supported_platforms,
                info_plist_file=info.info_plist_file,
                runtime_icon_overrides=info.runtime_icon_overrides,
                additional_icon_resources=info.additional_icon_resources,
                active_icon=detect_active_icon(Path(info.project_root), platform="apple"),
                warnings=info.warnings,
            )
            for info in xcode_infos
        ]

    candidates: dict[tuple[str, Path], list[str]] = {}

    for directory in walk_dirs(root, max_depth=5):
        evidence: list[str] = []
        if directory.suffix in {".xcodeproj", ".xcworkspace", ".xcassets"}:
            evidence.append(str(directory))
        if directory.name.endswith(".appiconset"):
            evidence.append(str(directory))

        if not evidence:
            continue

        platform = infer_apple_platform(directory)
        target_root = infer_apple_project_root(directory, root)
        candidates.setdefault((platform, target_root), []).extend(evidence)

    return [
        ProjectTarget(
            platform=platform,
            root=str(path),
            evidence=sorted(set(evidence)),
            active_icon=detect_active_icon(path, platform=platform),
        )
        for (platform, path), evidence in sorted(candidates.items(), key=lambda item: (item[0][0], str(item[0][1])))
    ]


def detect_web_targets(root: Path) -> list[ProjectTarget]:
    target = detect_web_target(root)
    if not target:
        return []
    target_root = Path(str(target["root"]))
    return [
        ProjectTarget(
            platform="web",
            root=str(target_root),
            evidence=list(target["evidence"]),
            confidence=0.85,
            target_name="web",
            app_icon_name="favicon",
            active_icon=detect_active_icon(target_root, platform="web"),
            warnings=None,
        )
    ]


def infer_android_project_root(directory: Path, workspace_root: Path) -> Path:
    parts = directory.relative_to(workspace_root).parts if directory != workspace_root else ()
    if directory.name == "app" and has_gradle_marker(directory.parent):
        return directory.parent
    if parts and parts[0] == "android":
        return workspace_root / parts[0]
    if parts and parts[0] == "app":
        return workspace_root
    return directory


def detect_active_icon(root: Path, *, platform: str) -> dict[str, object]:
    payload = inspect_active_icon(root, platform=platform, run_xcodebuild=False)
    history = payload.get("ignoredHistory")
    if isinstance(history, dict):
        payload["ignoredHistory"] = {"count": history.get("count", 0)}
    return payload


def has_gradle_marker(directory: Path) -> bool:
    return any(
        (directory / filename).exists()
        for filename in ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts")
    )


def infer_apple_project_root(directory: Path, workspace_root: Path) -> Path:
    parts = directory.relative_to(workspace_root).parts if directory != workspace_root else ()
    if parts and parts[0] in {"ios", "macos"}:
        return workspace_root / parts[0]
    if directory.suffix in {".xcodeproj", ".xcworkspace"}:
        return directory.parent
    for parent in [directory, *directory.parents]:
        if parent == workspace_root:
            return workspace_root
        if any(child.suffix in {".xcodeproj", ".xcworkspace"} for child in parent.iterdir() if child.exists()):
            return parent
    return directory.parent


def infer_apple_platform(path: Path) -> str:
    lowered = str(path).lower()
    if "macos" in lowered or "mac app" in lowered or "macapp" in lowered:
        return "macos"
    return "ios"


def walk_dirs(root: Path, *, max_depth: int) -> list[Path]:
    result: list[Path] = []
    root_depth = len(root.parts)
    for path in root.rglob("*"):
        if not path.is_dir():
            continue
        if len(path.parts) - root_depth > max_depth:
            continue
        if is_ignored_scan_path(path) or any(part in IGNORED_DIRS for part in path.parts):
            continue
        result.append(path)
    return [root, *sorted(result)]
