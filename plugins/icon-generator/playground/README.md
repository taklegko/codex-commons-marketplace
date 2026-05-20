# Icon Generator Playground

This playground tests the plugin locally without adding it to any marketplace.

It copies fixture projects into `playground/.work/`, creates a sample `master.png`, runs the v5 run/approval/apply workflow for iOS, macOS, and Android icons, extracts the active icon after apply, then validates the output.
It also runs source QA, creates contact sheets, inspects active icon state, Xcode settings, and Icon Composer prerequisites.
The playground creates approval manifests automatically because it operates only on disposable fixture copies.

## Run

From the repository root:

```bash
bash playground/run_playground.sh
```

Or from this directory:

```bash
bash run_playground.sh
```

## Output

The script creates:

```text
playground/.work/
  master.png
  qa/
  contact-sheet.png
  cache/
  android/
  ios/
  macos/
  flutter/
```

The `.work` directory is ignored by git. Delete it any time to reset the playground:

```bash
rm -rf playground/.work
```

## Manual CLI Checks

After running the playground, inspect individual outputs:

```bash
PYTHONPATH=scripts python3 -m app_icon_generator.cli validate --root playground/.work/android --json
PYTHONPATH=scripts python3 -m app_icon_generator.cli validate --root playground/.work/ios --json
PYTHONPATH=scripts python3 -m app_icon_generator.cli validate --root playground/.work/macos --json
PYTHONPATH=scripts python3 -m app_icon_generator.cli detect --root playground/.work/flutter --json
PYTHONPATH=scripts python3 -m app_icon_generator.cli active-icon --root playground/.work/macos --json
```
