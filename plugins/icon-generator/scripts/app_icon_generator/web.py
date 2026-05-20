from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
from time import strftime
from typing import Any

from PIL import Image

from .image_io import WriteResult, backup_existing, load_master_icon, resized_png, write_png, write_text
from .path_filters import is_ignored_scan_path


@dataclass(frozen=True)
class WebIconSpec:
    filename: str
    pixels: int
    role: str


FAVICON_PNG_SPECS = [
    WebIconSpec("favicon-16x16.png", 16, "favicon"),
    WebIconSpec("favicon-32x32.png", 32, "favicon"),
    WebIconSpec("apple-touch-icon.png", 180, "apple-touch-icon"),
    WebIconSpec("android-chrome-192x192.png", 192, "web-manifest-icon"),
    WebIconSpec("android-chrome-512x512.png", 512, "web-manifest-icon"),
]
FAVICON_ICO_SIZES = [16, 32, 48]


def generate_web_favicons(
    root: Path,
    source: Path,
    *,
    backup: bool,
    backup_root: Path | None = None,
    allow_crop: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    output_dir = find_web_icon_dir(root)
    image = load_master_icon(source, allow_crop=allow_crop)
    changed_files: list[str] = []
    backup_files: list[str] = []
    timestamp = strftime("%Y%m%d-%H%M%S")

    for spec in FAVICON_PNG_SPECS:
        icon = resized_png(image, spec.pixels, flatten=False)
        write_png(
            icon,
            output_dir / spec.filename,
            root=root,
            backup=backup,
            backup_root=backup_root,
            timestamp=timestamp,
            changed_files=changed_files,
            backup_files=backup_files,
        )

    write_ico(
        image,
        output_dir / "favicon.ico",
        root=root,
        backup=backup,
        backup_root=backup_root,
        timestamp=timestamp,
        changed_files=changed_files,
        backup_files=backup_files,
    )

    manifest_path = output_dir / "site.webmanifest"
    manifest = favicon_manifest(manifest_path)
    manifest["icons"] = [
        {
            "src": f"/{spec.filename}",
            "sizes": f"{spec.pixels}x{spec.pixels}",
            "type": "image/png",
        }
        for spec in FAVICON_PNG_SPECS
        if spec.role == "web-manifest-icon"
    ]
    write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        manifest_path,
        root=root,
        backup=backup,
        backup_root=backup_root,
        timestamp=timestamp,
        changed_files=changed_files,
        backup_files=backup_files,
    )

    result = WriteResult(changed_files=changed_files, backup_files=backup_files, removed_files=[])
    return {
        "ok": True,
        "platform": "web",
        "outputDir": str(output_dir),
        "changedFiles": result.changed_files,
        "backupFiles": result.backup_files,
        "removedFiles": result.removed_files,
        "htmlSnippet": html_snippet(),
    }


def write_ico(
    image: Image.Image,
    path: Path,
    *,
    root: Path,
    backup: bool,
    backup_root: Path | None,
    timestamp: str,
    changed_files: list[str],
    backup_files: list[str],
) -> None:
    buffer = BytesIO()
    image.save(buffer, format="ICO", sizes=[(size, size) for size in FAVICON_ICO_SIZES])
    payload = buffer.getvalue()
    if path.exists() and path.read_bytes() == payload:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_existing(path, root=root, backup=backup, backup_root=backup_root, timestamp=timestamp, backup_files=backup_files)
    path.write_bytes(payload)
    changed_files.append(str(path))


def favicon_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def html_snippet() -> str:
    return "\n".join(
        [
            '<link rel="icon" href="/favicon.ico" sizes="any">',
            '<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">',
            '<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">',
            '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">',
            '<link rel="manifest" href="/site.webmanifest">',
        ]
    )


def web_file_plan(root: Path) -> list[dict[str, Any]]:
    output_dir = find_web_icon_dir(root)
    plan = [{"operation": "update", "path": str(output_dir / "favicon.ico"), "sizes": FAVICON_ICO_SIZES}]
    plan.extend(
        {"operation": "update", "path": str(output_dir / spec.filename), "pixels": spec.pixels}
        for spec in FAVICON_PNG_SPECS
    )
    plan.append({"operation": "update", "path": str(output_dir / "site.webmanifest")})
    return plan


def inspect_active_web_icon(root: Path) -> dict[str, Any]:
    root = root.resolve()
    output_dir = find_web_icon_dir(root)
    candidates = web_icon_candidates(output_dir)
    master = max(candidates, key=lambda item: int(item["width"]) * int(item["height"])) if candidates else None
    warnings: list[str] = []
    if not output_dir.exists():
        warnings.append(f"Web icon output directory does not exist yet: {output_dir}")
    if not master:
        warnings.append("No existing favicon PNG source was found.")
    return {
        "activeTarget": {
            "platform": "web",
            "outputDir": str(output_dir),
            "htmlSnippet": html_snippet(),
        },
        "activeAppIconName": "favicon",
        "activeAppIconSetPath": None,
        "activeMasterPng": master,
        "confidence": 0.8 if master else 0.45,
        "warnings": warnings,
    }


def web_icon_candidates(directory: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for spec in FAVICON_PNG_SPECS:
        path = directory / spec.filename
        metadata = png_metadata(path)
        if metadata:
            candidates.append({**metadata, "role": spec.role})
    return candidates


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


def validate_web_favicons(root: Path, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    output_dir = find_web_icon_dir(root)
    checked: list[str] = []
    if not output_dir.exists():
        warnings.append(f"Web favicon output directory was not found: {output_dir}")
        return {"outputDir": str(output_dir), "found": False, "checked": checked}

    for spec in FAVICON_PNG_SPECS:
        path = output_dir / spec.filename
        validate_png_size(path, spec.pixels, errors)
        checked.append(str(path))

    ico = output_dir / "favicon.ico"
    validate_ico(ico, errors)
    checked.append(str(ico))

    manifest = output_dir / "site.webmanifest"
    validate_manifest(manifest, output_dir, errors)
    checked.append(str(manifest))
    return {"outputDir": str(output_dir), "found": True, "checked": checked, "htmlSnippet": html_snippet()}


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


def validate_ico(path: Path, errors: list[str]) -> None:
    if not path.exists():
        errors.append(f"Missing ICO: {path}")
        return
    try:
        with Image.open(path) as image:
            if image.format != "ICO":
                errors.append(f"Invalid ICO format for {path}: {image.format}")
    except OSError as exc:
        errors.append(f"Invalid ICO {path}: {exc}")


def validate_manifest(path: Path, output_dir: Path, errors: list[str]) -> None:
    if not path.exists():
        errors.append(f"Missing web manifest: {path}")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid web manifest {path}: {exc}")
        return
    if not isinstance(payload, dict):
        errors.append(f"Invalid web manifest {path}: root must be an object")
        return
    icons = payload.get("icons")
    if not isinstance(icons, list):
        errors.append(f"Invalid web manifest {path}: icons must be a list")
        return
    for icon in icons:
        if not isinstance(icon, dict):
            errors.append(f"Invalid web manifest icon entry in {path}: {icon}")
            continue
        src = icon.get("src")
        if not isinstance(src, str):
            errors.append(f"Invalid web manifest icon entry missing src in {path}: {icon}")
            continue
        icon_path = output_dir / src.lstrip("/")
        if not icon_path.exists():
            errors.append(f"Web manifest references missing icon: {icon_path}")


def detect_web_target(root: Path) -> dict[str, Any] | None:
    root = root.resolve()
    evidence: list[str] = []
    for filename in [
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
    ]:
        path = root / filename
        if path.exists():
            evidence.append(str(path))
    public_dir = root / "public"
    if public_dir.exists() and (evidence or any((public_dir / filename).exists() for filename in ["favicon.ico", "favicon-32x32.png", "apple-touch-icon.png", "site.webmanifest"])):
        evidence.append(str(public_dir))
    if not evidence:
        for path in sorted(root.rglob("package.json")):
            if is_ignored_scan_path(path):
                continue
            evidence.append(str(path))
            root = path.parent
            break
    if not evidence:
        return None
    return {
        "root": str(root),
        "evidence": sorted(set(evidence)),
        "outputDir": str(find_web_icon_dir(root)),
    }


def is_web_project(root: Path) -> bool:
    return detect_web_target(root) is not None


def has_web_favicon_files(root: Path) -> bool:
    return any(
        (find_web_icon_dir(root) / filename).exists()
        for filename in ["favicon.ico", *(spec.filename for spec in FAVICON_PNG_SPECS), "site.webmanifest"]
    )


def find_web_icon_dir(root: Path) -> Path:
    root = root.resolve()
    public_dir = root / "public"
    if public_dir.exists():
        return public_dir
    if any((root / filename).exists() for filename in ["favicon.ico", "favicon-32x32.png", "apple-touch-icon.png", "site.webmanifest"]):
        return root
    if (root / "index.html").exists() and not (root / "package.json").exists():
        return root
    return public_dir


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
