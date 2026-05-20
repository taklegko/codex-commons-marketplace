from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app_icon_generator.apple import IOS_ICON_SPECS, MACOS_ICON_SPECS, generate_apple_icons
from app_icon_generator.validate import validate_project


def make_master_icon(path: Path) -> None:
    image = Image.new("RGBA", (1024, 1024), (24, 88, 160, 255))
    image.save(path)


def test_generates_ios_appiconset(tmp_path: Path) -> None:
    root = tmp_path / "ios"
    (root / "MyApp.xcodeproj").mkdir(parents=True)
    (root / "MyApp" / "Assets.xcassets").mkdir(parents=True)
    source = tmp_path / "master.png"
    make_master_icon(source)

    result = generate_apple_icons(root, source, platform="ios", backup=True)

    appiconset = root / "MyApp" / "Assets.xcassets" / "AppIcon.appiconset"
    contents = json.loads((appiconset / "Contents.json").read_text(encoding="utf-8"))
    filenames = {entry["filename"] for entry in contents["images"]}
    assert filenames == {spec.filename for spec in IOS_ICON_SPECS}
    assert len(result.changed_files) == len(IOS_ICON_SPECS) + 1

    validation = validate_project(root)
    assert validation["ok"], validation["errors"]


def test_generates_macos_appiconset(tmp_path: Path) -> None:
    root = tmp_path / "macos"
    (root / "MacApp.xcodeproj").mkdir(parents=True)
    (root / "MacApp" / "Assets.xcassets").mkdir(parents=True)
    source = tmp_path / "master.png"
    make_master_icon(source)

    result = generate_apple_icons(root, source, platform="macos", backup=True)

    appiconset = root / "MacApp" / "Assets.xcassets" / "AppIcon.appiconset"
    contents = json.loads((appiconset / "Contents.json").read_text(encoding="utf-8"))
    filenames = {entry["filename"] for entry in contents["images"]}
    assert filenames == {spec.filename for spec in MACOS_ICON_SPECS}
    assert len(result.changed_files) == len(MACOS_ICON_SPECS) + 1

    validation = validate_project(root)
    assert validation["ok"], validation["errors"]


def test_apple_backup_preserves_existing_file(tmp_path: Path) -> None:
    root = tmp_path / "ios"
    appiconset = root / "Assets.xcassets" / "AppIcon.appiconset"
    appiconset.mkdir(parents=True)
    source = tmp_path / "master.png"
    make_master_icon(source)
    existing = appiconset / "Contents.json"
    existing.write_text('{"old": true}\n', encoding="utf-8")

    result = generate_apple_icons(root, source, platform="ios", backup=True)

    assert result.backup_files
    backup_contents = Path(result.backup_files[0]).read_text(encoding="utf-8")
    assert backup_contents == '{"old": true}\n'


def test_macos_generation_removes_stale_ios_icons(tmp_path: Path) -> None:
    root = tmp_path / "macos"
    appiconset = root / "Assets.xcassets" / "AppIcon.appiconset"
    appiconset.mkdir(parents=True)
    source = tmp_path / "master.png"
    make_master_icon(source)
    stale = appiconset / "Icon-App-60x60@2x.png"
    Image.new("RGBA", (120, 120), (1, 2, 3, 255)).save(stale)

    result = generate_apple_icons(root, source, platform="macos", backup=True)

    assert not stale.exists()
    assert str(stale) in result.removed_files
    assert any(path.endswith("Icon-App-60x60@2x.png") for path in result.backup_files)


def test_ios_generation_removes_stale_macos_icons(tmp_path: Path) -> None:
    root = tmp_path / "ios"
    appiconset = root / "Assets.xcassets" / "AppIcon.appiconset"
    appiconset.mkdir(parents=True)
    source = tmp_path / "master.png"
    make_master_icon(source)
    stale = appiconset / "icon_512x512.png"
    Image.new("RGBA", (512, 512), (1, 2, 3, 255)).save(stale)

    result = generate_apple_icons(root, source, platform="ios", backup=True)

    assert not stale.exists()
    assert str(stale) in result.removed_files
    assert any(path.endswith("icon_512x512.png") for path in result.backup_files)
