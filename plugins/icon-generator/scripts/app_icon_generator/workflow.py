from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Iterable

from PIL import Image, ImageChops

from .active_icon import extract_current_master, inspect_active_icon
from .android import ADAPTIVE_FOREGROUND_SIZE, LEGACY_LAUNCHER_SPECS, find_res_dir, generate_android_icons
from .apple import IOS_ICON_SPECS, MACOS_ICON_SPECS, find_appiconset, generate_apple_icons
from .detect_project import detect_project
from .icon_composer import (
    find_icon_composer_bundle_path,
    find_legacy_appiconset,
    icon_composer_foreground_path,
    icon_composer_preflight,
    inspect_icon_composer_project_state,
    generate_icon_composer_bundle,
)
from .image_io import IconImageError, WriteResult, center_crop_square, infer_edge_color, load_master_icon, resized_png
from .path_filters import is_ignored_scan_path
from .qa import make_contact_sheet, qa_source
from .runtime_icons import discover_runtime_icons, sync_runtime_icon
from .web import generate_web_favicons, web_file_plan


RUN_SCHEMA_VERSION = 1
APPROVAL_SCHEMA_VERSION = 1
DEFAULT_VARIANTS = 4
CURRENT_EDIT_DEFAULT_VARIANTS = 1
MASTER_SIZE = 1024
BRIEF_QA_LABELS = {"matches-brief", "minor-drift", "violates-preserved-elements"}
CHANGE_INTENSITIES = {"tiny", "subtle", "medium", "strong"}
EDIT_INTENTS = {"localized-edit", "redesign", "technical-repair"}


@dataclass(frozen=True)
class PreparedSource:
    path: Path
    sha256: str
    operation: str
    input_size: tuple[int, int]
    output_size: tuple[int, int]


def prepare_icon_run(
    root: Path,
    *,
    description: str,
    references: list[Path] | None = None,
    variants: int | None = None,
    from_current: bool = False,
    edit_intent: str | None = None,
    requested_changes: list[str] | None = None,
    preserved_elements: list[str] | None = None,
    change_intensity: str | None = None,
    target_area: str | None = None,
    style_constraints: list[str] | None = None,
    forbidden_changes: list[str] | None = None,
) -> dict[str, Any]:
    if not description.strip():
        raise ValueError("description is required")
    resolved_variants = variants if variants is not None else current_edit_default_variants(from_current=from_current)
    if resolved_variants < 1 or resolved_variants > 4:
        raise ValueError("variants must be between 1 and 4")

    root = root.resolve()
    run_dir = new_run_dir(root, description)
    for name in ("prompts", "references", "decoded", "prepared", "qa", "approvals"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)

    reference_payloads = copy_references(run_dir, references or [])
    active_report = None
    current_master = None
    edit_brief = build_edit_brief(
        description,
        from_current=from_current,
        edit_intent=edit_intent,
        requested_changes=requested_changes or [],
        preserved_elements=preserved_elements or [],
        change_intensity=change_intensity,
        target_area=target_area,
        style_constraints=style_constraints or [],
        forbidden_changes=forbidden_changes or [],
    )
    if from_current:
        current_out = run_dir / "references" / "current-active-master.png"
        current_master = extract_current_master(root, current_out, platform="auto")
        active_report = current_master["activeIcon"]
        reference_payloads.insert(
            0,
            {
                "path": current_master["out"],
                "role": "edit target: current active app icon master",
                "sourcePath": current_master["sourcePath"],
                "sha256": current_master["sha256"],
            },
        )
    request = {
        "schemaVersion": RUN_SCHEMA_VERSION,
        "kind": "icon-generator-run",
        "root": str(root),
        "description": description,
        "createdAt": utc_now(),
        "runDir": str(run_dir),
        "references": reference_payloads,
        "workflow": "edit-current-active-icon" if from_current else "imagegen-owned-visuals-deterministic-export",
        "visualEditPolicy": visual_edit_policy(description, from_current=from_current),
        "editIntent": edit_brief["editIntent"],
        "requestedChanges": edit_brief["requestedChanges"],
        "preservedElements": edit_brief["preservedElements"],
        "changeIntensity": edit_brief["changeIntensity"],
        "styleConstraints": edit_brief["styleConstraints"],
        "forbiddenChanges": edit_brief["forbiddenChanges"],
        "editBrief": edit_brief,
        "editBriefApproved": False if from_current else None,
        "activeIconReport": active_report,
        "sourceOptions": active_report.get("sourceOptions", []) if active_report else [],
        "sourceSelection": active_report.get("sourceSelection") if active_report else None,
        "currentMasterSha256": current_master["sha256"] if current_master else None,
        "currentMasterSourcePath": current_master["sourcePath"] if current_master else None,
        "variantStrategy": variant_strategy(resolved_variants),
        "recommendedVariantOptions": recommended_variant_options(from_current=from_current),
    }
    write_json(run_dir / "request.json", request)

    jobs: list[dict[str, Any]] = []
    initial_status = "needs-edit-brief-approval" if from_current else "ready"
    for index in range(1, resolved_variants + 1):
        job_id = f"variant-{index}"
        prompt_path = run_dir / "prompts" / f"{job_id}.md"
        prompt_path.write_text(design_prompt(description, index, reference_payloads, edit_current=from_current, edit_brief=edit_brief), encoding="utf-8")
        jobs.append(
            {
                "id": job_id,
                "kind": "edit-current-variant" if from_current else "design-variant",
                "status": initial_status,
                "promptPath": str(prompt_path),
                "inputImages": reference_payloads,
                "outputPath": None,
                "sourcePath": None,
                "sourceSha256": None,
                "recordedAt": None,
                "requiresImagegen": True,
                "briefQaLabel": None,
                "briefQaNote": None,
            }
        )

    write_json(
        run_dir / "imagegen-jobs.json",
        {
            "schemaVersion": RUN_SCHEMA_VERSION,
            "runDir": str(run_dir),
            "jobs": jobs,
        },
    )
    return {
        "ok": True,
        "runDir": str(run_dir),
        "request": request,
        "jobs": jobs,
    }


def current_edit_default_variants(*, from_current: bool) -> int:
    return CURRENT_EDIT_DEFAULT_VARIANTS if from_current else DEFAULT_VARIANTS


def variant_strategy(variants: int) -> str:
    return "single-fast-edit" if variants == 1 else "choice-set"


def recommended_variant_options(*, from_current: bool) -> list[dict[str, Any]]:
    if from_current:
        return [
            {"variants": 1, "label": "one quick variant", "recommended": True, "reason": "Best default for small localized edits."},
            {"variants": 4, "label": "four variants for choice", "recommended": False, "reason": "Useful when the user wants broader visual exploration."},
        ]
    return [
        {"variants": 4, "label": "four design variants", "recommended": True, "reason": "Best default for a new icon design."},
        {"variants": 1, "label": "one quick variant", "recommended": False, "reason": "Use when the user explicitly wants speed over breadth."},
    ]


def icon_job_status(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    jobs_payload = read_json(run_dir / "imagegen-jobs.json")
    request = read_json(run_dir / "request.json")
    jobs = jobs_payload.get("jobs", [])
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job["status"]] = counts.get(job["status"], 0) + 1
    return {
        "ok": True,
        "runDir": str(run_dir),
        "counts": counts,
        "editBrief": request.get("editBrief"),
        "editBriefApproved": request.get("editBriefApproved"),
        "readyJobs": [job for job in jobs if job.get("status") == "ready"],
        "completedJobs": [job for job in jobs if job.get("status") == "completed"],
    }


def approve_edit_brief(run_dir: Path, *, approval_note: str) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    request_path = run_dir / "request.json"
    request = read_json(request_path)
    if not request.get("editBrief"):
        raise ValueError("run does not have an edit brief")
    jobs_path = run_dir / "imagegen-jobs.json"
    jobs_payload = read_json(jobs_path)
    changed_jobs: list[str] = []
    for job in jobs_payload.get("jobs", []):
        if job.get("status") == "needs-edit-brief-approval":
            job["status"] = "ready"
            changed_jobs.append(str(job.get("id")))
    request["editBriefApproved"] = True
    request["editBriefApprovedAt"] = utc_now()
    request["editBriefApprovalNote"] = approval_note
    approval = {
        "schemaVersion": RUN_SCHEMA_VERSION,
        "kind": "icon-generator-edit-brief-approval",
        "createdAt": request["editBriefApprovedAt"],
        "runDir": str(run_dir),
        "approvalNote": approval_note,
        "editBrief": request["editBrief"],
        "changedJobs": changed_jobs,
    }
    approval_path = run_dir / "approvals" / "edit-brief.json"
    approval_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(approval_path, approval)
    write_json(request_path, request)
    write_json(jobs_path, jobs_payload)
    return {"ok": True, "runDir": str(run_dir), "approval": str(approval_path), **approval}


def label_icon_variant(run_dir: Path, *, job_id: str, label: str, note: str) -> dict[str, Any]:
    if label not in BRIEF_QA_LABELS:
        raise ValueError(f"label must be one of: {', '.join(sorted(BRIEF_QA_LABELS))}")
    run_dir = run_dir.resolve()
    jobs_path = run_dir / "imagegen-jobs.json"
    jobs_payload = read_json(jobs_path)
    job = next((item for item in jobs_payload.get("jobs", []) if item.get("id") == job_id), None)
    if not job:
        raise ValueError(f"unknown job id: {job_id}")
    if job.get("status") != "completed":
        raise ValueError(f"variant must be completed before labeling: {job_id}")
    job["briefQaLabel"] = label
    job["briefQaNote"] = note
    job["briefQaLabeledAt"] = utc_now()
    write_json(jobs_path, jobs_payload)
    return {"ok": True, "runDir": str(run_dir), "job": job}


def record_imagegen_result(run_dir: Path, *, job_id: str, source: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    source = source.resolve()
    if is_relative_to(source, run_dir):
        raise ValueError("record-imagegen-result requires an original imagegen output outside the run directory")
    if is_processed_run_artifact(source):
        raise ValueError("record-imagegen-result requires an original imagegen output, not a processed run artifact")
    if source.suffix.lower() != ".png":
        raise ValueError("record-imagegen-result source must be a PNG")
    metadata = image_metadata(source)

    jobs_path = run_dir / "imagegen-jobs.json"
    jobs_payload = read_json(jobs_path)
    jobs = jobs_payload.get("jobs", [])
    target = next((job for job in jobs if job.get("id") == job_id), None)
    if not target:
        raise ValueError(f"unknown job id: {job_id}")
    if target.get("status") == "completed":
        raise ValueError(f"job is already completed: {job_id}")
    if target.get("status") == "needs-edit-brief-approval":
        raise ValueError("edit brief must be shown to the user and approved with approve-edit-brief before recording imagegen output")
    if target.get("status") != "ready":
        raise ValueError(f"job is not ready for imagegen output: {job_id}")

    decoded = run_dir / "decoded" / f"{job_id}.png"
    decoded.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, decoded)
    digest = sha256_file(decoded)
    target.update(
        {
            "status": "completed",
            "sourcePath": str(source),
            "outputPath": str(decoded),
            "sourceSha256": digest,
            "metadata": metadata,
            "recordedAt": utc_now(),
            "briefQaLabel": "unchecked" if target.get("kind") == "edit-current-variant" else target.get("briefQaLabel"),
        }
    )
    write_json(jobs_path, jobs_payload)
    return {
        "ok": True,
        "runDir": str(run_dir),
        "job": target,
        "decodedPath": str(decoded),
        "sha256": digest,
    }


def finalize_icon_run(
    run_dir: Path,
    *,
    job_id: str | None = None,
    allow_crop: bool = False,
    allow_upscale: bool = False,
    allow_brief_violation: bool = False,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    selected = select_completed_job(run_dir, job_id)
    request_path = run_dir / "request.json"
    request = read_json(request_path)
    validate_selected_variant_against_edit_brief(request, selected, allow_brief_violation=allow_brief_violation)
    output = Path(selected["outputPath"])
    prepared = normalize_master_source(output, run_dir / "prepared", allow_crop=allow_crop, allow_upscale=allow_upscale)

    canonical = run_dir / "references" / "canonical-master.png"
    shutil.copy2(prepared.path, canonical)
    qa_payload = qa_source(prepared.path, run_dir / "qa")

    completed_sources = [Path(job["outputPath"]) for job in read_json(run_dir / "imagegen-jobs.json").get("jobs", []) if job.get("status") == "completed"]
    contact_sheet = None
    if completed_sources:
        contact_sheet = make_contact_sheet(completed_sources, run_dir / "qa" / "design-contact-sheet.png")

    request.update(
        {
            "selectedJobId": selected["id"],
            "selectedBriefQaLabel": selected.get("briefQaLabel"),
            "selectedBriefQaNote": selected.get("briefQaNote"),
            "canonicalMaster": str(canonical),
            "preparedSource": str(prepared.path),
            "preparedSourceSha256": prepared.sha256,
            "finalizedAt": utc_now(),
        }
    )
    write_json(request_path, request)

    summary = {
        "ok": True,
        "runDir": str(run_dir),
        "selectedJobId": selected["id"],
        "canonicalMaster": str(canonical),
        "preparedSource": str(prepared.path),
        "sourceSha256": prepared.sha256,
        "operation": prepared.operation,
        "inputSize": {"width": prepared.input_size[0], "height": prepared.input_size[1]},
        "outputSize": {"width": prepared.output_size[0], "height": prepared.output_size[1]},
        "qa": qa_payload,
        "designContactSheet": contact_sheet,
    }
    write_json(run_dir / "qa" / "run-summary.json", summary)
    return summary


def validate_selected_variant_against_edit_brief(
    request: dict[str, Any],
    selected: dict[str, Any],
    *,
    allow_brief_violation: bool,
) -> None:
    if request.get("workflow") != "edit-current-active-icon":
        return
    if not request.get("editBriefApproved"):
        raise ValueError("edit brief must be approved before finalizing a current-icon edit")
    label = selected.get("briefQaLabel")
    if label not in BRIEF_QA_LABELS:
        raise ValueError("variant must be labeled as matches-brief, minor-drift, or violates-preserved-elements before finalize-icon-run")
    if label == "violates-preserved-elements" and not allow_brief_violation:
        raise ValueError("selected variant violates preserved elements; pass --allow-brief-violation only after explicit user override")


def queue_icon_repairs(run_dir: Path, *, scope: str, note: str) -> dict[str, Any]:
    if scope not in {"design", "geometry", "edge"}:
        raise ValueError("scope must be design, geometry, or edge")
    if not note.strip():
        raise ValueError("repair note is required")
    if scope in {"geometry", "edge"} and requires_visual_regeneration(note):
        raise ValueError(
            "glow, shadow, color, background, and visual artifact repairs must use --scope design; "
            "do not use deterministic pixel filters for this kind of edit"
        )

    run_dir = run_dir.resolve()
    jobs_path = run_dir / "imagegen-jobs.json"
    payload = read_json(jobs_path)
    jobs = payload.get("jobs", [])
    repair_number = 1 + len([job for job in jobs if str(job.get("id", "")).startswith("repair-")])
    job_id = f"repair-{scope}-{repair_number}"
    prompt_path = run_dir / "prompts" / f"{job_id}.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)

    request = read_json(run_dir / "request.json")
    canonical = request.get("canonicalMaster")
    requires_imagegen = scope == "design"
    prompt_path.write_text(repair_prompt(scope, note, canonical), encoding="utf-8")
    job = {
        "id": job_id,
        "kind": f"{scope}-repair",
        "status": "ready",
        "promptPath": str(prompt_path),
        "inputImages": [{"path": canonical, "role": "canonical master"}] if canonical else [],
        "outputPath": None,
        "sourcePath": None,
        "sourceSha256": None,
        "recordedAt": None,
        "requiresImagegen": requires_imagegen,
        "note": note,
    }
    jobs.append(job)
    write_json(jobs_path, payload)
    return {"ok": True, "runDir": str(run_dir), "job": job}


def plan_apply(
    run_dir: Path,
    *,
    platform: str,
    mode: str,
    backup: bool,
    include_runtime_resources: list[str] | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    request = read_json(run_dir / "request.json")
    root = Path(str(request["root"])).resolve()
    source = Path(str(request.get("preparedSource", "")))
    if not source.exists():
        raise ValueError("run is not finalized; run finalize-icon-run before plan-apply")
    source_sha = sha256_file(source)
    if source_sha != request.get("preparedSourceSha256"):
        raise ValueError("prepared source digest does not match request manifest")

    detection = detect_project(root)
    active_report = inspect_active_icon(root, platform="auto", run_xcodebuild=False)
    runtime_report = discover_runtime_icons(root)
    include_runtime_resources = include_runtime_resources or []

    actions: list[dict[str, Any]] = []
    file_plan: list[dict[str, Any]] = []
    warnings: list[str] = []
    selected_platforms = select_platforms_for_plan(platform, detection.platforms, request, active_report)
    mode_decision = select_apply_mode(mode, selected_platforms, active_report, warnings)
    selected_mode = str(mode_decision["mode"])
    apply_blocked = bool(mode_decision.get("applyBlocked"))
    requires_user_decision = bool(mode_decision.get("requiresUserDecision"))
    if apply_blocked:
        warnings.extend(str(item) for item in mode_decision.get("warnings", []))

    if not apply_blocked and any(item in selected_platforms for item in ("ios", "macos")):
        apple_platform = apple_platform_arg(selected_platforms)
        if selected_mode == "icon-composer":
            if "macos" not in selected_platforms:
                raise ValueError("Icon Composer mode is only supported for macOS targets in this version")
            actions.append({"kind": "generate-icon-composer", "platform": "macos", "bundleName": "AppIcon"})
            file_plan.extend(icon_composer_file_plan(root, bundle_name="AppIcon"))
        else:
            actions.append({"kind": "generate-apple", "platform": apple_platform, "mode": selected_mode})
            file_plan.extend(apple_file_plan(root, apple_platform))
            if "macos" in selected_platforms:
                warnings.append(
                    "On macOS 26, legacy PNG AppIcon.appiconset output may render inside a system frame. Use Icon Composer .icon for Liquid Glass output."
                )

    if not apply_blocked and "android" in selected_platforms:
        actions.append({"kind": "generate-android", "platform": "android"})
        file_plan.extend(android_file_plan(root))

    if not apply_blocked and "web" in selected_platforms:
        actions.append({"kind": "generate-favicon", "platform": "web"})
        file_plan.extend(web_file_plan(root))
        warnings.append("Web favicon generation writes favicon assets and site.webmanifest only; add the returned HTML snippet to the site template manually if needed.")

    additional_names = {resource.name for resource in runtime_report.additional_icon_resources}
    if not apply_blocked:
        for resource in include_runtime_resources:
            if resource not in additional_names:
                raise ValueError(f"runtime resource is not detected in this project: {resource}")
            actions.append({"kind": "sync-runtime-icon", "resource": resource, "size": 512})
            file_plan.append({"operation": "update", "path": runtime_resource_path(runtime_report.to_dict(), resource)})

    if runtime_report.additional_icon_resources and not include_runtime_resources:
        warnings.append("Runtime icon resources were detected but are not included in this apply plan.")
    if runtime_report.runtime_icon_overrides:
        warnings.append("Runtime app icon override code was detected; asset catalog changes may not update the visible app icon.")

    plan = {
        "schemaVersion": RUN_SCHEMA_VERSION,
        "kind": "icon-generator-apply-plan",
        "createdAt": utc_now(),
        "runDir": str(run_dir),
        "root": str(root),
        "source": str(source),
        "sourceSha256": source_sha,
        "platform": platform,
        "selectedPlatforms": selected_platforms,
        "mode": selected_mode,
        "applyBlocked": apply_blocked,
        "requiresUserDecision": requires_user_decision,
        "blockedReason": mode_decision.get("blockedReason"),
        "blockingReasons": mode_decision.get("blockingReasons", []),
        "suggestedNextCommands": mode_decision.get("suggestedNextCommands", []),
        "fallbackOptions": mode_decision.get("fallbackOptions", []),
        "iconComposerPreflight": mode_decision.get("iconComposerPreflight"),
        "backup": backup,
        "backupDestination": str((run_dir / "backups").resolve()) if backup else None,
        "actions": actions,
        "filePlan": file_plan,
        "runtimeIconOverrides": runtime_report.to_dict()["runtimeIconOverrides"],
        "additionalIconResources": runtime_report.to_dict()["additionalIconResources"],
        "approvedRuntimeResources": include_runtime_resources,
        "detection": detection.to_dict(),
        "activeIcon": active_report,
        "warnings": warnings,
    }
    plan_path = run_dir / "apply-plan.json"
    write_json(plan_path, plan)
    return {**plan, "applyPlan": str(plan_path), "applyPlanSha256": sha256_file(plan_path)}


def approve_apply(run_dir: Path, *, apply_plan: Path, approval_note: str) -> dict[str, Any]:
    if not approval_note.strip():
        raise ValueError("approval note is required")
    run_dir = run_dir.resolve()
    apply_plan = apply_plan.resolve()
    plan = read_json(apply_plan)
    if plan.get("kind") != "icon-generator-apply-plan":
        raise ValueError("apply plan has invalid kind")
    if plan.get("applyBlocked") or plan.get("requiresUserDecision"):
        reason = plan.get("blockedReason") or "apply plan requires a user decision before project writes"
        raise ValueError(f"apply plan is blocked: {reason}")
    if Path(str(plan["runDir"])).resolve() != run_dir:
        raise ValueError("apply plan does not belong to this run")
    source = Path(str(plan["source"]))
    if not source.exists() or sha256_file(source) != plan.get("sourceSha256"):
        raise ValueError("apply plan source does not match the current source file")

    approval = {
        "schemaVersion": APPROVAL_SCHEMA_VERSION,
        "kind": "icon-generator-apply-approval",
        "createdAt": utc_now(),
        "approvalNote": approval_note,
        "runDir": str(run_dir),
        "applyPlan": str(apply_plan),
        "applyPlanSha256": sha256_file(apply_plan),
        "root": plan["root"],
        "source": plan["source"],
        "sourceSha256": plan["sourceSha256"],
        "platform": plan["platform"],
        "selectedPlatforms": plan["selectedPlatforms"],
        "mode": plan["mode"],
        "approvedRuntimeResources": plan.get("approvedRuntimeResources", []),
    }
    approval_path = run_dir / "approvals" / f"apply-{approval['applyPlanSha256'][:12]}.json"
    approval_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(approval_path, approval)
    stable_path = run_dir / "approvals" / "apply-approval.json"
    write_json(stable_path, approval)
    return {"ok": True, "approval": str(approval_path), "approvalStablePath": str(stable_path), **approval}


def apply_approval(approval_path: Path) -> dict[str, Any]:
    approval_path, _approval, plan_path, plan, source = read_valid_approval(approval_path)

    root = Path(str(plan["root"]))
    run_dir = Path(str(plan["runDir"]))
    backup = bool(plan.get("backup", True))
    backup_destination = plan.get("backupDestination")
    backup_root = Path(str(backup_destination)) if backup and backup_destination else (run_dir / "backups" if backup else None)
    changed_files: list[str] = []
    backup_files: list[str] = []
    removed_files: list[str] = []

    for action in plan.get("actions", []):
        kind = action.get("kind")
        if kind == "generate-apple":
            result = generate_apple_icons(root, source, platform=action["platform"], backup=backup, backup_root=backup_root)
            extend_write_result(result, changed_files, backup_files, removed_files)
        elif kind == "generate-android":
            result = generate_android_icons(root, source, backup=backup, backup_root=backup_root)
            extend_write_result(result, changed_files, backup_files, removed_files)
        elif kind == "generate-favicon":
            result_payload = generate_web_favicons(root, source, backup=backup, backup_root=backup_root)
            changed_files.extend(result_payload.get("changedFiles", []))
            backup_files.extend(result_payload.get("backupFiles", []))
            removed_files.extend(result_payload.get("removedFiles", []))
        elif kind == "generate-icon-composer":
            result_payload = generate_icon_composer_bundle(
                root,
                source,
                bg_color="#111111",
                bundle_name=action.get("bundleName", "AppIcon"),
                backup=backup,
                backup_root=backup_root,
            )
            changed_files.extend(result_payload.get("changedFiles", []))
            backup_files.extend(result_payload.get("backupFiles", []))
            removed_files.extend(result_payload.get("removedFiles", []))
        elif kind == "sync-runtime-icon":
            result = sync_runtime_icon(
                root,
                source,
                resource=action["resource"],
                size=int(action["size"]),
                backup=backup,
                backup_root=backup_root,
            )
            extend_write_result(result, changed_files, backup_files, removed_files)
        else:
            raise ValueError(f"unknown apply action: {kind}")

    applied_preview = applied_active_preview(root, run_dir, plan)
    verification = post_apply_verification(root, run_dir, plan, applied_preview)
    return {
        "ok": True,
        "approval": str(approval_path),
        "applyPlan": str(plan_path),
        "changedFiles": sorted(set(changed_files)),
        "backupFiles": sorted(set(backup_files)),
        "removedFiles": sorted(set(removed_files)),
        **applied_preview,
        "postApplyVerification": verification,
        "warnings": plan.get("warnings", []),
    }


def verify_applied_icon(approval_path: Path) -> dict[str, Any]:
    approval_path, _approval, plan_path, plan, _source = read_valid_approval(approval_path)
    root = Path(str(plan["root"]))
    run_dir = Path(str(plan["runDir"]))
    applied_preview = applied_active_preview(root, run_dir, plan)
    verification = post_apply_verification(root, run_dir, plan, applied_preview)
    return {
        "ok": verification["ok"],
        "approval": str(approval_path),
        "applyPlan": str(plan_path),
        **applied_preview,
        "postApplyVerification": verification,
        "warnings": plan.get("warnings", []),
    }


def read_valid_approval(approval_path: Path) -> tuple[Path, dict[str, Any], Path, dict[str, Any], Path]:
    approval_path = approval_path.resolve()
    approval = read_json(approval_path)
    if approval.get("kind") != "icon-generator-apply-approval":
        raise ValueError("approval has invalid kind")
    plan_path = Path(str(approval["applyPlan"]))
    plan = read_json(plan_path)
    if sha256_file(plan_path) != approval.get("applyPlanSha256"):
        raise ValueError("apply plan digest does not match approval")
    if plan.get("sourceSha256") != approval.get("sourceSha256"):
        raise ValueError("source digest in plan does not match approval")
    source = Path(str(plan["source"]))
    if sha256_file(source) != approval.get("sourceSha256"):
        raise ValueError("source file digest does not match approval")
    if Path(str(plan["root"])).resolve() != Path(str(approval["root"])).resolve():
        raise ValueError("project root in plan does not match approval")
    return approval_path, approval, plan_path, plan, source


def normalize_master_source(source: Path, out_dir: Path, *, allow_crop: bool, allow_upscale: bool) -> PreparedSource:
    if source.suffix.lower() != ".png":
        raise IconImageError(f"Source image must be a PNG: {source}")
    try:
        image = Image.open(source)
        image.load()
    except OSError as exc:
        raise IconImageError(f"Source image is not a valid PNG: {source}") from exc

    image = image.convert("RGBA")
    input_size = image.size
    operation = "copy"
    if image.width != image.height:
        if not allow_crop:
            raise IconImageError(f"Source image must be square; got {image.width}x{image.height}. Retry with --allow-crop after approval.")
        image = center_crop_square(image)
        operation = "center-crop"

    if image.width > MASTER_SIZE:
        image = resized_png(image, MASTER_SIZE, flatten=False)
        operation = f"{operation}+resize-down" if operation != "copy" else "resize-down"
    elif image.width < MASTER_SIZE:
        if not allow_upscale:
            raise IconImageError(f"Source image is smaller than 1024x1024; got {image.width}x{image.height}. Retry with --allow-upscale after approval.")
        image = resized_png(image, MASTER_SIZE, flatten=False)
        operation = f"{operation}+upscale" if operation != "copy" else "upscale"

    digest_source = hashlib.sha256()
    digest_source.update(image.tobytes())
    digest_source.update(str(image.size).encode("utf-8"))
    name = f"master-{digest_source.hexdigest()[:12]}.png"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / name
    image.save(out, format="PNG", optimize=True)
    return PreparedSource(
        path=out,
        sha256=sha256_file(out),
        operation=operation,
        input_size=input_size,
        output_size=image.size,
    )


def select_completed_job(run_dir: Path, job_id: str | None) -> dict[str, Any]:
    jobs = read_json(run_dir / "imagegen-jobs.json").get("jobs", [])
    completed = [job for job in jobs if job.get("status") == "completed"]
    if job_id:
        selected = next((job for job in completed if job.get("id") == job_id), None)
        if not selected:
            raise ValueError(f"completed job was not found: {job_id}")
        return selected
    if len(completed) != 1:
        raise ValueError("finalize-icon-run needs --job-id when zero or multiple jobs are completed")
    return completed[0]


def select_platforms(platform: str, detected_platforms: list[str]) -> list[str]:
    if platform == "auto":
        if not detected_platforms:
            raise ValueError("no supported platform detected; pass an explicit platform")
        return detected_platforms
    if platform == "both":
        return ["ios", "macos"]
    if platform not in {"ios", "macos", "android", "web"}:
        raise ValueError("platform must be auto, ios, macos, android, web, or both")
    if detected_platforms and platform not in detected_platforms:
        raise ValueError(f"requested platform {platform} conflicts with detected platforms: {', '.join(detected_platforms)}")
    return [platform]


def select_platforms_for_plan(
    platform: str,
    detected_platforms: list[str],
    request: dict[str, Any],
    active_report: dict[str, Any],
) -> list[str]:
    if platform != "auto":
        return select_platforms(platform, detected_platforms)
    if request.get("workflow") == "edit-current-active-icon":
        active_platform = active_report_platform(active_report)
        if active_platform and active_platform in detected_platforms:
            return [active_platform]
    return select_platforms(platform, detected_platforms)


def active_report_platform(active_report: dict[str, Any]) -> str | None:
    target = active_report.get("activeTarget")
    if isinstance(target, dict):
        platform = target.get("platform")
        if platform in {"ios", "macos", "android", "web"}:
            return str(platform)
    return None


def select_apply_mode(mode: str, selected_platforms: list[str], active_report: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    if mode == "legacy-appiconset":
        if selected_platforms == ["macos"] and is_macos_26_target(active_report):
            warnings.append(
                "Explicit legacy-appiconset mode selected for a macOS 26 target. This creates flat PNG assets, not Liquid Glass Icon Composer .icon output."
            )
        return {"mode": mode}
    if mode == "icon-composer":
        if selected_platforms == ["macos"]:
            preflight = icon_composer_preflight(check_package=False)
            if can_use_icon_composer(preflight):
                return {"mode": mode, "iconComposerPreflight": preflight}
            return blocked_icon_composer_mode(preflight, requested_mode=mode)
        return {"mode": mode}
    if mode != "auto":
        return {"mode": mode}
    if selected_platforms == ["macos"]:
        preflight = icon_composer_preflight(check_package=False)
        expects_liquid_glass = is_macos_26_target(active_report) or version_at_least(str(preflight.get("xcodeVersion") or ""), 26)
        if not expects_liquid_glass:
            return {"mode": "legacy-appiconset", "iconComposerPreflight": preflight}
        if can_use_icon_composer(preflight):
            warnings.append("macOS 26 target detected; auto mode selected Icon Composer .icon output for Liquid Glass.")
            return {"mode": "icon-composer", "iconComposerPreflight": preflight}
        return blocked_icon_composer_mode(preflight, requested_mode=mode)
    return {"mode": "legacy-appiconset"}


def can_use_icon_composer(preflight: dict[str, Any]) -> bool:
    return bool(preflight.get("canUseIconComposer", preflight.get("ok")) and preflight.get("liquidGlassReady"))


def blocked_icon_composer_mode(preflight: dict[str, Any], *, requested_mode: str) -> dict[str, Any]:
    blocking_reasons = preflight.get("blockingReasons") or ["Icon Composer/ictool is not ready."]
    return {
        "mode": "icon-composer",
        "applyBlocked": True,
        "requiresUserDecision": True,
        "blockedReason": "Icon Composer is required before macOS 26 Liquid Glass .icon assets can be applied.",
        "blockingReasons": blocking_reasons,
        "suggestedNextCommands": [
            "python -m app_icon_generator.cli icon-composer-install-guide --json",
            "python -m app_icon_generator.cli wait-icon-composer-ready --timeout 300 --interval 5 --json",
            "python -m app_icon_generator.cli plan-apply --run-dir <run-dir> --platform macos --mode icon-composer --json",
            "python -m app_icon_generator.cli plan-apply --run-dir <run-dir> --platform macos --mode legacy-appiconset --json",
        ],
        "fallbackOptions": preflight.get("fallbackOptions", []),
        "iconComposerPreflight": preflight,
        "warnings": [
            f"Requested mode {requested_mode} cannot continue until Icon Composer/ictool is ready.",
            "If the user refuses to install Icon Composer, ask before switching to explicit legacy-appiconset mode.",
        ],
    }


def is_macos_26_target(active_report: dict[str, Any]) -> bool:
    target = active_report.get("activeTarget")
    if not isinstance(target, dict):
        return False
    if target.get("platform") != "macos":
        return False
    settings = target.get("build_settings") if isinstance(target.get("build_settings"), dict) else {}
    deployment = settings.get("MACOSX_DEPLOYMENT_TARGET")
    return version_at_least(str(deployment or ""), 26)


def version_at_least(value: str, major: int) -> bool:
    match = re.search(r"(\d+)(?:\.(\d+))?", value)
    return bool(match and int(match.group(1)) >= major)


def apple_platform_arg(platforms: list[str]) -> str:
    has_ios = "ios" in platforms
    has_macos = "macos" in platforms
    if has_ios and has_macos:
        return "both"
    if has_ios:
        return "ios"
    if has_macos:
        return "macos"
    raise ValueError("no Apple platforms selected")


def apple_file_plan(root: Path, platform: str) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    if platform in {"ios", "both"}:
        appiconset = find_appiconset(root, platform="ios", fallback_name="AppIcon.appiconset")
        plan.extend(file_entries_for_specs(appiconset, IOS_ICON_SPECS))
        plan.append({"operation": "update", "path": str(appiconset / "Contents.json")})
        plan.extend(incompatible_entries(appiconset, ("icon_",), {spec.filename for spec in IOS_ICON_SPECS}))
    if platform in {"macos", "both"}:
        fallback = "AppIcon.appiconset" if platform == "macos" else "MacAppIcon.appiconset"
        appiconset = find_appiconset(root, platform="macos", fallback_name=fallback)
        plan.extend(file_entries_for_specs(appiconset, MACOS_ICON_SPECS))
        plan.append({"operation": "update", "path": str(appiconset / "Contents.json")})
        plan.extend(incompatible_entries(appiconset, ("Icon-App-",), {spec.filename for spec in MACOS_ICON_SPECS}))
    return plan


def file_entries_for_specs(appiconset: Path, specs: list[Any]) -> list[dict[str, Any]]:
    return [{"operation": "update", "path": str(appiconset / spec.filename), "pixels": spec.pixels} for spec in specs]


def incompatible_entries(appiconset: Path, prefixes: tuple[str, ...], expected_names: set[str]) -> list[dict[str, Any]]:
    if not appiconset.exists():
        return []
    return [
        {"operation": "remove-incompatible", "path": str(path)}
        for path in sorted(appiconset.glob("*.png"))
        if path.name not in expected_names and path.name.startswith(prefixes)
    ]


def android_file_plan(root: Path) -> list[dict[str, Any]]:
    res_dir = find_res_dir(root)
    plan = [
        {"operation": "update", "path": str(res_dir / density / "ic_launcher.png"), "pixels": pixels}
        for density, pixels in LEGACY_LAUNCHER_SPECS.items()
    ]
    plan.extend(
        [
            {"operation": "update", "path": str(res_dir / "drawable" / "ic_launcher_foreground.png"), "pixels": ADAPTIVE_FOREGROUND_SIZE},
            {"operation": "update", "path": str(res_dir / "drawable" / "ic_launcher_background.xml")},
            {"operation": "update", "path": str(res_dir / "mipmap-anydpi-v26" / "ic_launcher.xml")},
        ]
    )
    return plan


def icon_composer_file_plan(root: Path, *, bundle_name: str = "AppIcon") -> list[dict[str, Any]]:
    bundle_path = find_icon_composer_bundle_path(root, bundle_name)
    plan: list[dict[str, Any]] = [
        {"operation": "update", "path": str(bundle_path)},
        {"operation": "update", "path": str(bundle_path / "Assets" / "foreground.png")},
        {"operation": "update", "path": str(bundle_path / "icon.json")},
    ]
    legacy_appiconset = find_legacy_appiconset(root, bundle_name)
    if legacy_appiconset:
        plan.append({"operation": "remove-legacy-appiconset", "path": str(legacy_appiconset)})
    pbxproj = next((path for path in sorted(root.resolve().rglob("*.xcodeproj/project.pbxproj")) if not is_ignored_scan_path(path)), None)
    if pbxproj:
        plan.append({"operation": "update-xcode-project-resource", "path": str(pbxproj), "resource": str(bundle_path)})
    return plan


def applied_active_preview(root: Path, run_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    stamp = filename_timestamp()
    action_kinds = {str(action.get("kind")) for action in plan.get("actions", [])}
    if "generate-icon-composer" in action_kinds:
        action = next(action for action in plan.get("actions", []) if action.get("kind") == "generate-icon-composer")
        bundle_path = find_icon_composer_bundle_path(root, str(action.get("bundleName", "AppIcon")))
        foreground = icon_composer_foreground_path(bundle_path)
        if not foreground or not foreground.exists() or foreground.suffix.lower() != ".png":
            raise ValueError(f"Icon Composer foreground PNG was not found after apply: {bundle_path / 'Assets'}")
        digest = sha256_file(foreground)
        final = qa_dir / f"applied-active-preview-{stamp}-{digest[:12]}.png"
        shutil.copy2(foreground, final)
        active_icon = inspect_active_icon(root, platform="macos", run_xcodebuild=False)
        preview_manifest = {
            "schemaVersion": RUN_SCHEMA_VERSION,
            "kind": "icon-generator-preview-manifest",
            "createdAt": utc_now(),
            "runDir": str(run_dir),
            "root": str(root.resolve()),
            "displayPath": str(final),
            "displaySha256": digest,
            "sourcePath": str(foreground),
            "sourceSha256": digest,
            "activeIconComposerPath": str(bundle_path),
            "activeIconSetPath": None,
            "activeTarget": active_icon.get("activeTarget"),
            "platform": "macos",
            "selectedPlatforms": plan.get("selectedPlatforms", []),
            "applyPlanSha256": sha256_file(run_dir / "apply-plan.json") if (run_dir / "apply-plan.json").exists() else None,
        }
        manifest_path = qa_dir / f"preview-manifest-{stamp}-{digest[:12]}.json"
        write_json(manifest_path, preview_manifest)
        return {
            "appliedPreviewPath": str(final),
            "appliedPreviewSourcePath": str(foreground),
            "appliedPreviewSha256": digest,
            "activeIconSetPath": None,
            "activeIconComposerPath": str(bundle_path),
            "previewManifestPath": str(manifest_path),
        }

    tmp = qa_dir / f".applied-active-preview-{stamp}.tmp.png"
    platform = "auto"
    selected = plan.get("selectedPlatforms", [])
    if isinstance(selected, list) and len(selected) == 1:
        platform = str(selected[0])
    extracted = extract_current_master(root, tmp, platform=platform)
    digest = extracted["sha256"]
    final = qa_dir / f"applied-active-preview-{stamp}-{digest[:12]}.png"
    tmp.replace(final)
    active_icon = extracted["activeIcon"]
    preview_manifest = {
        "schemaVersion": RUN_SCHEMA_VERSION,
        "kind": "icon-generator-preview-manifest",
        "createdAt": utc_now(),
        "runDir": str(run_dir),
        "root": str(root.resolve()),
        "displayPath": str(final),
        "displaySha256": digest,
        "sourcePath": extracted["sourcePath"],
        "sourceSha256": sha256_file(Path(str(extracted["sourcePath"]))),
        "activeIconSetPath": active_icon.get("activeAppIconSetPath"),
        "activeTarget": active_icon.get("activeTarget"),
        "platform": platform,
        "selectedPlatforms": selected,
        "applyPlanSha256": sha256_file(run_dir / "apply-plan.json") if (run_dir / "apply-plan.json").exists() else None,
    }
    manifest_path = qa_dir / f"preview-manifest-{stamp}-{digest[:12]}.json"
    write_json(manifest_path, preview_manifest)
    return {
        "appliedPreviewPath": str(final),
        "appliedPreviewSourcePath": extracted["sourcePath"],
        "appliedPreviewSha256": digest,
        "activeIconSetPath": active_icon.get("activeAppIconSetPath"),
        "previewManifestPath": str(manifest_path),
    }


def post_apply_verification(root: Path, run_dir: Path, plan: dict[str, Any], applied_preview: dict[str, Any]) -> dict[str, Any]:
    active_source = Path(str(applied_preview.get("appliedPreviewSourcePath", "")))
    action_kinds = {str(action.get("kind")) for action in plan.get("actions", [])}
    comparison = compare_source_to_active_asset(Path(str(plan["source"])), active_source, plan)
    icon_composer_report = icon_composer_post_apply_report(root, plan) if "generate-icon-composer" in action_kinds else {"checked": False, "ok": None}
    contents = active_contents_json_report(applied_preview.get("activeIconSetPath")) if "generate-icon-composer" not in action_kinds else {"checked": False, "ok": None, "reason": "Icon Composer apply does not use AppIcon.appiconset Contents.json."}
    runtime_report = discover_runtime_icons(root).to_dict()
    guidance = visibility_guidance(plan, runtime_report)
    warnings: list[str] = []

    if comparison.get("activeMatchesApprovedSource") is False:
        warnings.append("The active icon asset does not match the approved source export.")
    if contents.get("ok") is False:
        warnings.append("The active AppIcon.appiconset Contents.json is invalid or references missing files.")
    if icon_composer_report.get("ok") is False:
        warnings.extend(str(item) for item in icon_composer_report.get("errors", []))
    warnings.extend(str(item) for item in icon_composer_report.get("warnings", []))
    if runtime_report.get("runtimeIconOverrides"):
        warnings.append("Runtime icon override code is still present; the app may display a runtime PNG instead of only the asset catalog icon.")
    if runtime_report.get("additionalIconResources"):
        warnings.append("Additional icon-like PNG resources are present; validate whether they must be synced separately.")

    comparable_ok = comparison.get("activeMatchesApprovedSource") is not False
    contents_ok = contents.get("ok") is not False
    icon_composer_ok = icon_composer_report.get("ok") is not False
    return {
        "ok": comparable_ok and contents_ok and icon_composer_ok,
        "approvedSource": plan["source"],
        "approvedSourceSha256": plan["sourceSha256"],
        "activePreviewPath": applied_preview.get("appliedPreviewPath"),
        "activePreviewSha256": applied_preview.get("appliedPreviewSha256"),
        "activePreviewSourcePath": applied_preview.get("appliedPreviewSourcePath"),
        "activeIconSetPath": applied_preview.get("activeIconSetPath"),
        "activeIconComposerPath": applied_preview.get("activeIconComposerPath"),
        "activeAssetComparison": comparison,
        "iconComposer": icon_composer_report,
        "contentsJson": contents,
        "runtimeIconOverrides": runtime_report.get("runtimeIconOverrides", []),
        "additionalIconResources": runtime_report.get("additionalIconResources", []),
        "visibilityGuidance": guidance,
        "warnings": warnings,
    }


def icon_composer_post_apply_report(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    action = next((item for item in plan.get("actions", []) if item.get("kind") == "generate-icon-composer"), None)
    if not action:
        return {"checked": False, "ok": None}
    bundle_name = str(action.get("bundleName", "AppIcon"))
    payload = inspect_icon_composer_project_state(root, bundle_name)
    payload["checked"] = True
    if payload.get("ok"):
        payload["buildVerificationRecommendation"] = (
            "Build the Xcode app and inspect .app/Contents/Resources/AppIcon.icns with inspect-built-app. "
            "A successful .icon apply means the source is connected; the built .icns is the final rendered artifact."
        )
    return payload


def compare_source_to_active_asset(source: Path, active_source: Path, plan: dict[str, Any]) -> dict[str, Any]:
    action_kinds = {str(action.get("kind")) for action in plan.get("actions", [])}
    if "generate-icon-composer" in action_kinds:
        if active_source.exists() and active_source.suffix.lower() == ".png":
            source_digest = sha256_file(source)
            active_digest = sha256_file(active_source)
            return {
                "status": "match" if source_digest == active_digest else "mismatch",
                "activeMatchesApprovedSource": source_digest == active_digest,
                "approvedSourcePath": str(source),
                "activeSourcePath": str(active_source),
                "approvedSourceSha256": source_digest,
                "activeSourceSha256": active_digest,
                "reason": "Icon Composer foreground should preserve the approved full-composition PNG exactly.",
            }
        return {
            "status": "not-comparable",
            "activeMatchesApprovedSource": None,
            "reason": "Icon Composer .icon output is not a direct PNG resize of the approved source.",
        }
    if not active_source.exists() or active_source.suffix.lower() != ".png":
        return {
            "status": "not-comparable",
            "activeMatchesApprovedSource": None,
            "reason": "The active source is not a PNG file.",
            "activeSourcePath": str(active_source),
        }

    source_image = load_master_icon(source, allow_crop=False)
    with Image.open(active_source) as image:
        active_image = image.convert("RGBA")

    flatten = "generate-apple" in action_kinds
    background = infer_edge_color(source_image) if flatten else None
    expected = resized_png(source_image, active_image.width, flatten=flatten, background=background).convert("RGBA")
    rgb_bbox = ImageChops.difference(expected.convert("RGB"), active_image.convert("RGB")).getbbox()
    alpha_bbox = ImageChops.difference(expected.getchannel("A"), active_image.getchannel("A")).getbbox()
    bbox = rgb_bbox or alpha_bbox
    return {
        "status": "match" if bbox is None else "mismatch",
        "activeMatchesApprovedSource": bbox is None,
        "approvedSourcePath": str(source),
        "activeSourcePath": str(active_source),
        "comparedSize": {"width": active_image.width, "height": active_image.height},
        "expectedFlattened": flatten,
        "differenceBoundingBox": list(bbox) if bbox else None,
        "rgbDifferenceBoundingBox": list(rgb_bbox) if rgb_bbox else None,
        "alphaDifferenceBoundingBox": list(alpha_bbox) if alpha_bbox else None,
    }


def active_contents_json_report(appiconset_value: object) -> dict[str, Any]:
    if not appiconset_value:
        return {"checked": False, "ok": None, "reason": "No active Apple AppIcon.appiconset was selected."}
    appiconset = Path(str(appiconset_value))
    contents = appiconset / "Contents.json"
    if not contents.exists():
        return {"checked": True, "ok": False, "path": str(contents), "missingFiles": [], "error": "Contents.json is missing."}
    try:
        payload = json.loads(contents.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"checked": True, "ok": False, "path": str(contents), "missingFiles": [], "error": str(exc)}

    missing: list[str] = []
    for entry in payload.get("images", []):
        filename = entry.get("filename")
        if filename and not (appiconset / filename).exists():
            missing.append(str(appiconset / filename))
    return {
        "checked": True,
        "ok": not missing,
        "path": str(contents),
        "imageCount": len(payload.get("images", [])),
        "missingFiles": missing,
    }


def visibility_guidance(plan: dict[str, Any], runtime_payload: dict[str, Any]) -> list[str]:
    selected = set(plan.get("selectedPlatforms", []))
    action_kinds = {str(action.get("kind")) for action in plan.get("actions", [])}
    guidance: list[str] = []
    if "macos" in selected:
        if "generate-icon-composer" in action_kinds:
            guidance.extend(
                [
                    "Rebuild the app after Icon Composer changes; Xcode renders the .icon project resource into the built .app resources.",
                    "Inspect the built app with inspect-built-app and check .app/Contents/Resources/AppIcon.icns after xcodebuild.",
                    "Quit and relaunch the app before judging the Dock icon. If the old icon remains, use Clean Build Folder and rebuild.",
                    "If multiple built .app copies exist with the same bundle id, remove or avoid stale build output before testing.",
                    "Run diagnose-macos-icon and find-running-apps before resetting Dock, Finder, LaunchServices, or iconservices caches.",
                ]
            )
        else:
            guidance.extend(
                [
                    "Rebuild the app after asset changes; Xcode compiles AppIcon.appiconset into the built .app resources.",
                    "Quit and relaunch the app before judging the Dock icon. If the old icon remains, use Clean Build Folder and rebuild.",
                    "If multiple built .app copies exist with the same bundle id, remove or avoid stale build output before testing.",
                    "Inspect the built app with inspect-built-app and check .app/Contents/Resources/AppIcon.icns or .icon output.",
                    "Run diagnose-macos-icon and find-running-apps before resetting Dock, Finder, LaunchServices, or iconservices caches.",
                ]
            )
    if "ios" in selected:
        guidance.append("Rebuild and reinstall the app on the simulator/device; SpringBoard may keep the previous icon until the app is reinstalled.")
    if "android" in selected:
        guidance.append("Rebuild and reinstall the app; Android launchers may cache old launcher icons until reinstall or launcher cache refresh.")
    if runtime_payload.get("runtimeIconOverrides") or runtime_payload.get("additionalIconResources"):
        guidance.append("The project has runtime icon state; validate whether resources such as NotificationIcon.png need a separate approved sync.")
    if plan.get("backup"):
        guidance.append("Safety backup was written into the run directory, outside the project; it is not active and must not be used as the next source unless restoring history explicitly.")
    return guidance


def runtime_resource_path(runtime_payload: dict[str, Any], resource: str) -> str:
    for item in runtime_payload.get("additionalIconResources", []):
        if item.get("name") == resource:
            return str(item.get("path"))
    return resource


def extend_write_result(result: WriteResult, changed_files: list[str], backup_files: list[str], removed_files: list[str]) -> None:
    changed_files.extend(result.changed_files)
    backup_files.extend(result.backup_files)
    removed_files.extend(result.removed_files)


def copy_references(run_dir: Path, references: list[Path]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for index, reference in enumerate(references, start=1):
        reference = reference.resolve()
        if not reference.exists():
            raise ValueError(f"reference image was not found: {reference}")
        target = run_dir / "references" / f"reference-{index}{reference.suffix.lower()}"
        shutil.copy2(reference, target)
        payloads.append({"path": str(target), "role": f"reference image {index}", "sourcePath": str(reference), "sha256": sha256_file(target)})
    return payloads


def build_edit_brief(
    description: str,
    *,
    from_current: bool,
    edit_intent: str | None = None,
    requested_changes: list[str] | None = None,
    preserved_elements: list[str] | None = None,
    change_intensity: str | None = None,
    target_area: str | None = None,
    style_constraints: list[str] | None = None,
    forbidden_changes: list[str] | None = None,
) -> dict[str, Any]:
    requested_changes = clean_list(requested_changes or [])
    preserved_elements = clean_list(preserved_elements or [])
    style_constraints = clean_list(style_constraints or [])
    forbidden_changes = clean_list(forbidden_changes or [])

    inferred_intent = infer_edit_intent(description, from_current=from_current)
    intent = edit_intent or inferred_intent
    if intent not in EDIT_INTENTS:
        raise ValueError(f"edit intent must be one of: {', '.join(sorted(EDIT_INTENTS))}")
    intensity = change_intensity or infer_change_intensity(description, intent=intent)
    if intensity not in CHANGE_INTENSITIES:
        raise ValueError(f"change intensity must be one of: {', '.join(sorted(CHANGE_INTENSITIES))}")

    inferred_target = target_area or infer_target_area(description)
    if not requested_changes:
        requested_changes = [description.strip()]
    if not preserved_elements:
        preserved_elements = infer_preserved_elements(description, intent=intent)

    if not style_constraints:
        style_constraints = default_style_constraints(intent)
    if not forbidden_changes:
        forbidden_changes = default_forbidden_changes(intent)

    return {
        "editIntent": intent,
        "summary": edit_brief_summary(intent, requested_changes, preserved_elements, intensity, inferred_target),
        "targetArea": inferred_target,
        "requestedChanges": requested_changes,
        "preservedElements": preserved_elements,
        "changeIntensity": intensity,
        "styleConstraints": style_constraints,
        "forbiddenChanges": forbidden_changes,
        "requiresUserConfirmationBeforeImagegen": bool(from_current),
        "variantLabels": ["matches-brief", "minor-drift", "violates-preserved-elements"],
        "applyPolicy": "variants labeled violates-preserved-elements require explicit user override before finalize/apply",
    }


def infer_edit_intent(description: str, *, from_current: bool) -> str:
    lowered = description.lower()
    redesign_markers = (
        "redesign",
        "new icon",
        "whole icon",
        "entire icon",
        "completely",
        "полностью",
        "всю икон",
        "вся икон",
        "переделай",
        "новую икон",
        "с нуля",
    )
    technical_markers = (
        "padding",
        "inset",
        "edge",
        "crop",
        "full-bleed",
        "canvas",
        "размер",
        "края",
        "паддинг",
        "обрез",
        "поле",
    )
    if any(marker in lowered for marker in redesign_markers):
        return "redesign"
    if any(marker in lowered for marker in technical_markers):
        return "technical-repair"
    return "localized-edit" if from_current else "redesign"


def infer_change_intensity(description: str, *, intent: str) -> str:
    lowered = description.lower()
    if any(marker in lowered for marker in ("tiny", "микро", "едва", "еле", "barely")):
        return "tiny"
    if any(marker in lowered for marker in ("чуть", "немного", "слегка", "slight", "slightly", "a bit", "subtle")):
        return "subtle"
    if any(marker in lowered for marker in ("сильно", "strong", "much more", "radical", "радикально")):
        return "strong"
    return "medium" if intent == "redesign" else "subtle"


def infer_target_area(description: str) -> str:
    patterns = [
        r"(?:everything is perfect,?\s+except|all perfect,?\s+except|perfect,?\s+except)\s+(.+?)(?:[.;]|$)",
        r"(?:все идеально,?\s+кроме|всё идеально,?\s+кроме|всё хорошо,?\s+кроме|все хорошо,?\s+кроме)\s+(.+?)(?:[.;]|$)",
        r"(?:кроме того, что)\s+(.+?)(?:[.;]|$)",
        r"(?:только|only)\s+(.+?)(?:[.;]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, description, flags=re.IGNORECASE)
        if match:
            return clean_phrase(match.group(1))
    return "target area described by requestedChanges; ask the user to confirm if ambiguous"


def infer_preserved_elements(description: str, *, intent: str) -> list[str]:
    if intent == "redesign":
        return []
    preserved: list[str] = []
    lowered = description.lower()
    if re.search(r"вс[её] идеально,?\s+кроме|everything is perfect,?\s+except|perfect,?\s+except", description, flags=re.IGNORECASE):
        preserved.append("everything except the requested target area")
    if "это все хорошо" in lowered or "это всё хорошо" in lowered or "everything else is good" in lowered:
        prefix = re.split(r"это вс[её] хорошо|everything else is good", description, flags=re.IGNORECASE)[0]
        segment = prefix.split(".")[-1]
        preserved.extend(clean_phrase(item) for item in re.split(r",|;|\band\b| и ", segment) if clean_phrase(item))
    keep_patterns = [
        r"(?:keep|preserve|do not change|don't change)\s+(.+?)(?:[.;]|$)",
        r"(?:сохрани|оставь|не меняй|не трогай)\s+(.+?)(?:[.;]|$)",
    ]
    for pattern in keep_patterns:
        for match in re.finditer(pattern, description, flags=re.IGNORECASE):
            preserved.extend(clean_phrase(item) for item in re.split(r",|;|\band\b| и ", match.group(1)) if clean_phrase(item))
    if not preserved:
        preserved = [
            "composition and object positions",
            "overall palette",
            "background unless explicitly requested",
            "unmentioned icon elements",
            "symbol semantics",
        ]
    return unique_strings(preserved)


def default_style_constraints(intent: str) -> list[str]:
    if intent == "redesign":
        return ["respect the user request", "keep app-icon readability at small sizes"]
    return [
        "same composition",
        "same camera/framing",
        "same palette except requested target changes",
        "same lighting except requested target changes",
        "same material except requested target changes",
        "same background unless explicitly requested",
    ]


def default_forbidden_changes(intent: str) -> list[str]:
    if intent == "redesign":
        return ["text or watermark unless explicitly requested", "baked outer frame unless explicitly requested"]
    return [
        "full icon redesign",
        "changing preserved elements",
        "moving or resizing unmentioned objects",
        "changing palette outside the requested target",
        "changing background unless explicitly requested",
        "adding objects, text, watermark, or baked outer frame",
    ]


def edit_brief_summary(
    intent: str,
    requested_changes: list[str],
    preserved_elements: list[str],
    intensity: str,
    target_area: str,
) -> str:
    change = requested_changes[0] if requested_changes else "no requested change"
    preserved = ", ".join(preserved_elements[:3]) if preserved_elements else "none"
    return f"{intent}; target={target_area}; intensity={intensity}; change={change}; preserve={preserved}"


def clean_list(values: list[str]) -> list[str]:
    return unique_strings(clean_phrase(value) for value in values if clean_phrase(value))


def clean_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" \n\t:,-.")).strip()


def unique_strings(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def bullet_list(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "- none"
    return "\n".join(f"- {value}" for value in values)


def design_prompt(
    description: str,
    index: int,
    references: list[dict[str, Any]],
    *,
    edit_current: bool,
    edit_brief: dict[str, Any] | None = None,
) -> str:
    reference_text = "Use the attached reference images only as visual grounding." if references else "No reference image was provided."
    if edit_current:
        variant_direction = localized_edit_variant_direction(index)
        shape_guidance = localized_shape_guidance(description)
        brief = edit_brief or build_edit_brief(description, from_current=True)
        requested = bullet_list(brief.get("requestedChanges", []))
        preserved = bullet_list(brief.get("preservedElements", []))
        style_constraints = bullet_list(brief.get("styleConstraints", []))
        forbidden = bullet_list(brief.get("forbiddenChanges", []))
        intent = str(brief.get("editIntent", "localized-edit"))
        if intent == "localized-edit":
            opening = "this is not a redesign."
            strict_contract = "Strict edit contract: this is not a redesign. Keep the same icon identity. Baseline invariant: preserve the current composition, style, palette, material, lighting model, symbol semantics, camera/framing, background, and overall silhouette unless the request explicitly changes one of those. Preserve the current composition, style, palette, material, lighting, and object identity."
            allowed_change = "Allowed change: only the requested visual detail may change. Every other visible element should remain as close as possible to the input image."
        elif intent == "technical-repair":
            opening = "this is a technical repair, not a redesign."
            strict_contract = "Strict repair contract: preserve the approved/current icon design. Change only the technical defect described in requestedChanges."
            allowed_change = "Allowed change: deterministic geometry, edge, canvas, or size repair only."
        else:
            opening = "this is an approved redesign of the current icon."
            strict_contract = "Redesign contract: the current icon is grounding/reference. Follow requestedChanges while preserving any explicitly listed preservedElements."
            allowed_change = "Allowed change: broader style and composition changes are allowed only to the extent described in requestedChanges."
        return f"""{opening}
Use case: precise-object-edit
Asset type: app icon master edit variant
Primary request: {description}
Edit intent: {intent}
Change intensity: {brief.get("changeIntensity", "subtle")}
Target area: {brief.get("targetArea", "requested area from the user description")}
Requested changes:
{requested}
Preserved elements:
{preserved}
Style constraints:
{style_constraints}
Forbidden changes:
{forbidden}
Input images: Use the attached current active app icon master as the edit target.
{strict_contract}
{allowed_change}
Forbidden changes: do not invent a new icon, new layout, new color palette, new material, new perspective, new background, new outer border, baked rounded tile, text, watermark, decorative effects, or additional objects.
Style scope rule: if the request asks for a style such as low poly, matte, glossy, flatter, more angular, softer, or brighter, apply that style only to the target area unless edit intent is redesign.
Shape edit rule: if the request asks to make a rounded object flatter, sharper, softer, thicker, thinner, straighter, or more/less curved, adjust only that object's geometry. Keep it in the same icon language. Do not make unrelated objects faceted, crystalline, low-poly, segmented, beveled, or angular.
{shape_guidance}
Visual cleanup rule: if the request involves glow, shadow, background color, color transitions, or tiny visual artifacts, create a clean coherent master render grounded in the input image. Do not simulate threshold masks, median filters, local blur patches, row/column interpolation, or pixel-level retouch artifacts.
Variant direction: {variant_direction}
"""
    emphasis = [
        "balanced, full-bleed app icon composition",
        "strong silhouette and readable center shape",
        "clean edges with no baked outer frame",
        "simple geometry that remains readable at 32px",
    ][index - 1]
    return f"""Use case: logo-brand
Asset type: app icon master variant
Primary request: {description}
Input images: {reference_text}
Composition/framing: square 1024x1024 app icon master, {emphasis}
Constraints: no text unless explicitly requested; no watermark; no outer border; no icon inside an icon; no rounded tile baked into the canvas; preserve transparent or full-bleed edges as appropriate for platform export.
"""


def repair_prompt(scope: str, note: str, canonical: str | None) -> str:
    if scope in {"geometry", "edge"}:
        return f"""Deterministic repair request.
Scope: {scope}
Note: {note}
Use local geometry/edge processing only. Do not call image generation for this repair.
Canonical master: {canonical or "not finalized yet"}
"""
    return f"""Use case: precise-object-edit
Asset type: app icon master repair
Primary request: {note}
Input images: canonical master reference must be used.
Constraints: preserve the approved design, style, composition, palette, and symbol semantics. Change only the described visual defect. No new outer frame, no icon inside an icon, no text, no watermark.
Clean-render rule: for glow, shadow, background color, or tiny visual artifacts, regenerate the affected visual cleanly as a coherent icon master. Do not use local pixel filters, manual color threshold masks, row/column interpolation, or blur patches.
"""


def localized_edit_variant_direction(index: int) -> str:
    directions = [
        "option 1 applies the smallest visible localized change; preserve everything else nearly pixel-for-pixel.",
        "option 2 applies a slightly stronger localized change; preserve style, color, lighting, and composition.",
        "option 3 applies a moderate localized change while keeping the original icon identity unchanged.",
        "option 4 applies the strongest acceptable localized change without becoming a redesign.",
    ]
    return directions[min(index, len(directions)) - 1]


def localized_shape_guidance(description: str) -> str:
    lowered = description.lower()
    shape_keywords = (
        "flat",
        "flatter",
        "flatten",
        "плоск",
        "приплюс",
        "округ",
        "кругл",
        "round",
        "rounded",
        "straight",
        "прям",
        "curve",
        "curved",
        "крив",
        "толщ",
        "thin",
        "thinner",
        "тонь",
    )
    if not any(keyword in lowered for keyword in shape_keywords):
        return "Localized edit rule: make the minimum coherent change needed for the request; do not reinterpret the object."
    return (
        "Localized shape guidance: treat the input shape as authoritative. For a flatter rounded object, reduce curvature or vertical roundness only enough to satisfy the request while keeping the same smooth graphic language, colors, lighting, and material."
    )


def visual_edit_policy(description: str, *, from_current: bool) -> dict[str, Any]:
    visual = requires_visual_regeneration(description)
    mode = "imagegen-clean-master" if visual or from_current else "imagegen-design-variants"
    deterministic_allowed = not visual
    guidance = (
        "Use image generation/editing to create a clean preview. Do not use PIL thresholding, local blur, median filters, or manual masks for glow, shadow, background, color, or tiny artifact fixes."
        if visual
        else "Use image generation for visual variants; use deterministic processing only after a preview is selected."
    )
    return {
        "mode": mode,
        "visualRegenerationRequired": visual,
        "deterministicPixelRetouchAllowed": deterministic_allowed,
        "redesignAllowed": not from_current,
        "editStrictness": "localized-preserve-invariants" if from_current else "new-design-variants",
        "guidance": guidance,
    }


def requires_visual_regeneration(text: str) -> bool:
    lowered = text.lower()
    keywords = (
        "glow",
        "glowing",
        "свеч",
        "сия",
        "shadow",
        "тень",
        "artifact",
        "артефакт",
        "dots",
        "точк",
        "штрих",
        "streak",
        "полос",
        "background",
        "фон",
        "black",
        "white",
        "чёрн",
        "черн",
        "бел",
        "color",
        "цвет",
        "blue",
        "син",
        "glossy",
        "глянц",
    )
    return any(keyword in lowered for keyword in keywords)


def new_run_dir(root: Path, description: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = slugify(root.name or description) or "icon-run"
    base = cache_root() / "runs"
    run_dir = base / f"{slug}-{timestamp}"
    suffix = 2
    while run_dir.exists():
        run_dir = base / f"{slug}-{timestamp}-{suffix}"
        suffix += 1
    return run_dir


def cache_root() -> Path:
    override = os.environ.get("ICON_GENERATOR_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "posix" and Path.home().joinpath("Library").exists():
        return Path.home() / "Library" / "Caches" / "icon-generator"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "icon-generator"
    return Path.home() / ".cache" / "icon-generator"


def image_metadata(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        return {"path": str(path), "width": image.width, "height": image.height, "mode": image.mode, "format": image.format}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def filename_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-")[:48]


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def is_processed_run_artifact(path: Path) -> bool:
    if not any(part in {"decoded", "prepared", "qa"} for part in path.parts):
        return False
    for parent in path.parents:
        if (parent / "request.json").exists() and (parent / "imagegen-jobs.json").exists():
            return True
    return False
