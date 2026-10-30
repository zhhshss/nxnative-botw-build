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

The intended final delivery is now a complete unsigned IPA for reinstall. Building it still requires the BOTW base game and 1.9.0 update, plus keys, outside this public repository. Existing saves must be backed up before replacement. The optional incremental tooling remains available, but exporting selected resources is no longer the preferred user workflow.

## Reproducibility

`source-pins.json` records both source commits. Tool archives are pinned by SHA-256, and staging records every applied patch and overlay. SDK/toolchain downloads are used only on the runner and excluded from artifacts. Host artifacts include source and license provenance; game data is never uploaded by this workflow.

Trigger **Touch the Wild Windows host and controls** from the Actions tab. The workflow has read-only repository permissions, no game-resource URL input and no secrets requirement. Its separate Mac job uses synthetic host inputs and a disposable iOS simulator, never a physical device.

## Licenses

The upstream and forks are MIT-licensed; their notices are retained in `UPSTREAM-LICENSE`, the staged checkout and artifact provenance. Third-party notices remain beside their sources and in the object artifact. No Apple SDK or Nintendo game content is redistributed in this repository.

## Resource export helper

NXKeyboard 0.3.2 can inspect the listed source resources in the installed app, show their size, and create a local ZIP only after the user selects Export. It does not upload anything. The artifact still targets the old installed DmIOS. The exported resource contents must not be committed to this public repository or uploaded by its public workflows.
