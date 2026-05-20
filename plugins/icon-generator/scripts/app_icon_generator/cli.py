from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import __version__
from .active_icon import extract_current_master, inspect_active_icon
from .app_inspect import diagnose_macos_icon, find_running_apps, inspect_built_app
from .android import generate_android_icons
from .apple import generate_apple_icons
from .detect_project import detect_project
from .icon_composer import generate_icon_composer_bundle, icon_composer_install_guide, icon_composer_preflight, wait_icon_composer_ready
from .image_io import IconImageError
from .path_filters import assert_safe_generated_output_path
from .qa import make_contact_sheet, qa_source
from .runtime_icons import discover_runtime_icons, sync_runtime_icon
from .validate import validate_project
from .workflow import (
    apply_approval,
    approve_edit_brief,
    approve_apply,
    finalize_icon_run,
    icon_job_status,
    label_icon_variant,
    plan_apply,
    prepare_icon_run,
    queue_icon_repairs,
    record_imagegen_result,
    verify_applied_icon,
)
from .xcode_inspect import inspect_xcode_project


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        payload = args.func(args)
    except (IconImageError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")

    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_human(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="icon-generator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect = subparsers.add_parser("detect", help="Detect supported app icon targets.")
    add_root_arg(detect)
    add_json_arg(detect)
    detect.set_defaults(func=run_detect)

    active = subparsers.add_parser("active-icon", help="Inspect the active app icon target and source.")
    add_root_arg(active)
    active.add_argument("--platform", choices=["auto", "ios", "macos", "android", "web"], default="auto")
    active.add_argument("--no-xcodebuild", action="store_true")
    add_json_arg(active)
    active.set_defaults(func=run_active_icon)

    extract = subparsers.add_parser("extract-current-master", help="Extract the current active app icon master PNG.")
    add_root_arg(extract)
    extract.add_argument("--out", type=Path, required=True)
    extract.add_argument("--platform", choices=["auto", "ios", "macos", "android", "web"], default="auto")
    extract.add_argument("--allow-project-output", action="store_true")
    add_json_arg(extract)
    extract.set_defaults(func=run_extract_current_master)

    apple = subparsers.add_parser("generate-apple", help="Generate iOS and/or macOS AppIcon assets.")
    add_root_arg(apple)
    add_source_arg(apple)
    add_json_arg(apple)
    apple.add_argument("--platform", choices=["ios", "macos", "both"], required=True)
    apple.add_argument("--mode", choices=["legacy-appiconset", "icon-composer", "auto"], default="legacy-appiconset")
    apple.add_argument("--backup", action="store_true")
    apple.add_argument("--allow-crop", action="store_true")
    apple.add_argument("--approved-source", action="store_true")
    apple.add_argument("--approval", type=Path)
    apple.set_defaults(func=run_generate_apple)

    android = subparsers.add_parser("generate-android", help="Generate Android launcher icon assets.")
    add_root_arg(android)
    add_source_arg(android)
    add_json_arg(android)
    android.add_argument("--backup", action="store_true")
    android.add_argument("--allow-crop", action="store_true")
    android.add_argument("--approved-source", action="store_true")
    android.add_argument("--approval", type=Path)
    android.set_defaults(func=run_generate_android)

    validate = subparsers.add_parser("validate", help="Validate generated app icon assets.")
    add_root_arg(validate)
    add_json_arg(validate)
    validate.set_defaults(func=run_validate)

    inspect_xcode = subparsers.add_parser("inspect-xcode", help="Inspect Xcode app icon settings.")
    add_root_arg(inspect_xcode)
    add_json_arg(inspect_xcode)
    inspect_xcode.add_argument("--no-xcodebuild", action="store_true")
    inspect_xcode.set_defaults(func=run_inspect_xcode)

    qa = subparsers.add_parser("qa-source", help="Analyze and render preview QA for a source icon PNG.")
    qa.add_argument("--source", type=Path, required=True)
    qa.add_argument("--out", type=Path, required=True)
    qa.add_argument("--allow-project-output", action="store_true")
    add_json_arg(qa)
    qa.set_defaults(func=run_qa_source)

    sheet = subparsers.add_parser("make-contact-sheet", help="Create a contact sheet for multiple icon variants.")
    sheet.add_argument("--sources", type=Path, nargs="+", required=True)
    sheet.add_argument("--out", type=Path, required=True)
    sheet.add_argument("--allow-project-output", action="store_true")
    add_json_arg(sheet)
    sheet.set_defaults(func=run_make_contact_sheet)

    composer = subparsers.add_parser("generate-icon-composer", help="Create an Icon Composer .icon bundle.")
    add_root_arg(composer)
    composer.add_argument("--foreground", type=Path, required=True)
    composer.add_argument("--bg-color", required=True)
    composer.add_argument("--dark-bg-color")
    composer.add_argument("--bundle-name", default="AppIcon")
    composer.add_argument("--backup", action="store_true")
    composer.add_argument("--approved-source", action="store_true")
    add_json_arg(composer)
    composer.set_defaults(func=run_generate_icon_composer)

    sync = subparsers.add_parser("sync-runtime-icons", help="Sync additional runtime icon PNG resources after approval.")
    add_root_arg(sync)
    add_source_arg(sync)
    sync.add_argument("--resource", required=True)
    sync.add_argument("--size", type=int, required=True)
    sync.add_argument("--backup", action="store_true")
    sync.add_argument("--approved-source", action="store_true")
    sync.add_argument("--approval", type=Path)
    add_json_arg(sync)
    sync.set_defaults(func=run_sync_runtime_icons)

    prepare_run = subparsers.add_parser("prepare-icon-run", help="Create an immutable icon design run with imagegen jobs.")
    add_root_arg(prepare_run)
    prepare_run.add_argument("--description", required=True)
    prepare_run.add_argument("--reference", type=Path, action="append", default=[])
    prepare_run.add_argument("--variants", type=int)
    prepare_run.add_argument("--from-current", action="store_true")
    prepare_run.add_argument("--edit-intent", choices=["localized-edit", "redesign", "technical-repair"])
    prepare_run.add_argument("--requested-change", action="append", default=[])
    prepare_run.add_argument("--preserve-element", action="append", default=[])
    prepare_run.add_argument("--change-intensity", choices=["tiny", "subtle", "medium", "strong"])
    prepare_run.add_argument("--target-area")
    prepare_run.add_argument("--style-constraint", action="append", default=[])
    prepare_run.add_argument("--forbidden-change", action="append", default=[])
    add_json_arg(prepare_run)
    prepare_run.set_defaults(func=run_prepare_icon_run)

    approve_brief = subparsers.add_parser("approve-edit-brief", help="Approve a current-icon edit brief before image generation.")
    approve_brief.add_argument("--run-dir", type=Path, required=True)
    approve_brief.add_argument("--approval-note", required=True)
    add_json_arg(approve_brief)
    approve_brief.set_defaults(func=run_approve_edit_brief)

    job_status = subparsers.add_parser("icon-job-status", help="Show imagegen job status for an icon run.")
    job_status.add_argument("--run-dir", type=Path, required=True)
    add_json_arg(job_status)
    job_status.set_defaults(func=run_icon_job_status)

    record = subparsers.add_parser("record-imagegen-result", help="Record a selected original imagegen output for a run job.")
    record.add_argument("--run-dir", type=Path, required=True)
    record.add_argument("--job-id", required=True)
    record.add_argument("--source", type=Path, required=True)
    add_json_arg(record)
    record.set_defaults(func=run_record_imagegen_result)

    label_variant = subparsers.add_parser("label-icon-variant", help="Label a completed current-icon variant against the edit brief.")
    label_variant.add_argument("--run-dir", type=Path, required=True)
    label_variant.add_argument("--job-id", required=True)
    label_variant.add_argument("--label", choices=["matches-brief", "minor-drift", "violates-preserved-elements"], required=True)
    label_variant.add_argument("--note", required=True)
    add_json_arg(label_variant)
    label_variant.set_defaults(func=run_label_icon_variant)

    finalize = subparsers.add_parser("finalize-icon-run", help="Normalize the selected master and create QA artifacts.")
    finalize.add_argument("--run-dir", type=Path, required=True)
    finalize.add_argument("--job-id")
    finalize.add_argument("--allow-crop", action="store_true")
    finalize.add_argument("--allow-upscale", action="store_true")
    finalize.add_argument("--allow-brief-violation", action="store_true")
    add_json_arg(finalize)
    finalize.set_defaults(func=run_finalize_icon_run)

    repair = subparsers.add_parser("queue-icon-repairs", help="Queue the smallest repair scope for an icon run.")
    repair.add_argument("--run-dir", type=Path, required=True)
    repair.add_argument("--scope", choices=["design", "geometry", "edge"], required=True)
    repair.add_argument("--note", required=True)
    add_json_arg(repair)
    repair.set_defaults(func=run_queue_icon_repairs)

    plan = subparsers.add_parser("plan-apply", help="Create a read-only apply plan for a finalized icon run.")
    plan.add_argument("--run-dir", type=Path, required=True)
    plan.add_argument("--platform", choices=["auto", "ios", "macos", "android", "web", "both"], default="auto")
    plan.add_argument("--mode", choices=["auto", "legacy-appiconset", "icon-composer"], default="auto")
    plan.add_argument("--include-runtime-resource", action="append", default=[])
    plan.add_argument("--no-backup", action="store_true")
    add_json_arg(plan)
    plan.set_defaults(func=run_plan_apply)

    approve = subparsers.add_parser("approve-apply", help="Create an approval manifest for an apply plan.")
    approve.add_argument("--run-dir", type=Path, required=True)
    approve.add_argument("--apply-plan", type=Path, required=True)
    approve.add_argument("--approval-note", required=True)
    add_json_arg(approve)
    approve.set_defaults(func=run_approve_apply)

    apply_parser = subparsers.add_parser("apply", help="Apply icon assets using a strict approval manifest.")
    apply_parser.add_argument("--approval", type=Path, required=True)
    add_json_arg(apply_parser)
    apply_parser.set_defaults(func=run_apply)

    verify_apply = subparsers.add_parser("verify-applied-icon", help="Verify active icon output after an approved apply.")
    verify_apply.add_argument("--approval", type=Path, required=True)
    add_json_arg(verify_apply)
    verify_apply.set_defaults(func=run_verify_applied_icon)

    built_app = subparsers.add_parser("inspect-built-app", help="Inspect generated .app icon resources.")
    built_app.add_argument("--app", type=Path, required=True)
    add_json_arg(built_app)
    built_app.set_defaults(func=run_inspect_built_app)

    running = subparsers.add_parser("find-running-apps", help="Find running .app copies for a bundle id.")
    running.add_argument("--bundle-id", required=True)
    add_json_arg(running)
    running.set_defaults(func=run_find_running_apps)

    diagnose = subparsers.add_parser("diagnose-macos-icon", help="Diagnose macOS app icon build/runtime state.")
    add_root_arg(diagnose)
    diagnose.add_argument("--app", type=Path)
    add_json_arg(diagnose)
    diagnose.set_defaults(func=run_diagnose_macos_icon)

    doctor = subparsers.add_parser("icon-composer-doctor", help="Check Icon Composer MCP prerequisites.")
    add_json_arg(doctor)
    doctor.set_defaults(func=run_icon_composer_doctor)

    install_guide = subparsers.add_parser("icon-composer-install-guide", help="Show guided manual Icon Composer installation steps.")
    add_json_arg(install_guide)
    install_guide.set_defaults(func=run_icon_composer_install_guide)

    wait_ready = subparsers.add_parser("wait-icon-composer-ready", help="Poll until Icon Composer and ictool are ready.")
    wait_ready.add_argument("--timeout", type=float, default=300)
    wait_ready.add_argument("--interval", type=float, default=5)
    add_json_arg(wait_ready)
    wait_ready.set_defaults(func=run_wait_icon_composer_ready)

    workflow_doctor = subparsers.add_parser("icon-generator-doctor", help="Inspect loaded Icon Generator plugin workflow state.")
    add_json_arg(workflow_doctor)
    workflow_doctor.set_defaults(func=run_icon_generator_doctor)

    return parser


def add_root_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, required=True)


def add_source_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", type=Path, required=True)


def add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", dest="json_output", action="store_true")
    parser.set_defaults(json_output=False)


def run_detect(args: argparse.Namespace) -> dict[str, Any]:
    return detect_project(args.root).to_dict()


def run_active_icon(args: argparse.Namespace) -> dict[str, Any]:
    return inspect_active_icon(args.root, platform=args.platform, run_xcodebuild=not args.no_xcodebuild)


def run_extract_current_master(args: argparse.Namespace) -> dict[str, Any]:
    return extract_current_master(args.root, args.out, platform=args.platform, allow_project_output=args.allow_project_output)


def run_generate_apple(args: argparse.Namespace) -> dict[str, Any]:
    require_workflow_apply(args)
    if args.mode == "icon-composer":
        raise ValueError("use generate-icon-composer for Icon Composer .icon output")

    result = generate_apple_icons(
        args.root,
        args.source,
        platform=args.platform,
        backup=args.backup,
        allow_crop=args.allow_crop,
    )
    runtime_report = discover_runtime_icons(args.root)
    warnings = runtime_generation_warnings(args.root, args.source, runtime_report)
    return {
        "ok": True,
        "platform": args.platform,
        "mode": args.mode,
        "changedFiles": result.changed_files,
        "backupFiles": result.backup_files,
        "removedFiles": result.removed_files,
        "diagnostic": legacy_diagnostic(args.platform, args.mode),
        "warnings": warnings,
        "runtimeIconOverrides": [item.to_dict() for item in runtime_report.runtime_icon_overrides],
        "additionalIconResources": [item.to_dict() for item in runtime_report.additional_icon_resources],
    }


def run_generate_android(args: argparse.Namespace) -> dict[str, Any]:
    require_workflow_apply(args)
    result = generate_android_icons(
        args.root,
        args.source,
        backup=args.backup,
        allow_crop=args.allow_crop,
    )
    return {
        "ok": True,
        "platform": "android",
        "changedFiles": result.changed_files,
        "backupFiles": result.backup_files,
        "removedFiles": result.removed_files,
    }


def run_validate(args: argparse.Namespace) -> dict[str, Any]:
    return validate_project(args.root)


def run_inspect_xcode(args: argparse.Namespace) -> dict[str, Any]:
    targets = inspect_xcode_project(args.root, run_xcodebuild=not args.no_xcodebuild)
    return {
        "root": str(args.root.resolve()),
        "targets": [target.to_dict() for target in targets],
    }


def run_qa_source(args: argparse.Namespace) -> dict[str, Any]:
    assert_safe_generated_output_path(args.out, allow_project_output=args.allow_project_output, label="qa-source output")
    return qa_source(args.source, args.out)


def run_make_contact_sheet(args: argparse.Namespace) -> dict[str, Any]:
    assert_safe_generated_output_path(args.out, allow_project_output=args.allow_project_output, label="contact sheet output")
    output = make_contact_sheet(args.sources, args.out)
    return {
        "ok": True,
        "sources": [str(source) for source in args.sources],
        "contactSheet": output,
    }


def run_generate_icon_composer(args: argparse.Namespace) -> dict[str, Any]:
    require_workflow_apply(args)
    return generate_icon_composer_bundle(
        args.root,
        args.foreground,
        bg_color=args.bg_color,
        dark_bg_color=args.dark_bg_color,
        bundle_name=args.bundle_name,
        backup=args.backup,
    )


def run_sync_runtime_icons(args: argparse.Namespace) -> dict[str, Any]:
    require_workflow_apply(args)
    result = sync_runtime_icon(
        args.root,
        args.source,
        resource=args.resource,
        size=args.size,
        backup=args.backup,
    )
    return {
        "ok": True,
        "mode": "sync-runtime-icons",
        "resource": args.resource,
        "size": args.size,
        "changedFiles": result.changed_files,
        "backupFiles": result.backup_files,
        "removedFiles": result.removed_files,
    }


def run_inspect_built_app(args: argparse.Namespace) -> dict[str, Any]:
    return inspect_built_app(args.app)


def run_find_running_apps(args: argparse.Namespace) -> dict[str, Any]:
    return find_running_apps(args.bundle_id)


def run_diagnose_macos_icon(args: argparse.Namespace) -> dict[str, Any]:
    return diagnose_macos_icon(args.root, app=args.app)


def run_icon_composer_doctor(args: argparse.Namespace) -> dict[str, Any]:
    return icon_composer_preflight()


def run_icon_composer_install_guide(args: argparse.Namespace) -> dict[str, Any]:
    return icon_composer_install_guide()


def run_wait_icon_composer_ready(args: argparse.Namespace) -> dict[str, Any]:
    return wait_icon_composer_ready(timeout=args.timeout, interval=args.interval)


def run_icon_generator_doctor(args: argparse.Namespace) -> dict[str, Any]:
    plugin_root = Path(__file__).resolve().parents[2]
    skill_path = plugin_root / "skills" / "icon-generator" / "SKILL.md"
    return {
        "ok": True,
        "version": __version__,
        "pluginRoot": str(plugin_root),
        "skillPath": str(skill_path),
        "directWritesDisabled": True,
        "requiredWorkflow": [
            "prepare-icon-run",
            "approve-edit-brief",
            "record-imagegen-result",
            "label-icon-variant",
            "finalize-icon-run",
            "plan-apply",
            "approve-apply",
            "apply",
        ],
        "localMarketplace": local_marketplace_report(),
    }


def run_prepare_icon_run(args: argparse.Namespace) -> dict[str, Any]:
    return prepare_icon_run(
        args.root,
        description=args.description,
        references=args.reference,
        variants=args.variants,
        from_current=args.from_current,
        edit_intent=args.edit_intent,
        requested_changes=args.requested_change,
        preserved_elements=args.preserve_element,
        change_intensity=args.change_intensity,
        target_area=args.target_area,
        style_constraints=args.style_constraint,
        forbidden_changes=args.forbidden_change,
    )


def run_approve_edit_brief(args: argparse.Namespace) -> dict[str, Any]:
    return approve_edit_brief(args.run_dir, approval_note=args.approval_note)


def run_icon_job_status(args: argparse.Namespace) -> dict[str, Any]:
    return icon_job_status(args.run_dir)


def run_record_imagegen_result(args: argparse.Namespace) -> dict[str, Any]:
    return record_imagegen_result(args.run_dir, job_id=args.job_id, source=args.source)


def run_label_icon_variant(args: argparse.Namespace) -> dict[str, Any]:
    return label_icon_variant(args.run_dir, job_id=args.job_id, label=args.label, note=args.note)


def run_finalize_icon_run(args: argparse.Namespace) -> dict[str, Any]:
    return finalize_icon_run(
        args.run_dir,
        job_id=args.job_id,
        allow_crop=args.allow_crop,
        allow_upscale=args.allow_upscale,
        allow_brief_violation=args.allow_brief_violation,
    )


def run_queue_icon_repairs(args: argparse.Namespace) -> dict[str, Any]:
    return queue_icon_repairs(args.run_dir, scope=args.scope, note=args.note)


def run_plan_apply(args: argparse.Namespace) -> dict[str, Any]:
    return plan_apply(
        args.run_dir,
        platform=args.platform,
        mode=args.mode,
        backup=not args.no_backup,
        include_runtime_resources=args.include_runtime_resource,
    )


def run_approve_apply(args: argparse.Namespace) -> dict[str, Any]:
    return approve_apply(args.run_dir, apply_plan=args.apply_plan, approval_note=args.approval_note)


def run_apply(args: argparse.Namespace) -> dict[str, Any]:
    return apply_approval(args.approval)


def run_verify_applied_icon(args: argparse.Namespace) -> dict[str, Any]:
    return verify_applied_icon(args.approval)


def require_workflow_apply(args: argparse.Namespace) -> None:
    workflow = "prepare-icon-run -> record-imagegen-result -> finalize-icon-run -> plan-apply -> approve-apply -> apply"
    if getattr(args, "approved_source", False):
        raise ValueError(
            "--approved-source is deprecated and is not accepted for direct writes. "
            f"Use {workflow}."
        )
    raise ValueError(
        f"direct write commands are disabled in the run workflow. Use {workflow}."
    )


def legacy_diagnostic(platform: str, mode: str) -> str | None:
    if platform in {"macos", "both"} and mode in {"legacy-appiconset", "auto"}:
        return (
            "On macOS 26, legacy PNG AppIcon.appiconset output may render inside a system frame. "
            "Use Icon Composer mode through the run workflow for Liquid Glass .icon output."
        )
    return None


def runtime_generation_warnings(root: Path, source: Path, runtime_report) -> list[str]:
    warnings: list[str] = []
    if not runtime_report.runtime_icon_overrides and not runtime_report.additional_icon_resources:
        return warnings

    warnings.append(
        "Runtime icon override or additional icon resources were detected; generated AppIcon assets may not update the visible app icon."
    )
    for resource in runtime_report.additional_icon_resources:
        warnings.append(
            "Ask before syncing runtime resource through the run workflow: "
            f"add --include-runtime-resource {resource.name} when creating the approved apply plan."
        )
    return warnings


def local_marketplace_report() -> dict[str, Any]:
    home = Path.home()
    cache_root = home / ".codex" / "plugins" / "cache" / "local-dev" / "icon-generator-plugin"
    versions: list[dict[str, Any]] = []
    if cache_root.exists():
        for version_dir in sorted(path for path in cache_root.iterdir() if path.is_dir()):
            manifest = version_dir / ".codex-plugin" / "plugin.json"
            version = version_dir.name
            if manifest.exists():
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                    version = str(payload.get("version", version))
                except json.JSONDecodeError:
                    pass
            versions.append({"path": str(version_dir), "version": version})
    marketplace_plugin = home / ".codex" / "local-marketplaces" / "icon-generator" / "plugins" / "icon-generator-plugin"
    return {
        "cacheRoot": str(cache_root),
        "cachedVersions": versions,
        "marketplacePluginPath": str(marketplace_plugin),
        "marketplacePluginResolvesTo": str(marketplace_plugin.resolve()) if marketplace_plugin.exists() else None,
    }


def print_human(payload: dict[str, Any]) -> None:
    if "platforms" in payload:
        platforms = ", ".join(payload["platforms"]) or "none"
        print(f"Detected platforms: {platforms}")
        for target in payload["targets"]:
            print(f"- {target['platform']}: {target['root']}")
        return

    if "changedFiles" in payload:
        target = payload.get("platform") or payload.get("mode") or "output"
        print(f"Generated {len(payload['changedFiles'])} files for {target}.")
        if payload["backupFiles"]:
            print(f"Backed up {len(payload['backupFiles'])} existing files.")
        if payload.get("removedFiles"):
            print(f"Removed {len(payload['removedFiles'])} incompatible files.")
        if payload.get("diagnostic"):
            print(f"diagnostic: {payload['diagnostic']}")
        for warning in payload.get("warnings", []):
            print(f"warning: {warning}")
        return

    if "ok" in payload:
        print("Validation passed." if payload["ok"] else "Validation failed.")
        for error in payload.get("errors", []):
            print(f"error: {error}")
        for warning in payload.get("warnings", []):
            print(f"warning: {warning}")


if __name__ == "__main__":
    raise SystemExit(main())
