from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app_icon_generator.detect_project import detect_project
from app_icon_generator.validate import validate_project
from app_icon_generator.web import FAVICON_PNG_SPECS, generate_web_favicons
from app_icon_generator.workflow import apply_approval, approve_apply, finalize_icon_run, plan_apply, prepare_icon_run, record_imagegen_result


def make_master_icon(path: Path, color: tuple[int, int, int, int] = (20, 80, 160, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (1024, 1024), color).save(path)


def make_web_fixture(root: Path) -> None:
    (root / "public").mkdir(parents=True)
    (root / "package.json").write_text('{"scripts":{"dev":"vite"}}\n', encoding="utf-8")
    (root / "index.html").write_text("<html><head></head><body></body></html>\n", encoding="utf-8")


def test_detects_web_project(tmp_path: Path) -> None:
    root = tmp_path / "site"
    make_web_fixture(root)

    detection = detect_project(root).to_dict()

    assert "web" in detection["platforms"]
    target = next(target for target in detection["targets"] if target["platform"] == "web")
    assert target["app_icon_name"] == "favicon"
    assert target["active_icon"]["activeTarget"]["outputDir"].endswith("/public")


def test_generates_web_favicon_assets(tmp_path: Path) -> None:
    root = tmp_path / "site"
    make_web_fixture(root)
    source = tmp_path / "master.png"
    make_master_icon(source)

    result = generate_web_favicons(root, source, backup=True)

    assert result["ok"] is True
    assert result["htmlSnippet"].count("rel=") == 5
    for spec in FAVICON_PNG_SPECS:
        path = root / "public" / spec.filename
        with Image.open(path) as image:
            assert image.size == (spec.pixels, spec.pixels)
    with Image.open(root / "public" / "favicon.ico") as image:
        assert image.format == "ICO"
    manifest = json.loads((root / "public" / "site.webmanifest").read_text(encoding="utf-8"))
    assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "512x512"}

    validation = validate_project(root)
    assert validation["ok"], validation["errors"]
    assert validation["web"]["found"] is True


def test_web_apply_uses_run_backups_and_applied_preview(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ICON_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "site"
    make_web_fixture(root)
    old_source = tmp_path / "old.png"
    new_source = tmp_path / "new.png"
    make_master_icon(old_source, color=(220, 10, 20, 255))
    make_master_icon(new_source, color=(1, 2, 3, 255))
    generate_web_favicons(root, old_source, backup=False)

    run_dir = Path(prepare_icon_run(root, description="website favicon", variants=2)["runDir"])
    record_imagegen_result(run_dir, job_id="variant-1", source=new_source)
    finalize_icon_run(run_dir, job_id="variant-1")
    plan = plan_apply(run_dir, platform="web", mode="auto", backup=True)
    approval = approve_apply(run_dir, apply_plan=Path(plan["applyPlan"]), approval_note="Apply favicon assets.")

    result = apply_approval(Path(approval["approval"]))

    assert result["changedFiles"]
    assert result["backupFiles"]
    assert all(Path(path).is_relative_to(run_dir / "backups") for path in result["backupFiles"])
    assert not (root / ".icon-generator-backups").exists()
    assert result["appliedPreviewPath"].startswith(str(run_dir / "qa"))
    assert result["appliedPreviewSourcePath"].endswith("android-chrome-512x512.png")
