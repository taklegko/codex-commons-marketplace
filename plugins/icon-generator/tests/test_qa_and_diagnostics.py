from __future__ import annotations

from pathlib import Path
import plistlib
import shutil
import subprocess

from PIL import Image
import pytest

from app_icon_generator.app_inspect import find_running_apps, inspect_built_app
from app_icon_generator.cli import main
from app_icon_generator.qa import make_contact_sheet, qa_source


def make_icon(path: Path, *, alpha: int = 255) -> None:
    Image.new("RGBA", (1024, 1024), (24, 88, 160, alpha)).save(path)


def test_qa_source_reports_edges_and_contact_sheet(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    out = tmp_path / "qa"
    make_icon(source)

    payload = qa_source(source, out)

    assert payload["size"] == {"width": 1024, "height": 1024}
    assert payload["edgeAlphaAverage"] == 255
    assert payload["contactSheet"]
    assert Path(payload["contactSheet"]).exists()
    assert payload["likelyVisibleFrameRisk"] is True
    assert payload["edgeSides"]["top"]["alphaAverage"] == 255
    assert payload["uniformBorderThickness"]


def test_qa_source_reports_non_1024_and_uniform_field_risk(tmp_path: Path) -> None:
    source = tmp_path / "large-white-field.png"
    out = tmp_path / "qa"
    image = Image.new("RGBA", (1254, 1254), (255, 255, 255, 255))
    draw_color = (10, 10, 10, 255)
    for x in range(180, 1074):
        for y in range(180, 1074):
            image.putpixel((x, y), draw_color)
    image.save(source)

    payload = qa_source(source, out)

    warnings = "\n".join(payload["warnings"])
    assert "1254x1254" in warnings
    assert payload["whiteFieldRisk"] is True
    assert payload["uniformBorderThickness"]["top"] >= 16
    assert payload["fullBleedSuitability"] == "needs-1024-normalization"


def test_make_contact_sheet(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    out = tmp_path / "sheet.png"
    make_icon(first)
    make_icon(second, alpha=200)

    result = make_contact_sheet([first, second], out)

    assert result == str(out)
    assert out.exists()


def test_cli_contact_sheet_rejects_project_root_output_without_override(tmp_path: Path) -> None:
    project = tmp_path / "replyx"
    xcodeproj = project / "ReplyX.xcodeproj"
    xcodeproj.mkdir(parents=True)
    (xcodeproj / "project.pbxproj").write_text("{}", encoding="utf-8")
    source = tmp_path / "first.png"
    make_icon(source)

    with pytest.raises(SystemExit) as exc:
        main(["make-contact-sheet", "--sources", str(source), "--out", str(project / "icon-candidates-contact-sheet.jpg"), "--json"])

    assert exc.value.code == 2
    assert not (project / "icon-candidates-contact-sheet.jpg").exists()


def test_cli_contact_sheet_allows_project_output_with_explicit_override(tmp_path: Path) -> None:
    project = tmp_path / "replyx"
    xcodeproj = project / "ReplyX.xcodeproj"
    xcodeproj.mkdir(parents=True)
    (xcodeproj / "project.pbxproj").write_text("{}", encoding="utf-8")
    source = tmp_path / "first.png"
    out = project / "icon-candidates-contact-sheet.jpg"
    make_icon(source)

    code = main(["make-contact-sheet", "--sources", str(source), "--out", str(out), "--allow-project-output", "--json"])

    assert code == 0
    assert out.exists()


def test_find_running_apps_reports_duplicate_bundle_paths() -> None:
    ps_output = """
      101 /tmp/A/ReplyX.app/Contents/MacOS/ReplyX
      102 /tmp/B/ReplyX.app/Contents/MacOS/ReplyX
    """

    def fake_read_bundle_identifier(path: Path) -> str | None:
        if "ReplyX.app" in str(path):
            return "dev.local.ReplyX"
        return None

    import app_icon_generator.app_inspect as app_inspect

    original = app_inspect.read_bundle_identifier
    app_inspect.read_bundle_identifier = fake_read_bundle_identifier
    try:
        payload = find_running_apps("dev.local.ReplyX", ps_output=ps_output)
    finally:
        app_inspect.read_bundle_identifier = original

    assert payload["hasDuplicates"] is True
    assert payload["count"] == 2


@pytest.mark.skipif(not shutil.which("iconutil"), reason="iconutil is required to build a valid icns fixture")
def test_inspect_built_app_reads_icns_representations(tmp_path: Path) -> None:
    app = tmp_path / "Demo.app"
    resources = app / "Contents" / "Resources"
    iconset = tmp_path / "AppIcon.iconset"
    resources.mkdir(parents=True)
    iconset.mkdir()
    plist_payload = {"CFBundleIdentifier": "com.example.demo"}
    with (app / "Contents" / "Info.plist").open("wb") as file:
        plistlib.dump(plist_payload, file)

    for filename, size in {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }.items():
        Image.new("RGBA", (size, size), (20, 40, 60, 255)).save(iconset / filename)

    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(resources / "AppIcon.icns")], check=True)

    payload = inspect_built_app(app)

    assert payload["bundleIdentifier"] == "com.example.demo"
    assert payload["icns"][0]["ok"] is True
    sizes = {(rep["pixelWidth"], rep["pixelHeight"]) for rep in payload["icns"][0]["representations"]}
    assert (1024, 1024) in sizes
