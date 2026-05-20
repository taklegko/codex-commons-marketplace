from __future__ import annotations

from pathlib import Path
import json

from PIL import Image
import pytest

import app_icon_generator.icon_composer as icon_composer
from app_icon_generator.icon_composer import generate_icon_composer_bundle, icon_composer_install_guide, icon_composer_preflight, wait_icon_composer_ready
from app_icon_generator.workflow import apply_approval, approve_apply, finalize_icon_run, plan_apply, prepare_icon_run, record_imagegen_result


def make_png(path: Path, size: int = 1024, color: tuple[int, int, int, int] = (1, 2, 3, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (size, size), color).save(path)


def make_realistic_macos_project(root: Path, *, deployment: str = "26.0") -> None:
    xcodeproj = root / "replyx.xcodeproj"
    appiconset = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset"
    xcodeproj.mkdir(parents=True)
    appiconset.mkdir(parents=True)
    (appiconset / "Contents.json").write_text(
        '{"images":[{"idiom":"mac","size":"512x512","scale":"2x","filename":"icon_512x512@2x.png"}],"info":{"author":"xcode","version":1}}\n',
        encoding="utf-8",
    )
    make_png(appiconset / "icon_512x512@2x.png")
    (xcodeproj / "project.pbxproj").write_text(
        f"""
// !$*UTF8*$!
{{
  objects = {{
/* Begin PBXBuildFile section */
/* End PBXBuildFile section */
/* Begin PBXFileReference section */
/* End PBXFileReference section */
/* Begin PBXGroup section */
    111111111111111111111111 /* Resources */ = {{
      isa = PBXGroup;
      children = (
      );
      path = Resources;
      sourceTree = "<group>";
    }};
/* End PBXGroup section */
/* Begin PBXResourcesBuildPhase section */
    222222222222222222222222 /* Resources */ = {{
      isa = PBXResourcesBuildPhase;
      buildActionMask = 2147483647;
      files = (
      );
      runOnlyForDeploymentPostprocessing = 0;
    }};
/* End PBXResourcesBuildPhase section */
    buildSettings = {{
      SDKROOT = macosx;
      MACOSX_DEPLOYMENT_TARGET = {deployment};
      PRODUCT_NAME = ReplyX;
      PRODUCT_BUNDLE_IDENTIFIER = dev.local.ReplyX;
      ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;
    }};
  }};
}}
""",
        encoding="utf-8",
    )


def test_icon_composer_preflight_reports_missing_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(icon_composer.shutil, "which", lambda _name: None)
    monkeypatch.setattr(icon_composer, "find_icon_composer_app", lambda: None)
    monkeypatch.setattr(icon_composer, "find_ictool", lambda: None)

    payload = icon_composer_preflight()

    assert payload["ok"] is False
    assert payload["installRequired"] is True
    assert payload["canUseIconComposer"] is False
    assert payload["blockingReasons"]
    assert "Icon Composer is required" in payload["installMessage"]
    assert any(option["id"] == "homebrew-cask" for option in payload["installOptions"])


def test_generate_icon_composer_stops_when_preflight_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    foreground = tmp_path / "glyph.png"
    foreground.write_bytes(b"not-used")
    monkeypatch.setattr(
        icon_composer,
        "icon_composer_preflight",
        lambda: {
            "ok": False,
            "installMessage": "Icon Composer unavailable",
        },
    )

    with pytest.raises(ValueError, match="Icon Composer unavailable"):
        generate_icon_composer_bundle(
            tmp_path,
            foreground,
            bg_color="#111111",
            bundle_name="AppIcon",
            backup=True,
        )


def test_icon_composer_preflight_selects_ictool_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_ictool = tmp_path / "ictool"
    fake_icon_composer = tmp_path / "Icon Composer.app"
    fake_ictool.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_icon_composer.mkdir()

    def fake_which(name: str) -> str | None:
        if name in {"node", "npx"}:
            return f"/usr/bin/{name}"
        return None

    monkeypatch.setattr(icon_composer.shutil, "which", fake_which)
    monkeypatch.setattr(icon_composer, "find_icon_composer_app", lambda: fake_icon_composer)
    monkeypatch.setattr(icon_composer, "find_ictool", lambda: fake_ictool)
    monkeypatch.setattr(icon_composer, "read_command_version", lambda _command: "v24.0.0")
    monkeypatch.setattr(icon_composer, "check_icon_composer_package", lambda: {"checked": True, "ready": False, "error": "sharp failed"})

    payload = icon_composer.icon_composer_preflight()

    assert payload["ok"] is True
    assert payload["mcpReady"] is False
    assert payload["ictoolReady"] is True
    assert payload["selectedBackend"] == "ictool"


def test_icon_composer_preflight_allows_ictool_without_node(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_ictool = tmp_path / "ictool"
    fake_icon_composer = tmp_path / "Icon Composer.app"
    fake_ictool.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_icon_composer.mkdir()
    monkeypatch.setattr(icon_composer.shutil, "which", lambda _name: None)
    monkeypatch.setattr(icon_composer, "find_icon_composer_app", lambda: fake_icon_composer)
    monkeypatch.setattr(icon_composer, "find_ictool", lambda: fake_ictool)

    payload = icon_composer_preflight()

    assert payload["ok"] is True
    assert payload["canUseIconComposer"] is True
    assert payload["installRequired"] is False
    assert payload["selectedBackend"] == "ictool"
    assert payload["mcpReady"] is False


def test_icon_composer_install_guide_is_manual_only() -> None:
    payload = icon_composer_install_guide()

    assert payload["manualOnly"] is True
    assert any(option["id"] == "homebrew-cask" for option in payload["installOptions"])
    assert "wait-icon-composer-ready" in payload["verificationCommand"]


def test_wait_icon_composer_ready_succeeds_after_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = iter(
        [
            {"canUseIconComposer": False, "liquidGlassReady": False},
            {"canUseIconComposer": True, "liquidGlassReady": True},
        ]
    )
    monkeypatch.setattr(icon_composer, "icon_composer_preflight", lambda check_package=False: next(payloads))

    payload = wait_icon_composer_ready(timeout=1, interval=0)

    assert payload["ok"] is True
    assert payload["ready"] is True
    assert payload["attempts"] == 2


def test_wait_icon_composer_ready_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(icon_composer, "icon_composer_preflight", lambda check_package=False: {"canUseIconComposer": False, "liquidGlassReady": False})

    payload = wait_icon_composer_ready(timeout=0, interval=0)

    assert payload["ok"] is False
    assert payload["timedOut"] is True
    assert payload["installGuide"]["manualOnly"] is True


def test_generate_icon_composer_writes_bundle_outside_xcassets_and_updates_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    make_realistic_macos_project(tmp_path)
    source = tmp_path / "source.png"
    make_png(source, color=(7, 8, 9, 255))
    monkeypatch.setattr(
        icon_composer,
        "icon_composer_preflight",
        lambda: {
            "ok": True,
            "selectedBackend": "ictool",
            "mcpReady": False,
            "ictoolReady": True,
            "liquidGlassReady": True,
            "installMessage": None,
            "warnings": [],
        },
    )

    result = generate_icon_composer_bundle(tmp_path, source, bg_color="#111111", bundle_name="AppIcon", backup=True, backup_root=tmp_path / "run" / "backups")

    bundle = tmp_path / "App" / "Resources" / "AppIcon.icon"
    old_iconset = tmp_path / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset"
    assert result["ok"] is True
    assert Path(result["bundlePath"]) == bundle
    assert bundle.exists()
    assert not (tmp_path / "App" / "Resources" / "Assets.xcassets" / "AppIcon.icon").exists()
    assert not old_iconset.exists()
    assert result["removedFiles"]
    pbxproj_text = (tmp_path / "replyx.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
    assert "path = AppIcon.icon;" in pbxproj_text
    assert "path = \"App/Resources/AppIcon.icon\"" not in pbxproj_text
    assert pbxproj_text.count("/* AppIcon.icon */ = {isa = PBXFileReference;") == 1
    assert "AppIcon.icon in Resources" in pbxproj_text
    icon_json = json.loads((bundle / "icon.json").read_text(encoding="utf-8"))
    assert icon_json["groups"][0]["layers"][0]["position"]["scale"] == 1


def test_icon_composer_project_state_fails_when_legacy_iconset_still_exists(tmp_path: Path) -> None:
    make_realistic_macos_project(tmp_path)
    bundle = tmp_path / "App" / "Resources" / "AppIcon.icon"
    make_png(bundle / "Assets" / "foreground.png")
    (bundle / "icon.json").write_text(
        json.dumps({"groups": [{"layers": [{"position": {"scale": 1}}]}]}) + "\n",
        encoding="utf-8",
    )

    payload = icon_composer.inspect_icon_composer_project_state(tmp_path, "AppIcon")

    assert payload["ok"] is False
    assert payload["legacyAppIconSetPresent"] is True
    assert any("legacy AppIcon.appiconset" in error for error in payload["errors"])


def test_workflow_icon_composer_apply_verifies_connected_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    make_realistic_macos_project(tmp_path / "project")
    source = tmp_path / "generated" / "ig_variant.png"
    make_png(source, color=(11, 12, 13, 255))
    monkeypatch.setattr(
        icon_composer,
        "icon_composer_preflight",
        lambda: {
            "ok": True,
            "selectedBackend": "ictool",
            "mcpReady": False,
            "ictoolReady": True,
            "liquidGlassReady": True,
            "installMessage": None,
            "warnings": [],
        },
    )

    root = tmp_path / "project"
    run_dir = Path(prepare_icon_run(root, description="reply icon", variants=2)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=source)
    finalize_icon_run(run_dir, job_id="variant-1")
    plan = plan_apply(run_dir, platform="macos", mode="icon-composer", backup=True)
    approval = approve_apply(run_dir, apply_plan=Path(plan["applyPlan"]), approval_note="Apply Icon Composer icon.")

    result = apply_approval(Path(approval["approval"]))

    assert result["ok"] is True
    assert result["activeIconComposerPath"].endswith("App/Resources/AppIcon.icon")
    assert result["appliedPreviewSourcePath"].endswith("App/Resources/AppIcon.icon/Assets/foreground.png")
    assert result["postApplyVerification"]["ok"] is True
    assert result["postApplyVerification"]["iconComposer"]["projectResourcesContainIconBundle"] is True
    assert result["postApplyVerification"]["iconComposer"]["navigatorReferencesClean"] is True


def test_icon_composer_apply_cleans_duplicate_wrong_xcode_reference(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    make_realistic_macos_project(tmp_path)
    pbxproj = tmp_path / "replyx.xcodeproj" / "project.pbxproj"
    text = pbxproj.read_text(encoding="utf-8")
    text = text.replace(
        "/* Begin PBXBuildFile section */\n",
        "/* Begin PBXBuildFile section */\n"
        "\t\tBBBBBBBBBBBBBBBBBBBBBBBB /* AppIcon.icon in Resources */ = {isa = PBXBuildFile; fileRef = AAAAAAAAAAAAAAAAAAAAAAAA /* AppIcon.icon */; };\n",
    )
    text = text.replace(
        "/* Begin PBXFileReference section */\n",
        "/* Begin PBXFileReference section */\n"
        "\t\tAAAAAAAAAAAAAAAAAAAAAAAA /* AppIcon.icon */ = {isa = PBXFileReference; lastKnownFileType = folder.iconcomposer.icon; path = \"App/Resources/AppIcon.icon\"; sourceTree = \"<group>\"; };\n"
        "\t\tCCCCCCCCCCCCCCCCCCCCCCCC /* AppIcon.icon */ = {isa = PBXFileReference; lastKnownFileType = folder.iconcomposer.icon; path = AppIcon.icon; sourceTree = \"<group>\"; };\n",
    )
    text = text.replace(
        "children = (\n",
        "children = (\n"
        "\t\t\t\tAAAAAAAAAAAAAAAAAAAAAAAA /* AppIcon.icon */,\n"
        "\t\t\t\tCCCCCCCCCCCCCCCCCCCCCCCC /* AppIcon.icon */,\n",
        1,
    )
    text = text.replace(
        "files = (\n",
        "files = (\n"
        "\t\t\t\tBBBBBBBBBBBBBBBBBBBBBBBB /* AppIcon.icon in Resources */,\n",
        1,
    )
    pbxproj.write_text(text, encoding="utf-8")
    source = tmp_path / "source.png"
    make_png(source)
    monkeypatch.setattr(
        icon_composer,
        "icon_composer_preflight",
        lambda: {
            "ok": True,
            "selectedBackend": "ictool",
            "mcpReady": False,
            "ictoolReady": True,
            "liquidGlassReady": True,
            "installMessage": None,
            "warnings": [],
        },
    )

    generate_icon_composer_bundle(
        tmp_path,
        source,
        bg_color="#111111",
        bundle_name="AppIcon",
        backup=True,
        backup_root=tmp_path.parent / f"{tmp_path.name}-run-backups",
    )
    payload = icon_composer.inspect_icon_composer_project_state(tmp_path, "AppIcon")
    pbxproj_text = pbxproj.read_text(encoding="utf-8")

    assert payload["ok"] is True
    assert payload["navigatorReferencesClean"] is True
    assert payload["canonicalIconReferencePath"] == "AppIcon.icon"
    assert "path = \"App/Resources/AppIcon.icon\"" not in pbxproj_text
    assert pbxproj_text.count("/* AppIcon.icon */ = {isa = PBXFileReference;") == 1
    assert pbxproj_text.count("/* AppIcon.icon */,") == 1


def test_icon_composer_project_state_fails_on_duplicate_references(tmp_path: Path) -> None:
    bundle = tmp_path / "App" / "Resources" / "AppIcon.icon"
    make_png(bundle / "Assets" / "foreground.png")
    (bundle / "icon.json").write_text(json.dumps({"groups": [{"layers": [{"position": {"scale": 1}}]}]}) + "\n", encoding="utf-8")
    xcodeproj = tmp_path / "replyx.xcodeproj"
    xcodeproj.mkdir()
    (xcodeproj / "project.pbxproj").write_text(
        """
// !$*UTF8*$!
{
  objects = {
/* Begin PBXBuildFile section */
    BBBBBBBBBBBBBBBBBBBBBBBB /* AppIcon.icon in Resources */ = {isa = PBXBuildFile; fileRef = AAAAAAAAAAAAAAAAAAAAAAAA /* AppIcon.icon */; };
/* End PBXBuildFile section */
/* Begin PBXFileReference section */
    AAAAAAAAAAAAAAAAAAAAAAAA /* AppIcon.icon */ = {isa = PBXFileReference; lastKnownFileType = folder.iconcomposer.icon; path = AppIcon.icon; sourceTree = "<group>"; };
    CCCCCCCCCCCCCCCCCCCCCCCC /* AppIcon.icon */ = {isa = PBXFileReference; lastKnownFileType = folder.iconcomposer.icon; path = "App/Resources/AppIcon.icon"; sourceTree = "<group>"; };
/* End PBXFileReference section */
/* Begin PBXGroup section */
    111111111111111111111111 /* Resources */ = {isa = PBXGroup; children = (
        AAAAAAAAAAAAAAAAAAAAAAAA /* AppIcon.icon */,
        CCCCCCCCCCCCCCCCCCCCCCCC /* AppIcon.icon */,
    ); path = Resources; sourceTree = "<group>"; };
/* End PBXGroup section */
/* Begin PBXResourcesBuildPhase section */
    222222222222222222222222 /* Resources */ = {isa = PBXResourcesBuildPhase; files = (
        BBBBBBBBBBBBBBBBBBBBBBBB /* AppIcon.icon in Resources */,
    ); };
/* End PBXResourcesBuildPhase section */
  };
}
""",
        encoding="utf-8",
    )

    payload = icon_composer.inspect_icon_composer_project_state(tmp_path, "AppIcon")

    assert payload["ok"] is False
    assert payload["navigatorReferencesClean"] is False
    assert payload["duplicateIconComposerReferences"]
    assert payload["wrongIconReferencePaths"]
