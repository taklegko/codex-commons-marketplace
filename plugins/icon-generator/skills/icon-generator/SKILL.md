---
name: icon-generator
description: Use when creating, replacing, resizing, validating, diagnosing, or exporting app icon assets or website favicons for iOS, macOS, Android, Xcode, Android Studio, web projects, Icon Composer, Liquid Glass, launcher icons, favicons, AppIcon.appiconset, .icon bundles, or .icns resources.
---

# Icon Generator

Use this skill when the user asks Codex to create app icons, launcher icons, website favicons, Xcode AppIcon assets, Android Studio launcher assets, Icon Composer `.icon` bundles, or platform-ready icon sizes.

The bundled CLI package lives in `../../scripts` relative to this `SKILL.md`. When using this skill from another project, resolve the plugin root first and run commands with:

```bash
PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli ...
```

## Required Workflow

Use this mandatory checklist, modeled after hatch-pet run ownership:

1. Inspect active icon.
2. Prepare a run.
3. For current-icon edits, show and approve the Edit Brief.
4. Generate or record preview candidates.
5. Label variants against the Edit Brief.
6. Finalize QA and ask for design approval.
7. Create and show the apply plan.
8. Apply only after apply-plan approval.
9. Verify and show only the immutable applied preview.

1. Inspect first:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli detect --root <project-root> --json
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli active-icon --root <project-root> --json
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli inspect-xcode --root <project-root> --json
   ```

   Report detected platform, confidence, evidence, target name, bundle id, active app icon name, active icon set path, active master PNG, `SDKROOT`, `SUPPORTED_PLATFORMS`, and `MACOSX_DEPLOYMENT_TARGET` when present. Use `sourceOptions` from `active-icon`: only `active-current` is a valid default source for targeted current-icon edits. `IconSource/` and backup files are history/reference only and must not be offered as default candidates. Ignore backup/history paths when reasoning about the active icon.

2. Create a run:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli prepare-icon-run --root <project-root> --description "<brief>" --json
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli icon-job-status --run-dir <run-dir> --json
   ```

   Ask what the icon should show and whether the user has a reference before preparing the run. The run directory is the temporary source of truth and lives outside the repo by default. Do not reuse or overwrite preview paths. Do not create project-local folders for variants, contact sheets, source candidates, masks, or old generated images.

   For requests like "change the current app icon", "make this existing icon brighter", or other targeted edits of the icon visible in Xcode/Android Studio, use the current-icon flow:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli active-icon --root <project-root> --json
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli extract-current-master --root <project-root> --out <run-dir-or-cache-path>/current-active-master.png --json
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli prepare-icon-run --root <project-root> --description "<targeted edit>" --from-current --json
   ```

   Do not use `IconSource/`, `.icon-generator-backups/`, `.app-icon-generator-backups/`, DerivedData, build output, or old chat preview paths as the current icon source unless the user explicitly asks to restore or compare history.

   If the active source cannot be resolved and `sourceSelection.requiresUserChoice` is true, ask what icon the user wants or ask for an explicit reference. Do not fall back to old `IconSource/` files. Only show old generated sources when the user asks to compare or restore history.

   For targeted edits such as "slightly", "a bit", "чуть-чуть", "немного", "make this flatter", "less round", or "fix only this detail", treat the request as a localized edit, not a redesign. Before image generation, show the generated `editBrief` to the user and ask for confirmation. The brief contains requested changes, preserved elements, change intensity, target area, style constraints, and forbidden changes. If the user says "everything is perfect except X", then only `X` may change and the rest belongs in preserved elements.

   Before current-icon image generation, ask whether to make `1` quick conservative variant or `4` variants for comparison. Recommend `1` for small/localized edits and use it by default when the user does not ask for a choice set. Use `--variants 4` only when the user explicitly wants several options. New icon creation can still use four variants by default.

   Approve the brief only after explicit user confirmation:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli approve-edit-brief --run-dir <run-dir> --approval-note "<user confirmed edit brief>" --json
   ```

   Do not run `$imagegen` or `record-imagegen-result` before `approve-edit-brief`. The generated variants must differ only by the strength or interpretation of the requested local change. Preserve the current composition, palette, material, lighting, style, background, and symbol semantics. If the user asks for a style such as low poly, matte, glossy, flatter, or more angular, apply it only to the target area unless they explicitly ask to change the whole icon.

3. Image generation stage:

   Use the installed `$imagegen` skill for visual generation. Python/Pillow in this plugin is only for deterministic resize, crop/pad, QA, contact sheets, asset export, and validation.

   For visual edits involving glow, shadow, background color, color transitions, glossy materials, or tiny artifacts, use image generation/editing to create a clean master preview. Do not use PIL thresholding, median filters, local blur patches, row/column interpolation, manual masks, or color-classification retouching. If the user says "keep the composition, make the background black, keep the glow," the right path is a clean grounded master render, not low-level pixel surgery.

   Store temporary previews, crops, masks, QA sheets, and diagnostics in the run directory. Do not write temporary files into `App/Resources/IconSource` or app resource folders. Leave a final master in project resources only if the user explicitly wants a source artifact there.

   For each ready job, use the prompt file listed in `imagegen-jobs.json` and every listed input image with its role label. Record only the selected original `$CODEX_HOME/generated_images/.../ig_*.png` output:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli record-imagegen-result --run-dir <run-dir> --job-id <job-id> --source <generated-output.png> --json
   ```

   Never manually edit `imagegen-jobs.json` to mark a visual job complete.

   After recording a current-icon variant, label it against the approved Edit Brief before finalize:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli label-icon-variant --run-dir <run-dir> --job-id <job-id> --label matches-brief --note "<why>" --json
   ```

   Allowed labels are `matches-brief`, `minor-drift`, and `violates-preserved-elements`. Show these labels with the contact sheet. A variant labeled `violates-preserved-elements` must not be finalized or applied unless the user explicitly overrides that violation.

4. Glyph refinement stage:

   Use concrete visual terms when editing simple shapes:

   - `double tip`: split or forked end.
   - `inner kink`: sharp bend inside a curve.
   - `single tail`: one continuous terminal shape.
   - `comma readability`: still reads as a comma at small size.
   - `quote-like shape`: starts reading as quotation marks or vertical sticks.

   If the user says a tip is double, do not argue. Zoom into the area, create 2-4 alternatives, and preserve the symbol semantics. If the design is approved and only padding, border, full-bleed, canvas size, or edge pixels are wrong, queue a geometry/edge repair instead of redesigning:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli queue-icon-repairs --run-dir <run-dir> --scope geometry --note "<technical fix>" --json
   ```

   Do not use geometry/edge repairs for glow, shadows, color changes, glossy material, or cleanup of tiny visual artifacts. Use `--scope design` and generate a clean grounded preview.

5. QA and design approval stage:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli finalize-icon-run --run-dir <run-dir> --job-id <chosen-job-id> --json
   ```

   Show the immutable QA contact sheet path and prepared master path. Report size, edge pixels, corner RGBA, uniform border thickness, baked rounded tile risk, black/white field risk, full-bleed suitability, content bounds, contrast, and visible-frame risk. Ask exactly whether to apply this variant to app icon assets or make another preview correction. Do not continue until the user explicitly approves this exact design. Visual edit approval is not apply approval.

6. Apply plan and approval stage:

   Create a read-only plan:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli plan-apply --run-dir <run-dir> --platform auto --mode auto --json
   ```

   For targeted current-icon edits, `--platform auto` must mean the active app icon platform only. Do not add web favicon output just because the repo has a web target. Use `--platform web` only when the user explicitly asks for favicons.

   For a website favicon, use the same approval flow and choose the web platform:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli plan-apply --run-dir <run-dir> --platform web --json
   ```

   Web favicon apply writes final assets only: `favicon.ico`, `favicon-16x16.png`, `favicon-32x32.png`, `apple-touch-icon.png`, `android-chrome-192x192.png`, `android-chrome-512x512.png`, and `site.webmanifest` in the site's web public directory. It returns an HTML `<link>` snippet; do not edit HTML templates automatically unless the user explicitly approves that separate source-code change.

   If `runtimeIconOverrides` or `additionalIconResources` includes a resource such as `NotificationIcon.png`, stop and ask whether to include it. Only after approval, recreate the plan with:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli plan-apply --run-dir <run-dir> --platform auto --mode auto --include-runtime-resource NotificationIcon.png --json
   ```

   Show the exact file plan, warnings, backup policy, platform, mode, source SHA, and runtime resources. Explain backup as a safety copy of old active files before overwrite; it is written into the run directory outside the project, does not affect the active icon, and is not a source. Do not apply until the user approves the apply plan. Then create approval and apply:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli approve-apply --run-dir <run-dir> --apply-plan <run-dir>/apply-plan.json --approval-note "<user approval>" --json
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli apply --approval <approval path returned by approve-apply> --json
   ```

   `approve-apply` returns the actual approval path and also writes `approvals/apply-approval.json` as a stable path. Do not guess the approval filename; use the returned `approval` or `approvalStablePath`.

   Direct `generate-*` and `sync-runtime-icons` commands are disabled for normal use in v0.7.0. Do not use `--approved-source`. After apply, show only `appliedPreviewPath` from the apply result as the final image preview. That path is an immutable run `qa/` copy with a unique timestamped filename.

   Re-check the applied state when needed:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli verify-applied-icon --approval <approval.json> --json
   ```

   Use `postApplyVerification` to report whether the active asset matches the approved source export, whether `Contents.json` references existing files, and what rebuild/relaunch/cache steps are needed. If the chat preview looks stale or the user says the displayed image is wrong, run `verify-applied-icon` and show the new `appliedPreviewPath`; never reopen or display the mutable `AppIcon.appiconset/*.png` path.

7. macOS 26 / Liquid Glass stage:

   If the project is macOS with Xcode 26/macOS 26 target expectations, run:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli icon-composer-doctor --json
   ```

   If using Icon Composer, pass `--mode icon-composer` to `plan-apply`. For macOS targets with `MACOSX_DEPLOYMENT_TARGET >= 26.0`, `--mode auto` should select Icon Composer when `ictool` is ready. If `icon-composer-doctor` returns `installRequired=true` or a plan returns `requiresUserDecision=true`, stop before apply approval.

   Ask the user whether to install Icon Composer or use explicit legacy PNG output. If they agree to install, show the guided manual flow and wait for readiness:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli icon-composer-install-guide --json
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli wait-icon-composer-ready --timeout 300 --interval 5 --json
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli plan-apply --run-dir <run-dir> --platform macos --mode icon-composer --json
   ```

   Do not run `brew install` for the user. The install guide gives manual options: Xcode-bundled Icon Composer, standalone Icon Composer, or `brew install --cask icon-composer`. Continue only after `canUseIconComposer=true`, `ictoolReady=true`, and `liquidGlassReady=true`.

   If the user refuses Icon Composer, offer `plan-apply --mode legacy-appiconset` or stop. Legacy fallback must be explicit; auto mode must not silently downgrade macOS 26 Liquid Glass output to PNG assets.

   Icon Composer apply is not a PNG asset catalog write. The apply plan must show that it will:

   - create or update `AppIcon.icon` outside `Assets.xcassets`;
   - remove the same-name legacy `AppIcon.appiconset` after backup;
   - update `project.pbxproj` so the `.icon` is included in the target Resources build phase;
   - keep `icon.json` layer `position.scale` at `1` when the foreground PNG is already a full icon composition.

   If post-apply verification still sees a same-name legacy `.appiconset`, or the `.icon` is not referenced in the Xcode project Resources build phase, treat the apply as failed and do not claim the app icon is connected.

   Also verify Xcode navigator/resource-reference cleanliness after Icon Composer apply. There must be a single canonical `PBXFileReference` for `AppIcon.icon` with `path = AppIcon.icon` and `lastKnownFileType = folder.iconcomposer.icon`. A duplicate or root-relative reference such as `path = "App/Resources/AppIcon.icon"` is a failed post-apply verification and must be reported.

   Diagnostic to show when relevant: if a legacy PNG `AppIcon.appiconset` shows a light system frame on macOS 26, use an Icon Composer `.icon`; PNG transparency or inset changes are not a reliable fix.

8. Build inspect and runtime check:

   ```bash
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli inspect-built-app --app <path-to-app> --json
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli diagnose-macos-icon --root <project-root> --app <path-to-app> --json
   PYTHONPATH=<plugin-root>/scripts python3 -m app_icon_generator.cli find-running-apps --bundle-id <bundle-id> --json
   ```

   Before suggesting Dock, NotificationCenter, LaunchServices, or iconservices resets, report duplicate running `.app` paths with the same bundle id.

   For Icon Composer applies, build and inspect the compiled app icon before treating the result as production-ready. Check `.app/Contents/Resources/AppIcon.icns` with `inspect-built-app`; source `.icon` success alone is not enough when validating Xcode output.

## Safety Rules

- Never overwrite existing icons silently. Always pass `--backup` in real projects.
- Never use `--approved-source`; v0.7.0 requires an approval manifest created by `approve-apply`.
- Never run `approve-apply` or `apply` unless the user explicitly approved the exact immutable preview and exact apply plan.
- Never approve a plan with `applyBlocked=true` or `requiresUserDecision=true`; resolve the Icon Composer install/fallback decision first.
- A visual edit request is approval to make a preview, not approval to write platform assets.
- Visual edit approval is not apply approval; require both approvals.
- Edit Brief approval is separate from visual edit approval and apply approval; require all relevant approvals in order.
- Backups created by the run workflow are written to `<run-dir>/backups/`, outside the project; explain that they are safety copies, not active icons or source candidates. Legacy `.icon-generator-backups/` and `.app-icon-generator-backups/` folders are history only.
- Never use backup/history, `IconSource/`, DerivedData, build output, or old preview files as the current icon source for targeted edits.
- If a current app icon exists, start from that active icon. If it does not exist or cannot be identified, ask what icon the user wants; do not mine old generated sources.
- Do not write temporary diagnostic files, masks, crops, variants, contact sheets, or intermediate masters into app resources, project root, or `IconSource/`.
- Do not use low-level pixel retouching for visual style changes, glow/shadow edits, background color changes, or tiny visual artifacts; generate a clean preview instead.
- Show final previews only from `appliedPreviewPath` returned by `apply`.
- Never show final previews from mutable project PNGs, backups, `IconSource/`, `$CODEX_HOME/generated_images`, or old preview paths.
- Do not edit Xcode build settings, Info.plist, runtime icon code, or project files unless the user explicitly approves that change in the apply plan. Icon Composer mode may update `project.pbxproj` only to add the approved `.icon` resource and only after apply-plan approval.
- Do not edit website HTML templates automatically for favicons. Return the snippet and ask before changing source templates.
- Detect and report `NSApplication.shared.applicationIconImage` usage; do not remove it automatically.
- If `Bundle.main.url(forResource: ..., withExtension: "png")` points at an icon-like resource, treat it as a separate source of visible icon state.
- Do not mix design refinement, platform format changes, and runtime behavior changes in one step.
- After the user approves a design, do not change style, composition, palette, or symbol semantics unless the user asks for design changes.
- For "чуть-чуть" or other small current-icon edits, do not redesign. Create conservative localized variants and ask the user to choose or refine before planning apply.
- If generated variants drift from the brief, label them `minor-drift` or `violates-preserved-elements`; do not silently present them as valid.
- Do not claim that this plugin publishes to App Store Connect, Google Play, or external services.

## Outputs

Apple legacy generation writes clean `AppIcon.appiconset` folders with valid `Contents.json`.

Android generation writes legacy launcher PNGs and adaptive launcher icon resources.

Icon Composer generation creates an `.icon` bundle through `icon-composer-mcp`/`npx` or the local `ictool` fallback, connects it as an Xcode resource after approval, removes same-name legacy appiconset conflicts after backup, and reports whether Liquid Glass rendering prerequisites are available.
