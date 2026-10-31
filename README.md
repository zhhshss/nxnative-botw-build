# nxnative BOTW build workspace

Public source-only build orchestration for Touch the Wild and the NXKeyboard plugin. It combines the pinned `devinhurry/touch-the-wild` implementation with newer `seedds/nxnative` changes and the reviewed `xbunax/nxnative` Windows build support. GitHub Actions compiles ARM64 iOS host objects without Nintendo game files, keys, RomFS, shaders or saves.

The Touch the Wild integration adds native scene aspect correction, independent UI proportions, native menu and map touch controls, and relative camera input. A local camera patch lets a new eligible finger take control without a position jump and clears ended or canceled owners. These features require freshly rewritten game modules; the old NXKeyboard plugin cannot add them to the old executable by itself.

## Verified cloud build

[Actions run 40924302938](https://github.com/zhhshss/nxnative-botw-build/actions/runs/40924302938) completed successfully on 2026-10-29: 181/181 host sources compiled, the plugin compiled and its signature pages verified, and input regression checks passed. The host object ZIP is approximately 4.6 MB. This is a compiler result, not device validation or a finished game update.

The successful run above predates the Touch the Wild integration and is not validation of the current source pins. The new workflow checks native aspect/input code and the production UIKit handlers separately from the Windows compilation. It does not validate gameplay on a physical iPad.

`resource-requirements.json` lists the source containers needed for a shader build. Their bytes are **not** in this repository. Game data stays private and is not an Actions input.

## Current scope

- The Windows job produces host object files and a relocatable, hashed inventory for later local linking.
- The plugin job builds NXKeyboard 0.3.2, including its existing touch/keyboard/mouse behavior, removal of the on-screen camera hint, and opt-in local resource export. This plugin still targets the older supported DmIOS binary; it must be re-adapted before use with a newly linked host.
- A successful host-object job is a compile check, **not a runnable game or completed IPA**. Shader generation, original-module rewriting, final linking, keyboard/mouse integration and device validation remain separate.
- Touch the Wild corrects the game projection and UI separately. On a narrower iPad screen, it retains the vertical field of view and shows less of the scene horizontally than at 16:9. Physical iPad acceptance is still needed.
- The Windows Objective-C launcher does not yet port Touch the Wild's SwiftUI visual layout editor or game-data import bridge. A host-object build cannot prove those final link/UI requirements complete.

The complete IPA workflow builds an unsigned IPA for reinstall using the owner's BOTW base game, 1.9.0 update, DLC and keys. The optional incremental tooling remains available.

## Complete IPA build and pickup

Set the secrets documented in `.github/workflows/build-ipa.yml` and set the `BUILD_IPA` repository variable to `true`. Dispatch **Touch the Wild Windows host and controls** on the intended branch. Its IPA job restores the iOS SwiftUI launcher and shader tools, writes `out/BOTW-iPad-Touch.ipa`, and publishes to the owner's R2 storage or as a password-protected Actions artifact.

The reviewed `--ipa-ci` stage releases downloaded container copies synchronously after all game and DLC extraction succeeds. Reclamation requires the explicit CI flag and the runner's `RUNNER_TEMP/nsp` folder. Compile products remain available throughout Xcode's build.

Completed shaders and GPU pipeline archives are cached as an AES-256-GCM encrypted archive. The cache namespace covers game-file contents, shader sources and profiles, translator binaries, compiler/SDK versions, GPU and password rotation. Native build fingerprints still validate restored outputs. Translator fingerprints use binary contents so a fresh checkout timestamp does not force recompilation. A completed shader build can be cached even if a later Xcode or packing step fails. The previous cold build spent about 77 minutes on iOS shaders and 17 minutes on pipelines; a later build can reuse those results. The first cache population still runs the complete shader build.

Pickup streams the outer Actions ZIP and encrypted inner ZIP directly into one IPA, verifies its SHA256 against the successful build log, and checks its app entries before publishing the file. It needs `gh`, `bsdtar` (`libarchive-tools` on Ubuntu), and enough space for one IPA plus 512 MiB:

```sh
python3 scripts/fetch_ipa.py <successful-run-id> \
  --output-dir /path/to/downloads --password-file /path/to/ipa-password.txt
```

The optional `--token-file` and `--public-base` arguments print a link for an existing owner-controlled file server. Game containers, RomFS and keys are excluded from the shader cache.

## Reproducibility

`source-pins.json` records both source commits. Tool archives are pinned by SHA-256, and staging records every applied patch and overlay. SDK/toolchain downloads are used only on the runner and excluded from artifacts. Host artifacts include source and license provenance. The complete IPA job returns the owner's game data through their configured storage or an encrypted artifact.

Trigger **Touch the Wild Windows host and controls** from the Actions tab. The compile and control checks use public sources, synthetic host inputs and a disposable iOS simulator. The complete IPA job runs on an explicit dispatch when `BUILD_IPA` is enabled and reads the owner's inputs from repository secrets.

## Licenses

The upstream and forks are MIT-licensed; their notices are retained in `UPSTREAM-LICENSE`, the staged checkout and artifact provenance. Third-party notices remain beside their sources and in the object artifact. No Apple SDK or Nintendo game content is redistributed in this repository.

## Resource export helper

NXKeyboard 0.3.2 can inspect the listed source resources in the installed app, show their size, and create a local ZIP only after the user selects Export. It does not upload anything. The artifact still targets the old installed DmIOS. The exported resource contents must not be committed to this public repository or uploaded by its public workflows.
