from __future__ import annotations

from pathlib import Path

from app_icon_generator.detect_project import detect_project


FIXTURES = Path(__file__).parent / "fixtures"


def test_detects_android_project() -> None:
    detection = detect_project(FIXTURES / "android")

    assert detection.platforms == ["android"]
    assert len(detection.targets) == 1
    assert detection.targets[0].platform == "android"


def test_detects_ios_project() -> None:
    detection = detect_project(FIXTURES / "ios")

    assert detection.platforms == ["ios"]
    assert len(detection.targets) == 1
    assert detection.targets[0].platform == "ios"


def test_detects_macos_project() -> None:
    detection = detect_project(FIXTURES / "macos")

    assert detection.platforms == ["macos"]
    assert len(detection.targets) == 1
    assert detection.targets[0].platform == "macos"


def test_detects_mixed_flutter_style_project() -> None:
    detection = detect_project(FIXTURES / "flutter")

    assert detection.platforms == ["android", "ios"]
    roots = {Path(target.root).name for target in detection.targets}
    assert roots == {"android", "ios"}


def test_unknown_project_has_no_targets(tmp_path: Path) -> None:
    detection = detect_project(tmp_path)

    assert detection.platforms == []
    assert detection.targets == []


def test_detects_macos_from_sdkroot_not_asset_catalog_name(tmp_path: Path) -> None:
    root = tmp_path / "ambiguous"
    xcodeproj = root / "ReplyX.xcodeproj"
    appiconset = root / "App" / "Assets.xcassets" / "AppIcon.appiconset"
    xcodeproj.mkdir(parents=True)
    appiconset.mkdir(parents=True)
    (xcodeproj / "project.pbxproj").write_text(
        """
        {
          buildSettings = {
            SDKROOT = macosx;
            SUPPORTED_PLATFORMS = "macosx";
            PRODUCT_BUNDLE_IDENTIFIER = dev.local.ReplyX;
            ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;
            INFOPLIST_FILE = App/Resources/Info.plist;
          };
        }
        """,
        encoding="utf-8",
    )

    detection = detect_project(root)

    assert detection.platforms == ["macos"]
    target = detection.targets[0]
    assert target.platform == "macos"
    assert target.sdk_root == "macosx"
    assert target.bundle_identifier == "dev.local.ReplyX"
    assert target.app_icon_name == "AppIcon"
    assert target.app_icon_set_path == str(appiconset)


def test_detects_macos_from_macos_deployment_target(tmp_path: Path) -> None:
    root = tmp_path / "deployment"
    xcodeproj = root / "ReplyX.xcodeproj"
    xcodeproj.mkdir(parents=True)
    (xcodeproj / "project.pbxproj").write_text(
        """
        {
          buildSettings = {
            MACOSX_DEPLOYMENT_TARGET = 15.0;
            PRODUCT_BUNDLE_IDENTIFIER = dev.local.ReplyX;
            ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;
          };
        }
        """,
        encoding="utf-8",
    )

    detection = detect_project(root)

    assert detection.platforms == ["macos"]
    assert detection.targets[0].platform == "macos"
    assert "MACOSX_DEPLOYMENT_TARGET=15.0" in detection.targets[0].evidence


def test_appiconset_idiom_mac_overrides_conflicting_ios_settings(tmp_path: Path) -> None:
    root = tmp_path / "conflict"
    xcodeproj = root / "ReplyX.xcodeproj"
    appiconset = root / "App" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset"
    xcodeproj.mkdir(parents=True)
    appiconset.mkdir(parents=True)
    (xcodeproj / "project.pbxproj").write_text(
        """
        {
          buildSettings = {
            SDKROOT = iphoneos;
            PRODUCT_BUNDLE_IDENTIFIER = dev.local.ReplyX;
            ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;
          };
        }
        """,
        encoding="utf-8",
    )
    (appiconset / "Contents.json").write_text(
        """
        {
          "images": [
            { "idiom": "mac", "size": "512x512", "scale": "2x", "filename": "icon_512x512@2x.png" }
          ],
          "info": { "author": "xcode", "version": 1 }
        }
        """,
        encoding="utf-8",
    )

    detection = detect_project(root)

    assert detection.platforms == ["macos"]
    assert detection.targets[0].platform == "macos"
    assert detection.targets[0].warnings


def test_detect_json_includes_xcode_evidence(tmp_path: Path) -> None:
    root = tmp_path / "iosish"
    xcodeproj = root / "App.xcodeproj"
    xcodeproj.mkdir(parents=True)
    (xcodeproj / "project.pbxproj").write_text(
        """
        {
          buildSettings = {
            SDKROOT = iphoneos;
            SUPPORTED_PLATFORMS = "iphoneos iphonesimulator";
            PRODUCT_BUNDLE_IDENTIFIER = com.example.app;
            ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;
          };
        }
        """,
        encoding="utf-8",
    )

    payload = detect_project(root).to_dict()
    target = payload["targets"][0]

    assert target["platform"] == "ios"
    assert target["confidence"] >= 0.9
    assert target["bundle_identifier"] == "com.example.app"
    assert target["sdk_root"] == "iphoneos"


def test_detects_macos_from_appiconset_idiom_when_build_settings_are_missing(tmp_path: Path) -> None:
    root = tmp_path / "ambiguous"
    xcodeproj = root / "App.xcodeproj"
    appiconset = root / "Assets.xcassets" / "AppIcon.appiconset"
    xcodeproj.mkdir(parents=True)
    appiconset.mkdir(parents=True)
    (xcodeproj / "project.pbxproj").write_text("{}", encoding="utf-8")
    (appiconset / "Contents.json").write_text(
        """
        {
          "images": [
            { "idiom": "mac", "size": "512x512", "scale": "2x", "filename": "icon_512x512@2x.png" }
          ],
          "info": { "author": "xcode", "version": 1 }
        }
        """,
        encoding="utf-8",
    )

    detection = detect_project(root)

    assert detection.platforms == ["macos"]
    assert detection.targets[0].confidence >= 0.8
