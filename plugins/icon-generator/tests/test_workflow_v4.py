from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
import pytest

from app_icon_generator.cli import main
import app_icon_generator.workflow as workflow
from app_icon_generator.workflow import (
    apply_approval,
    approve_edit_brief,
    approve_apply,
    finalize_icon_run,
    label_icon_variant,
    plan_apply,
    prepare_icon_run,
    queue_icon_repairs,
    record_imagegen_result,
    verify_applied_icon,
)


def make_png(path: Path, size: int = 1024, color: tuple[int, int, int, int] = (20, 80, 160, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (size, size), color).save(path)


def make_macos_fixture(root: Path, *, deployment: str | None = None) -> None:
    xcodeproj = root / "ReplyX.xcodeproj"
    appiconset = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset"
    xcodeproj.mkdir(parents=True)
    appiconset.mkdir(parents=True)
    deployment_line = f"            MACOSX_DEPLOYMENT_TARGET = {deployment};\n" if deployment else ""
    (xcodeproj / "project.pbxproj").write_text(
        """
        {
          buildSettings = {
            SDKROOT = macosx;
""" + deployment_line + """
            PRODUCT_NAME = ReplyX;
            PRODUCT_BUNDLE_IDENTIFIER = dev.local.ReplyX;
            ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;
          };
        }
        """,
        encoding="utf-8",
    )
    (appiconset / "Contents.json").write_text(
        '{"images":[{"idiom":"mac","size":"512x512","scale":"2x","filename":"icon_512x512@2x.png"}],"info":{"author":"xcode","version":1}}\n',
        encoding="utf-8",
    )


def make_replyx_runtime_fixture(root: Path) -> Path:
    make_macos_fixture(root)
    swift = root / "App" / "Core" / "AppIconManager.swift"
    notification = root / "App" / "Resources" / "NotificationIcon.png"
    swift.parent.mkdir(parents=True)
    make_png(notification, size=512, color=(220, 10, 20, 255))
    (root / "ReplyX.xcodeproj" / "project.pbxproj").write_text(
        """
        {
          1 /* NotificationIcon.png in Resources */ = {isa = PBXBuildFile; };
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
    swift.write_text(
        """
        enum AppIconManager {
            private static let iconName = "NotificationIcon"
            static func applyApplicationIcon() {
                guard let url = Bundle.main.url(forResource: iconName, withExtension: "png") else { return }
                NSApplication.shared.applicationIconImage = NSImage(contentsOf: url)
            }
        }
        """,
        encoding="utf-8",
    )
    return notification


def test_run_directory_records_and_finalizes_immutable_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    root.mkdir()
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated, size=1254)

    prepared = prepare_icon_run(root, description="black comma icon", variants=2)
    run_dir = Path(prepared["runDir"])

    assert (run_dir / "request.json").exists()
    assert (run_dir / "imagegen-jobs.json").exists()
    assert (run_dir / "prompts" / "variant-1.md").exists()

    recorded = record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    assert Path(recorded["decodedPath"]).exists()

    summary = finalize_icon_run(run_dir, job_id="variant-1")
    with Image.open(summary["preparedSource"]) as image:
        assert image.size == (1024, 1024)
    assert summary["operation"] == "resize-down"
    assert Path(summary["qa"]["contactSheet"]).exists()
    assert Path(summary["canonicalMaster"]).exists()


def test_record_imagegen_result_rejects_run_local_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    root.mkdir()
    run_dir = Path(prepare_icon_run(root, description="icon", variants=2)["runDir"])
    local = run_dir / "decoded" / "fake.png"
    make_png(local)

    with pytest.raises(ValueError, match="outside the run directory"):
        record_imagegen_result(run_dir, job_id="variant-1", source=local)


def test_apply_requires_matching_approval_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root)
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated, color=(1, 2, 3, 255))

    run_dir = Path(prepare_icon_run(root, description="reply icon", variants=2)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    finalize_icon_run(run_dir, job_id="variant-1")
    plan = plan_apply(run_dir, platform="auto", mode="legacy-appiconset", backup=True)
    assert plan["backupDestination"] == str((run_dir / "backups").resolve())
    approval = approve_apply(run_dir, apply_plan=Path(plan["applyPlan"]), approval_note="Design and apply plan approved.")

    result = apply_approval(Path(approval["approval"]))

    assert result["changedFiles"]
    assert result["backupFiles"]
    assert all(Path(path).is_relative_to(run_dir / "backups") for path in result["backupFiles"])
    assert not (root / ".icon-generator-backups").exists()
    assert result["postApplyVerification"]["ok"] is True
    assert result["previewManifestPath"].endswith(".json")
    assert Path(result["previewManifestPath"]).exists()
    assert (root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512@2x.png").exists()

    plan_path = Path(plan["applyPlan"])
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["mode"] = "tampered"
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        apply_approval(Path(approval["approval"]))


def test_verify_applied_icon_detects_active_asset_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root)
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated, color=(1, 2, 3, 255))

    run_dir = Path(prepare_icon_run(root, description="reply icon", variants=2)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    finalize_icon_run(run_dir, job_id="variant-1")
    plan = plan_apply(run_dir, platform="auto", mode="legacy-appiconset", backup=True)
    approval = approve_apply(run_dir, apply_plan=Path(plan["applyPlan"]), approval_note="Design and apply plan approved.")
    apply_approval(Path(approval["approval"]))

    make_png(root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512@2x.png", color=(99, 99, 99, 255))
    verification = verify_applied_icon(Path(approval["approval"]))

    assert verification["ok"] is False
    assert verification["postApplyVerification"]["activeAssetComparison"]["activeMatchesApprovedSource"] is False


def test_applied_preview_paths_are_unique_for_repeated_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root)
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated, color=(1, 2, 3, 255))

    run_dir = Path(prepare_icon_run(root, description="reply icon", variants=2)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    finalize_icon_run(run_dir, job_id="variant-1")
    plan = plan_apply(run_dir, platform="auto", mode="legacy-appiconset", backup=True)
    approval = approve_apply(run_dir, apply_plan=Path(plan["applyPlan"]), approval_note="Design and apply plan approved.")

    applied = apply_approval(Path(approval["approval"]))
    first = verify_applied_icon(Path(approval["approval"]))
    second = verify_applied_icon(Path(approval["approval"]))

    paths = {applied["appliedPreviewPath"], first["appliedPreviewPath"], second["appliedPreviewPath"]}
    assert len(paths) == 3
    assert all(Path(path).parent == run_dir / "qa" for path in paths)
    assert applied["appliedPreviewSha256"] == first["appliedPreviewSha256"] == second["appliedPreviewSha256"]


def test_runtime_resource_sync_requires_inclusion_in_apply_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    notification = make_replyx_runtime_fixture(root)
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated, color=(7, 8, 9, 255))

    run_dir = Path(prepare_icon_run(root, description="reply icon", variants=2)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    finalize_icon_run(run_dir, job_id="variant-1")

    plan_without_runtime = plan_apply(run_dir, platform="auto", mode="legacy-appiconset", backup=True)
    approval_without_runtime = approve_apply(
        run_dir,
        apply_plan=Path(plan_without_runtime["applyPlan"]),
        approval_note="Apply only the app icon set.",
    )
    apply_approval(Path(approval_without_runtime["approval"]))
    with Image.open(notification) as image:
        assert image.getpixel((0, 0)) == (220, 10, 20, 255)
    assert "Runtime icon resources were detected" in "\n".join(plan_without_runtime["warnings"])

    plan_with_runtime = plan_apply(
        run_dir,
        platform="auto",
        mode="legacy-appiconset",
        backup=True,
        include_runtime_resources=["NotificationIcon.png"],
    )
    approval_with_runtime = approve_apply(
        run_dir,
        apply_plan=Path(plan_with_runtime["applyPlan"]),
        approval_note="Apply app icon set and NotificationIcon.png.",
    )
    apply_approval(Path(approval_with_runtime["approval"]))
    with Image.open(notification) as image:
        assert image.getpixel((0, 0)) == (7, 8, 9, 255)


def test_direct_generate_approved_source_is_deprecated(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    make_png(source)

    with pytest.raises(SystemExit) as exc:
        main(["generate-android", "--root", str(tmp_path), "--source", str(source), "--approved-source", "--json"])

    assert exc.value.code == 2


def test_direct_generate_apple_backup_is_disabled_and_does_not_modify_assets(tmp_path: Path) -> None:
    root = tmp_path / "project"
    make_macos_fixture(root)
    active = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512@2x.png"
    make_png(active, color=(220, 10, 20, 255))
    before = active.read_bytes()
    source = tmp_path / "master.png"
    make_png(source, color=(1, 2, 3, 255))

    with pytest.raises(SystemExit) as exc:
        main(["generate-apple", "--root", str(root), "--source", str(source), "--platform", "macos", "--backup", "--json"])

    assert exc.value.code == 2
    assert active.read_bytes() == before
    assert not (root / ".icon-generator-backups").exists()


def test_icon_generator_doctor_reports_loaded_workflow(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["icon-generator-doctor", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["version"] == "0.7.0"
    assert payload["directWritesDisabled"] is True
    assert "prepare-icon-run" in payload["requiredWorkflow"]
    assert payload["skillPath"].endswith("skills/icon-generator/SKILL.md")


def test_approve_apply_writes_stable_approval_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root)
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated)

    run_dir = Path(prepare_icon_run(root, description="reply icon", variants=2)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    finalize_icon_run(run_dir, job_id="variant-1")
    plan = plan_apply(run_dir, platform="auto", mode="legacy-appiconset", backup=True)
    approval = approve_apply(run_dir, apply_plan=Path(plan["applyPlan"]), approval_note="Approved apply.")

    assert Path(approval["approval"]).exists()
    assert Path(approval["approvalStablePath"]).exists()
    assert apply_approval(Path(approval["approvalStablePath"]))["ok"] is True


def test_current_icon_auto_platform_does_not_add_web_favicons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root)
    make_png(root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512@2x.png")
    (root / "package.json").write_text('{"scripts":{"dev":"vite"}}\n', encoding="utf-8")
    (root / "public").mkdir()
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated)

    run_dir = Path(prepare_icon_run(root, description="make current icon brighter", variants=2, from_current=True)["runDir"])
    approve_edit_brief(run_dir, approval_note="Brief approved.")
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    label_icon_variant(run_dir, job_id="variant-1", label="matches-brief", note="Only requested detail changed.")
    finalize_icon_run(run_dir, job_id="variant-1")
    plan = plan_apply(run_dir, platform="auto", mode="legacy-appiconset", backup=True)

    assert plan["selectedPlatforms"] == ["macos"]
    assert [action["kind"] for action in plan["actions"]] == ["generate-apple"]


def test_macos_26_auto_mode_selects_icon_composer_when_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root, deployment="26.0")
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated)
    monkeypatch.setattr(
        workflow,
        "icon_composer_preflight",
        lambda **_kwargs: {"ok": True, "canUseIconComposer": True, "liquidGlassReady": True, "ictoolReady": True, "selectedBackend": "ictool"},
    )

    run_dir = Path(prepare_icon_run(root, description="reply icon", variants=2)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    finalize_icon_run(run_dir, job_id="variant-1")
    plan = plan_apply(run_dir, platform="macos", mode="auto", backup=True)

    assert plan["mode"] == "icon-composer"
    assert [action["kind"] for action in plan["actions"]] == ["generate-icon-composer"]
    assert any(item["operation"] == "remove-legacy-appiconset" for item in plan["filePlan"])


def test_macos_26_auto_mode_blocks_when_icon_composer_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root, deployment="26.0")
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated)
    monkeypatch.setattr(
        workflow,
        "icon_composer_preflight",
        lambda **_kwargs: {
            "ok": False,
            "canUseIconComposer": False,
            "installRequired": True,
            "liquidGlassReady": False,
            "ictoolReady": False,
            "blockingReasons": ["Icon Composer.app or ictool was not found."],
            "fallbackOptions": [{"id": "legacy-appiconset", "mode": "legacy-appiconset"}],
        },
    )

    run_dir = Path(prepare_icon_run(root, description="reply icon", variants=2)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    finalize_icon_run(run_dir, job_id="variant-1")
    plan = plan_apply(run_dir, platform="macos", mode="auto", backup=True)

    assert plan["mode"] == "icon-composer"
    assert plan["applyBlocked"] is True
    assert plan["requiresUserDecision"] is True
    assert plan["actions"] == []
    assert plan["filePlan"] == []
    assert plan["suggestedNextCommands"]
    with pytest.raises(ValueError, match="blocked"):
        approve_apply(run_dir, apply_plan=Path(plan["applyPlan"]), approval_note="Apply blocked plan.")


def test_explicit_legacy_mode_on_macos_26_is_allowed_with_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root, deployment="26.0")
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated)
    monkeypatch.setattr(
        workflow,
        "icon_composer_preflight",
        lambda **_kwargs: {"ok": False, "canUseIconComposer": False, "liquidGlassReady": False, "ictoolReady": False},
    )

    run_dir = Path(prepare_icon_run(root, description="reply icon", variants=2)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    finalize_icon_run(run_dir, job_id="variant-1")
    plan = plan_apply(run_dir, platform="macos", mode="legacy-appiconset", backup=True)

    assert plan["mode"] == "legacy-appiconset"
    assert plan["applyBlocked"] is False
    assert [action["kind"] for action in plan["actions"]] == ["generate-apple"]
    assert any("flat PNG assets" in warning for warning in plan["warnings"])


def test_geometry_repair_job_is_deterministic_not_imagegen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    root.mkdir()
    run_dir = Path(prepare_icon_run(root, description="icon", variants=2)["runDir"])

    repair = queue_icon_repairs(run_dir, scope="geometry", note="Remove uniform padding.")

    assert repair["job"]["requiresImagegen"] is False
    assert repair["job"]["kind"] == "geometry-repair"


def test_visual_artifact_repair_requires_design_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    root.mkdir()
    run_dir = Path(prepare_icon_run(root, description="icon", variants=2)["runDir"])

    with pytest.raises(ValueError, match="pixel filters"):
        queue_icon_repairs(run_dir, scope="edge", note="remove tiny dark dots under the blue glow")


def test_prepare_from_current_marks_visual_regeneration_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root)
    appicon = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512@2x.png"
    make_png(appicon)

    run_dir = Path(prepare_icon_run(root, description="make the blue dot glow brighter", variants=2, from_current=True)["runDir"])
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    prompt = (run_dir / "prompts" / "variant-1.md").read_text(encoding="utf-8")

    assert request["visualEditPolicy"]["visualRegenerationRequired"] is True
    assert request["visualEditPolicy"]["deterministicPixelRetouchAllowed"] is False
    assert request["visualEditPolicy"]["redesignAllowed"] is False
    assert request["visualEditPolicy"]["editStrictness"] == "localized-preserve-invariants"
    assert request["editBriefApproved"] is False
    assert request["editBrief"]["editIntent"] == "localized-edit"
    assert request["editBrief"]["requestedChanges"] == ["make the blue dot glow brighter"]
    assert request["editBrief"]["requiresUserConfirmationBeforeImagegen"] is True
    status = json.loads((run_dir / "imagegen-jobs.json").read_text(encoding="utf-8"))["jobs"][0]["status"]
    assert status == "needs-edit-brief-approval"
    assert "Do not simulate threshold masks" in prompt


def test_prepare_from_current_defaults_to_one_fast_variant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root)
    appicon = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512@2x.png"
    make_png(appicon)

    payload = prepare_icon_run(root, description="move the X slightly higher", from_current=True)
    run_dir = Path(payload["runDir"])
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    jobs = json.loads((run_dir / "imagegen-jobs.json").read_text(encoding="utf-8"))["jobs"]

    assert len(jobs) == 1
    assert request["variantStrategy"] == "single-fast-edit"
    assert request["recommendedVariantOptions"][0]["variants"] == 1
    assert request["recommendedVariantOptions"][0]["recommended"] is True


def test_prepare_new_icon_defaults_to_four_variants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    root.mkdir()

    payload = prepare_icon_run(root, description="new black comma icon")
    run_dir = Path(payload["runDir"])
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    jobs = json.loads((run_dir / "imagegen-jobs.json").read_text(encoding="utf-8"))["jobs"]

    assert len(jobs) == 4
    assert request["variantStrategy"] == "choice-set"


def test_prepare_icon_run_allows_one_variant_finalize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    root.mkdir()
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated)

    run_dir = Path(prepare_icon_run(root, description="simple icon", variants=1)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)
    summary = finalize_icon_run(run_dir)

    assert summary["ok"] is True


def test_prepare_from_current_minor_shape_edit_forbids_redesign(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root)
    appicon = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512@2x.png"
    make_png(appicon)

    run_dir = Path(prepare_icon_run(root, description="сделай округлый предмет чуть более плоским", variants=2, from_current=True)["runDir"])
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    prompt = (run_dir / "prompts" / "variant-1.md").read_text(encoding="utf-8")

    assert request["visualEditPolicy"]["redesignAllowed"] is False
    assert request["editBrief"]["changeIntensity"] == "subtle"
    assert "this is not a redesign" in prompt
    assert "Preserve the current composition, style, palette, material" in prompt
    assert "Do not make unrelated objects faceted" in prompt
    assert "For a flatter rounded object, reduce curvature" in prompt


def test_current_icon_edit_requires_brief_approval_and_variant_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root)
    appicon = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512@2x.png"
    make_png(appicon)
    generated = tmp_path / "generated" / "ig_variant.png"
    make_png(generated)

    run_dir = Path(prepare_icon_run(root, description="everything is perfect except the central shape; make it slightly matte", variants=2, from_current=True)["runDir"])

    with pytest.raises(ValueError, match="edit brief must be shown"):
        record_imagegen_result(run_dir, job_id="variant-1", source=generated)

    approve_edit_brief(run_dir, approval_note="User approved the edit brief.")
    record_imagegen_result(run_dir, job_id="variant-1", source=generated)

    with pytest.raises(ValueError, match="must be labeled"):
        finalize_icon_run(run_dir, job_id="variant-1")

    label_icon_variant(run_dir, job_id="variant-1", label="violates-preserved-elements", note="The background changed.")
    with pytest.raises(ValueError, match="explicit user override"):
        finalize_icon_run(run_dir, job_id="variant-1")

    summary = finalize_icon_run(run_dir, job_id="variant-1", allow_brief_violation=True)
    assert summary["ok"] is True


def test_edit_brief_extracts_everything_perfect_except_and_preserve_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root)
    appicon = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512@2x.png"
    make_png(appicon)

    description = (
        "В этой иконке все идеально, кроме бабла. "
        "Букву X, темный фон, светящийся шарик, это все хорошо сделано."
    )
    run_dir = Path(prepare_icon_run(root, description=description, variants=2, from_current=True)["runDir"])
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    prompt = (run_dir / "prompts" / "variant-1.md").read_text(encoding="utf-8")

    assert request["editIntent"] == "localized-edit"
    assert request["editBrief"]["targetArea"] == "бабла"
    assert "everything except the requested target area" in request["preservedElements"]
    assert any("Букву X" in item for item in request["preservedElements"])
    assert any("темный фон" in item for item in request["preservedElements"])
    assert "Requested changes:" in prompt
    assert "Preserved elements:" in prompt
    assert "Forbidden changes:" in prompt


def test_whole_icon_low_poly_is_redesign_not_localized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    make_macos_fixture(root)
    appicon = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512@2x.png"
    make_png(appicon)

    run_dir = Path(prepare_icon_run(root, description="Поменяй всю иконку в стиле low poly", variants=2, from_current=True)["runDir"])
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    prompt = (run_dir / "prompts" / "variant-1.md").read_text(encoding="utf-8")

    assert request["editIntent"] == "redesign"
    assert "this is an approved redesign" in prompt
