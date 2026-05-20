from __future__ import annotations

from pathlib import Path

from PIL import Image

from app_icon_generator.android import ADAPTIVE_FOREGROUND_SIZE, LEGACY_LAUNCHER_SPECS, generate_android_icons
from app_icon_generator.validate import validate_project


def make_master_icon(path: Path) -> None:
    image = Image.new("RGBA", (1024, 1024), (200, 40, 80, 255))
    image.save(path)


def test_generates_android_launcher_icons(tmp_path: Path) -> None:
    root = tmp_path / "android"
    res_dir = root / "app" / "src" / "main" / "res"
    res_dir.mkdir(parents=True)
    source = tmp_path / "master.png"
    make_master_icon(source)

    result = generate_android_icons(root, source, backup=True)

    for density, pixels in LEGACY_LAUNCHER_SPECS.items():
        path = res_dir / density / "ic_launcher.png"
        with Image.open(path) as image:
            assert image.size == (pixels, pixels)

    with Image.open(res_dir / "drawable" / "ic_launcher_foreground.png") as image:
        assert image.size == (ADAPTIVE_FOREGROUND_SIZE, ADAPTIVE_FOREGROUND_SIZE)

    assert (res_dir / "drawable" / "ic_launcher_background.xml").exists()
    assert (res_dir / "mipmap-anydpi-v26" / "ic_launcher.xml").exists()
    assert len(result.changed_files) == len(LEGACY_LAUNCHER_SPECS) + 3

    validation = validate_project(root)
    assert validation["ok"], validation["errors"]


def test_android_backup_preserves_existing_icon(tmp_path: Path) -> None:
    root = tmp_path / "android"
    res_dir = root / "app" / "src" / "main" / "res"
    old_icon = res_dir / "mipmap-mdpi" / "ic_launcher.png"
    old_icon.parent.mkdir(parents=True)
    Image.new("RGBA", (48, 48), (1, 2, 3, 255)).save(old_icon)
    source = tmp_path / "master.png"
    make_master_icon(source)

    result = generate_android_icons(root, source, backup=True)

    assert result.backup_files
    backup = next(Path(path) for path in result.backup_files if path.endswith("mipmap-mdpi/ic_launcher.png"))
    with Image.open(backup) as image:
        assert image.getpixel((0, 0)) == (1, 2, 3, 255)
