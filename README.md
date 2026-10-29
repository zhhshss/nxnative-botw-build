# nxnative BOTW build workspace

Public source-only build orchestration for a pinned nxnative host and the NXKeyboard plugin. It combines the specified seedds upstream revision with the xbunax Windows build support and Objective-C launcher. GitHub Actions compiles ARM64 iOS host objects without Nintendo game files, keys, RomFS, shaders or saves.

## Verified cloud build

[Actions run 40924302938](https://github.com/zhhshss/nxnative-botw-build/actions/runs/40924302938) completed successfully on 2026-10-29: 181/181 host sources compiled, the plugin compiled and its signature pages verified, and input regression checks passed. The host object ZIP is approximately 4.6 MB. This is a compiler result, not device validation or a finished game update.

`resource-requirements.json` lists the source containers needed for the next shader build. Their bytes are **not** in this repository. The installed app's game data stays private and is not an Actions input. Native aspect and full HUD adaptation still need game resource inspection.

## Current scope

- The Windows job produces host object files and a relocatable, hashed inventory for later local linking.
- The plugin job builds NXKeyboard 0.3.1, including its existing touch/keyboard/mouse behavior and removal of the on-screen camera hint. This plugin still targets the older supported DmIOS binary; it must be re-adapted before use with a newly linked host.
- A successful host-object job is a compile check, **not a runnable game or completed update IPA**. Shader generation, original-module rewriting, final linking, plugin target adaptation and device validation remain separate.
- Native aspect-ratio rendering with an undistorted, complete HUD is not implemented by these builds. Existing view scaling cannot provide that feature.

The intended final delivery is a file-level update: replace changed executable/runtime/shader files while keeping the user's existing game resources and saves. Do not import a resource-free IPA over an app that holds its only RomFS copy inside its bundle.

## Reproducibility

`source-pins.json` records both source commits. Tool archives are pinned by SHA-256, and staging records every applied patch and overlay. SDK/toolchain downloads are used only on the runner and excluded from artifacts. Host artifacts include source and license provenance; game data is never uploaded by this workflow.

Trigger **Windows host and plugin** from the Actions tab. The workflow has read-only repository permissions, no game-resource URL input and no secrets requirement.

## Licenses

The upstream and Windows fork are MIT-licensed by seedds; their notice is retained in `UPSTREAM-LICENSE`. Their third-party source/license files remain in the staged checkout and the artifact's notices. No Apple SDK or Nintendo content is redistributed in this repository.
