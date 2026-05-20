from __future__ import annotations

from pathlib import Path

from PIL import Image
import pytest

from app_icon_generator.cli import main
from app_icon_generator.detect_project import detect_project
from app_icon_generator.runtime_icons import discover_runtime_icons, sync_runtime_icon
from app_icon_generator.validate import validate_project


def make_master(path: Path, color: tuple[int, int, int, int] = (10, 120, 220, 255)) -> None:
    Image.new("RGBA", (1024, 1024), color).save(path)


def make_replyx_style_fixture(root: Path) -> Path:
    xcodeproj = root / "replyx.xcodeproj"
    appiconset = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset"
    swift = root / "App" / "Core" / "AppIconManager.swift"
    notification = root / "App" / "Resources" / "NotificationIcon.png"
    xcodeproj.mkdir(parents=True)
    appiconset.mkdir(parents=True)
    swift.parent.mkdir(parents=True)
    notification.parent.mkdir(parents=True, exist_ok=True)

    (xcodeproj / "project.pbxproj").write_text(
        """
        {
          1 /* NotificationIcon.png in Resources */ = {isa = PBXBuildFile; };
          buildSettings = {
            SDKROOT = macosx;
            PRODUCT_NAME = ReplyX;
            PRODUCT_BUNDLE_IDENTIFIER = dev.local.ReplyX.debug;
            ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;
            INFOPLIST_FILE = App/Resources/Info.plist;
          };
        }
        """,
        encoding="utf-8",
    )
    (appiconset / "Contents.json").write_text(
        """
        {
          "images": [
            {
              "idiom": "mac",
              "size": "512x512",
              "scale": "2x",
              "filename": "icon_512x512@2x.png"
            }
          ],
          "info": { "author": "xcode", "version": 1 }
        }
        """,
        encoding="utf-8",
    )
    Image.new("RGBA", (1024, 1024), (1, 2, 3, 255)).save(appiconset / "icon_512x512@2x.png")
    Image.new("RGBA", (512, 512), (200, 30, 60, 255)).save(notification)
    swift.write_text(
        """
        enum AppIconManager {
            private static let iconName = "NotificationIcon"

            static func applyApplicationIcon() {
                guard let url = Bundle.main.url(forResource: iconName, withExtension: "png") else {
                    return
                }
                NSApplication.shared.applicationIconImage = NSImage(contentsOf: url)
            }
        }
        """,
        encoding="utf-8",
    )
    return notification


def test_replyx_style_runtime_icons_are_reported_by_detect(tmp_path: Path) -> None:
    notification = make_replyx_style_fixture(tmp_path)

    detection = detect_project(tmp_path)
    target = detection.targets[0]

    assert target.platform == "macos"
    assert target.bundle_identifier == "dev.local.ReplyX.debug"
    assert target.runtime_icon_overrides
    assert target.additional_icon_resources
    assert target.additional_icon_resources[0]["path"] == str(notification)


def test_validate_warns_about_runtime_icon_override_and_out_of_sync_resource(tmp_path: Path) -> None:
    make_replyx_style_fixture(tmp_path)

    payload = validate_project(tmp_path)

    assert payload["ok"] is True
    assert payload["runtimeIconOverrides"]
    assert payload["additionalIconResources"]
    warnings = "\n".join(payload["warnings"])
    assert "Runtime app icon override detected" in warnings
    assert "NotificationIcon.png" in warnings
    assert "out of sync" in warnings


def test_sync_runtime_icon_writes_approved_resource_and_backup(tmp_path: Path) -> None:
    notification = make_replyx_style_fixture(tmp_path)
    source = tmp_path / "master.png"
    make_master(source)

    result = sync_runtime_icon(tmp_path, source, resource="NotificationIcon.png", size=512, backup=True)

    assert result.changed_files == [str(notification)]
    assert result.backup_files
    with Image.open(notification) as image:
        assert image.size == (512, 512)
        assert image.getpixel((0, 0)) == (10, 120, 220, 255)


def test_generate_commands_require_approved_source(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    make_master(source)

    with pytest.raises(SystemExit) as exc:
        main(["generate-android", "--root", str(tmp_path), "--source", str(source), "--json"])

    assert exc.value.code == 2


def test_backup_dirs_warn_when_not_gitignored(tmp_path: Path) -> None:
    (tmp_path / ".icon-generator-backups").mkdir()
    (tmp_path / ".app-icon-generator-backups").mkdir()

    payload = validate_project(tmp_path)

    warnings = "\n".join(payload["warnings"])
    assert ".icon-generator-backups/" in warnings
    assert ".app-icon-generator-backups/" in warnings


def test_runtime_icon_scans_ignore_backup_dirs(tmp_path: Path) -> None:
    make_replyx_style_fixture(tmp_path)
    backup = tmp_path / ".app-icon-generator-backups" / "old" / "NotificationIcon.png"
    backup.parent.mkdir(parents=True)
    Image.new("RGBA", (512, 512), (0, 0, 0, 255)).save(backup)

    report = discover_runtime_icons(tmp_path)

    assert len(report.additional_icon_resources) == 1
    assert ".app-icon-generator-backups" not in report.additional_icon_resources[0].path
