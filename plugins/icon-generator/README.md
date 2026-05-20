# Icon Generator

Create app icons and favicons from inside Codex.

Icon Generator is a Codex plugin that turns a design brief or source image into
platform-ready icon assets for iOS, macOS, Android, Flutter, and web projects. It
detects the active icon in your project, prepares previews, asks for approval,
then writes the correct asset files only after you approve the final plan.

## What It Does

| Platform | Output |
| --- | --- |
| iOS | `AppIcon.appiconset` with valid `Contents.json` |
| macOS | legacy `AppIcon.appiconset` or Icon Composer `.icon` bundles |
| Android | launcher PNGs plus adaptive icon resources |
| Flutter | iOS and Android icon assets inside the Flutter project structure |
| Web | `favicon.ico`, PNG favicons, `apple-touch-icon.png`, and `site.webmanifest` |

Use it when you want Codex to:

- create a new app icon from a text brief;
- edit the current icon without losing the original composition;
- turn one image into iOS, macOS, Android, or web icon assets;
- generate favicons for a website;
- diagnose why a macOS app still shows an old or unexpected icon;
- create macOS 26 / Liquid Glass `.icon` output through Icon Composer.

## Use It In Codex

Install this repository as a Codex plugin, then ask Codex for the icon work you
want. Good prompts are concrete:

```text
Create app icons for this iOS project: a black comma on a clean white background.
```

```text
Generate Android launcher icons from this image.
```

```text
Make the current macOS icon a bit flatter, but keep the shape and colors.
```

```text
Generate favicons for this website.
```

Codex will use the bundled `icon-generator` skill automatically when the task is
about app icons, launcher icons, favicons, Xcode `AppIcon` assets, Android Studio
launcher assets, Icon Composer, or Liquid Glass icons.

## The Approval Flow

The plugin is designed to avoid silent overwrites. A normal run looks like this:

1. Codex detects the project type and current active icon.
2. You describe the icon or provide a source image.
3. Codex creates preview candidates.
4. You approve the exact preview you want.
5. Codex shows the apply plan with the files it will write.
6. You approve the apply plan.
7. Codex writes the assets, keeps backups outside your project, and verifies the result.

For edits to an existing icon, Codex starts from the active icon in the project.
It does not use old backups, generated previews, build output, or history folders
as the source unless you explicitly ask for that.

## What Gets Written

Icon Generator writes final platform assets only after approval.

For web projects, it writes:

- `favicon.ico`
- `favicon-16x16.png`
- `favicon-32x32.png`
- `apple-touch-icon.png`
- `android-chrome-192x192.png`
- `android-chrome-512x512.png`
- `site.webmanifest`

For Apple and Android projects, it writes the icon resources expected by the
detected platform. In Icon Composer mode, it can also update the Xcode project so
the approved `.icon` bundle is included in the app resources.

Temporary previews, QA sheets, run metadata, and backups live outside your app
project by default under:

```text
~/Library/Caches/icon-generator/runs/
```

## macOS 26 And Liquid Glass

For macOS targets that should use Liquid Glass icons, the plugin supports Icon
Composer `.icon` bundles.

It can check whether Icon Composer / `ictool` is available, guide installation if
needed, and avoid falling back to flat PNG assets unless you explicitly choose
that fallback. Node.js 18+ and `npx` are useful for the bundled
`icon-composer-mcp` integration.

## Requirements

- Codex with plugin support.
- Python 3.10+.
- Pillow, installed through the plugin package.
- Node.js 18+ when using the Icon Composer MCP integration.
- Icon Composer or Xcode 26 tools for macOS 26 / Liquid Glass `.icon` output.

## For Plugin Development

Clone the repository and install it locally:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[test]"
```

Run the test suite:

```bash
python3 -m pytest
```

The repository also includes a disposable playground:

```bash
bash playground/run_playground.sh
```

Generated playground output is written to `playground/.work/` and is ignored by
Git.

## CLI Reference

Most users should ask Codex to run the workflow for them. The CLI exists for the
plugin skill and for local development.

When running the CLI directly from this repository:

```bash
python3 -m app_icon_generator.cli detect --root . --json
python3 -m app_icon_generator.cli active-icon --root . --json
python3 -m app_icon_generator.cli icon-generator-doctor --json
python3 -m app_icon_generator.cli validate --root . --json
```

When using the plugin from another project, point `PYTHONPATH` at the bundled
package:

```bash
PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli detect --root <project-root> --json
```

The normal write workflow is:

```bash
PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli prepare-icon-run --root <project-root> --description "<brief>" --json
PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli record-imagegen-result --run-dir <run-dir> --job-id variant-1 --source <generated-output.png> --json
PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli finalize-icon-run --run-dir <run-dir> --job-id variant-1 --json
PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli plan-apply --run-dir <run-dir> --platform auto --mode auto --json
PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli approve-apply --run-dir <run-dir> --apply-plan <run-dir>/apply-plan.json --approval-note "Design and apply plan approved." --json
PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli apply --approval <approval path returned by approve-apply> --json
PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli verify-applied-icon --approval <approval path returned by approve-apply> --json
```

## Scope

V1 supports iOS, macOS, Android, Flutter icon assets, website favicons, and macOS
Icon Composer `.icon` output. It does not publish assets to App Store Connect,
Google Play, or external services.
