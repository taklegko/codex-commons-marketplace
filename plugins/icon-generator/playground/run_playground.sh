#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORK_DIR="$SCRIPT_DIR/.work"
PYTHONPATH_VALUE="$REPO_ROOT/scripts"
export ICON_GENERATOR_CACHE_DIR="$WORK_DIR/cache"

cd "$REPO_ROOT"

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

cp -R tests/fixtures/android "$WORK_DIR/android"
cp -R tests/fixtures/ios "$WORK_DIR/ios"
cp -R tests/fixtures/macos "$WORK_DIR/macos"
cp -R tests/fixtures/flutter "$WORK_DIR/flutter"

PYTHONPATH="$PYTHONPATH_VALUE" python3 playground/make_master_icon.py "$WORK_DIR/master.png"

echo
echo "Visual QA"
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli qa-source --source "$WORK_DIR/master.png" --out "$WORK_DIR/qa" --json
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli make-contact-sheet --sources "$WORK_DIR/master.png" "$WORK_DIR/master.png" --out "$WORK_DIR/contact-sheet.png" --json

echo
echo "Detecting fixture projects"
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli detect --root "$WORK_DIR/android" --json
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli active-icon --root "$WORK_DIR/android" --platform android --json
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli detect --root "$WORK_DIR/ios" --json
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli detect --root "$WORK_DIR/macos" --json
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli detect --root "$WORK_DIR/flutter" --json
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli inspect-xcode --root "$WORK_DIR/macos" --no-xcodebuild --json

echo
echo "Applying icons through v4 run workflow"

run_workflow() {
  local root="$1"
  local platform="$2"
  local prepare_output run_dir plan_output approval_output approval_path

  prepare_output="$(PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli prepare-icon-run --root "$root" --description "Playground app icon" --variants 2 --json)"
  echo "$prepare_output"
  run_dir="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["runDir"])' <<<"$prepare_output")"

  PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli record-imagegen-result --run-dir "$run_dir" --job-id variant-1 --source "$WORK_DIR/master.png" --json
  PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli finalize-icon-run --run-dir "$run_dir" --job-id variant-1 --json
  plan_output="$(PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli plan-apply --run-dir "$run_dir" --platform "$platform" --mode legacy-appiconset --json)"
  echo "$plan_output"
  approval_output="$(PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli approve-apply --run-dir "$run_dir" --apply-plan "$run_dir/apply-plan.json" --approval-note "Playground fixture approved." --json)"
  echo "$approval_output"
  approval_path="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["approval"])' <<<"$approval_output")"
  PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli apply --approval "$approval_path" --json
  PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli active-icon --root "$root" --platform "$platform" --no-xcodebuild --json
  PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli extract-current-master --root "$root" --platform "$platform" --out "$run_dir/qa/current-active-master-after-apply.png" --json
}

run_workflow "$WORK_DIR/android" android
run_workflow "$WORK_DIR/ios" ios
run_workflow "$WORK_DIR/macos" macos
run_workflow "$WORK_DIR/flutter/android" android
run_workflow "$WORK_DIR/flutter/ios" ios

echo
echo "Icon Composer preflight"
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli icon-composer-doctor --json

echo
echo "Validating generated projects"
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli validate --root "$WORK_DIR/android" --json
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli validate --root "$WORK_DIR/ios" --json
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli validate --root "$WORK_DIR/macos" --json
PYTHONPATH="$PYTHONPATH_VALUE" python3 -m app_icon_generator.cli validate --root "$WORK_DIR/flutter" --json

echo
echo "Playground output: $WORK_DIR"
