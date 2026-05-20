from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
import pytest

from app_icon_generator.active_icon import extract_current_master, inspect_active_icon
from app_icon_generator.detect_project import detect_project
from app_icon_generator.validate import validate_project
from app_icon_generator.workflow import apply_approval, approve_apply, finalize_icon_run, plan_apply, prepare_icon_run, record_imagegen_result


def make_png(path: Path, size: int = 1024, color: tuple[int, int, int, int] = (20, 80, 160, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (size, size), color).save(path)


def make_xcode_active_fixture(root: Path) -> Path:
    xcodeproj = root / "replyx.xcodeproj"
    active = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset"
    source_dir = root / "App" / "Resources" / "IconSource"
    backup = root / ".app-icon-generator-backups" / "old" / "App" / "Resources" / "Assets.xcassets" / "ReplyXAppIcon.appiconset"
    xcodeproj.mkdir(parents=True)
    active.mkdir(parents=True)
    backup.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    (xcodeproj / "project.pbxproj").write_text(
        """
        {
          buildSettings = {
            SDKROOT = macosx;
            PRODUCT_NAME = ReplyX;
            PRODUCT_BUNDLE_IDENTIFIER = dev.local.ReplyX;
            ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;
          };
        }
        """,
        encoding="utf-8",
    )
    contents = {
        "images": [
            {"idiom": "mac", "size": "16x16", "scale": "1x", "filename": "icon_16x16.png"},
            {"idiom": "mac", "size": "512x512", "scale": "2x", "filename": "icon_512x512@2x.png"},
        ],
        "info": {"author": "xcode", "version": 1},
    }
    (active / "Contents.json").write_text(json.dumps(contents), encoding="utf-8")
    (backup / "Contents.json").write_text(json.dumps(contents), encoding="utf-8")
    make_png(active / "icon_16x16.png", size=16, color=(1, 1, 1, 255))
    make_png(active / "icon_512x512@2x.png", size=1024, color=(10, 20, 30, 255))
    make_png(backup / "icon_512x512@2x.png", size=1024, color=(240, 240, 240, 255))
    make_png(source_dir / "replyx-app-icon-master.png", size=1024, color=(200, 0, 0, 255))
    return active


def make_android_active_fixture(root: Path) -> Path:
    res = root / "app" / "src" / "main" / "res"
    manifest = root / "app" / "src" / "main" / "AndroidManifest.xml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        """<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <application android:icon="@mipmap/ic_launcher" />
</manifest>
""",
        encoding="utf-8",
    )
    make_png(res / "mipmap-mdpi" / "ic_launcher.png", size=48, color=(1, 2, 3, 255))
    make_png(res / "mipmap-xxxhdpi" / "ic_launcher.png", size=192, color=(4, 5, 6, 255))
    return manifest


def test_detect_excludes_backup_paths_from_evidence(tmp_path: Path) -> None:
    make_xcode_active_fixture(tmp_path)

    payload = detect_project(tmp_path).to_dict()
    text = json.dumps(payload)

    assert ".app-icon-generator-backups" not in text
    assert payload["targets"][0]["active_icon"]["activeAppIconSetPath"].endswith("AppIcon.appiconset")


def test_active_icon_chooses_build_setting_appicon_not_backup(tmp_path: Path) -> None:
    active = make_xcode_active_fixture(tmp_path)

    payload = inspect_active_icon(tmp_path, platform="macos", run_xcodebuild=False)

    assert payload["activeAppIconName"] == "AppIcon"
    assert payload["activeAppIconSetPath"] == str(active)
    assert payload["activeMasterPng"]["path"] == str(active / "icon_512x512@2x.png")
    assert payload["ignoredHistory"]["count"] == 1
    assert payload["sourceCandidates"][0]["path"].endswith("IconSource/replyx-app-icon-master.png")
    assert payload["sourceCandidates"][0]["classification"] == "historical-source-reference"
    assert payload["sourceCandidates"][0]["usableByDefault"] is False
    assert payload["sourceOptions"][0]["role"] == "active-current"
    assert payload["sourceOptions"][0]["recommended"] is True
    assert len(payload["sourceOptions"]) == 1
    assert payload["sourceSelection"]["requiresUserChoice"] is False


def test_extract_current_master_apple_uses_active_appiconset_only(tmp_path: Path) -> None:
    root = tmp_path / "project"
    make_xcode_active_fixture(root)
    out = tmp_path / "out" / "current.png"

    payload = extract_current_master(root, out, platform="macos")

    assert payload["sourcePath"].endswith("AppIcon.appiconset/icon_512x512@2x.png")
    with Image.open(out) as image:
        assert image.size == (1024, 1024)
        assert image.getpixel((0, 0)) == (10, 20, 30, 255)


def test_extract_current_master_rejects_iconsource_output(tmp_path: Path) -> None:
    root = tmp_path / "project"
    make_xcode_active_fixture(root)
    out = root / "App" / "Resources" / "IconSource" / "current.png"

    with pytest.raises(ValueError, match="IconSource"):
        extract_current_master(root, out, platform="macos")


def test_extract_current_master_android_normalizes_largest_launcher_png(tmp_path: Path) -> None:
    root = tmp_path / "android"
    make_android_active_fixture(root)
    out = tmp_path / "out" / "current-android.png"

    payload = extract_current_master(root, out, platform="android")

    assert payload["sourcePath"].endswith("mipmap-xxxhdpi/ic_launcher.png")
    with Image.open(out) as image:
        assert image.size == (1024, 1024)
        assert image.getpixel((0, 0)) == (4, 5, 6, 255)


def test_prepare_from_current_records_active_master_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    make_xcode_active_fixture(tmp_path / "project")

    payload = prepare_icon_run(tmp_path / "project", description="make the blue dot glow brighter", variants=2, from_current=True)
    request = json.loads((Path(payload["runDir"]) / "request.json").read_text(encoding="utf-8"))
    prompt = (Path(payload["runDir"]) / "prompts" / "variant-1.md").read_text(encoding="utf-8")

    assert request["currentMasterSourcePath"].endswith("AppIcon.appiconset/icon_512x512@2x.png")
    assert request["currentMasterSha256"]
    assert request["sourceOptions"][0]["role"] == "active-current"
    assert "preserve the current composition" in prompt
    assert payload["jobs"][0]["kind"] == "edit-current-variant"


def test_apply_returns_preview_from_active_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_xcode_active_fixture(root)
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated, color=(7, 8, 9, 255))

    run_dir = Path(prepare_icon_run(root, description="reply icon", variants=2)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    finalize_icon_run(run_dir, job_id="variant-1")
    plan = plan_apply(run_dir, platform="auto", mode="legacy-appiconset", backup=True)
    approval = approve_apply(run_dir, apply_plan=Path(plan["applyPlan"]), approval_note="Approved active icon apply.")

    result = apply_approval(Path(approval["approval"]))

    assert result["appliedPreviewPath"].endswith(".png")
    assert Path(result["appliedPreviewPath"]).exists()
    assert result["appliedPreviewSourcePath"].endswith("AppIcon.appiconset/icon_512x512@2x.png")
    assert result["activeIconSetPath"].endswith("AppIcon.appiconset")
    assert result["postApplyVerification"]["activeAssetComparison"]["activeMatchesApprovedSource"] is True
    assert any("Rebuild the app" in item for item in result["postApplyVerification"]["visibilityGuidance"])


def test_validate_classifies_active_sources_history_and_candidates(tmp_path: Path) -> None:
    make_xcode_active_fixture(tmp_path)

    payload = validate_project(tmp_path)

    assert payload["activeIcon"]["activeAppIconSetPath"].endswith("AppIcon.appiconset")
    assert payload["sourceCandidates"][0]["classification"] == "historical-source-reference"
    assert payload["ignoredHistory"]["count"] == 1
