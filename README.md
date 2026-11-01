# nxnative BOTW build workspace

Public source-only build orchestration for Touch the Wild and the NXKeyboard plugin. It combines the pinned `devinhurry/touch-the-wild` implementation with newer `seedds/nxnative` changes and the reviewed `xbunax/nxnative` Windows build support. The Windows job compiles ARM64 iOS host objects without Nintendo game files, keys, RomFS, shaders or saves; the optional complete IPA job reads the owner's private inputs.

The Touch the Wild integration adds native scene aspect correction, independent UI proportions, native menu and map touch controls, and relative camera input. A local camera patch lets a new eligible finger take control without a position jump and clears ended or canceled owners. These features require freshly rewritten game modules; the old NXKeyboard plugin cannot add them to the old executable by itself.

## Verified cloud build

[Actions run 41262288572](https://github.com/zhhshss/nxnative-botw-build/actions/runs/41262288572) completed successfully on 2026-11-01: source regressions, Windows host/plugin compilation, native aspect/input and production UIKit handler tests, and the complete BOTW IPA build all passed. Restored shader outputs passed their native fingerprints; all 19,592 shader outputs were reused in 57 seconds. The IPA was built from the owner's base game, 1.9.0 update and both DLC containers. Physical iPad gameplay remains untested.

| Measured duration | Cold run 41230668664 | Cache run 41262288572 |
| --- | --- | --- |
| Complete workflow, including downloads, cache handling, encryption and upload | 2 h 55 m 23 s | 52 m 10 s |
| Build the IPA step | 2 h 34 m 37 s | 26 m 14 s |
| iOS shader step | 1 h 41 m 11 s | 57 s, existing outputs reused |

The cache run includes migration and resaving of the earlier cache. The stable cache is now published, and the temporary migration variable has been removed. These measurements apply while shader inputs remain unchanged and the compatible cache is available.

`resource-requirements.json` lists the source containers needed for a shader build. Their bytes are **not** in this repository. Game data stays private and is not an Actions input.

## Current scope

- The Windows job produces host object files and a relocatable, hashed inventory for later local linking.
- The plugin job builds NXKeyboard 0.3.2, including its existing touch/keyboard/mouse behavior, removal of the on-screen camera hint, and opt-in local resource export. This plugin still targets the older supported DmIOS binary; it must be re-adapted before use with a newly linked host.
- A successful host-object job is a compile check, **not a runnable game or completed IPA**. Shader generation, original-module rewriting, final linking, keyboard/mouse integration and device validation remain separate.
- Touch the Wild corrects the game projection and UI separately. On a narrower iPad screen, it retains the vertical field of view and shows less of the scene horizontally than at 16:9. Physical iPad acceptance is still needed.
- The Windows Objective-C launcher does not yet port Touch the Wild's SwiftUI visual layout editor or game-data import bridge. A host-object build cannot prove those final link/UI requirements complete.

The complete IPA workflow builds an unsigned IPA for reinstall using the owner's BOTW base game, 1.9.0 update, DLC and keys. The optional incremental tooling remains available.

## Complete IPA build and pickup

Set the secrets documented in `.github/workflows/build-ipa.yml` and set the `BUILD_IPA` repository variable to `true`. Dispatch **Touch the Wild Windows host and controls** on the intended branch. Its default `nxrom` mode uses the original in-app importer: it writes an app-only `out/BOTW-iPad-Touch.ipa` and a separate `out/BOTW-iPad-Touch.nxrom` containing the game/update/DLC. The two outputs are published as independent authenticated artifacts, so fetching an app update does not fetch the large data package. `bundle` still builds a complete IPA, and `separate` is the manual RomFS-transfer mode. The existing `IPA_ZIP_PASSWORD` secret supplies the encryption password.

Install the app IPA, open it and select its matching `.nxrom` once. The importer verifies and stores the data in the app's Documents directory. Later app builds with the same data SHA256 use the existing installation; settings or app-code changes do not require another data download. Install an update over the existing app with the same bundle identifier to preserve Documents. Game or DLC content changes produce a new data identity and require the matching package.

`publish_game_data=false` omits the separate data upload when only an app update is needed. The app still carries its required data descriptor. For an owner who already has a complete IPA, `scripts/split_game_data.py` can generate the original-format `.nxrom` and an import-ready app IPA locally; the executable, frameworks and shaders are preserved byte for byte.

The reviewed `--ipa-ci` stage releases downloaded container copies synchronously after all game and DLC extraction succeeds. Reclamation requires the explicit CI flag and the runner's `RUNNER_TEMP/nsp` folder. Compile products remain available throughout Xcode's build.

Completed shaders and GPU pipeline archives are cached as an AES-256-GCM encrypted archive. The cache namespace covers game-file contents, shader sources and profiles, translator binaries, compiler/SDK versions, GPU and password rotation. Native build fingerprints still validate restored outputs. Translator fingerprints use binary contents, and compiler fingerprints exclude Metal's randomly mounted `InstalledDir`, so new checkouts and runner mounts can reuse identical results. A completed shader build can be cached even if a later Xcode or packing step fails. The first cache population still runs the complete shader build.

For a cache made by the earlier workflow, `LEGACY_METAL_COMPILER_ROOT` can identify its old `Metal.xctoolchain` mount. The workflow reconstructs the old key using every current input, authenticates the cache, validates the native shader fingerprint, and saves migrated outputs under the stable key. `Shader compiler cache diagnostics` records the compiler versions, paths and binary digests for investigating misses.

Pickup reads the Actions ZIP64 central directory with HTTP ranges, then streams the selected entry directly into one IPA. It verifies the entry CRC, authenticates AES-GCM ciphertext, verifies the IPA SHA256 against the successful build log, and checks its app entries before publishing the file. Reading the central directory avoids the ambiguous large data descriptors that make forward-only libarchive extraction fail above 4 GiB. The older encrypted ZIP format remains readable. It needs `gh`, `bsdtar` (`libarchive-tools` on Ubuntu), Python `cryptography`, and enough space for one IPA plus 512 MiB:

```sh
python3 scripts/fetch_ipa.py <successful-run-id> \
  --output-dir /path/to/downloads --password-file /path/to/ipa-password.txt
```

For `nxrom` builds this defaults to `BOTW-iPad-Touch-App.ipa`. Add `--with-game-data` for the first pickup, optionally `--data-dir /path/to/large-disk`; the matching `BOTW-<version>-DLC.nxrom` is checked by SHA256 and reused if already present, including when that run publishes only an app. Subsequent pickups without this flag request only the app artifact. Repeated pickups of an identical existing app/data pair do not request another artifact download. A newer split app replaces the previous file only after authentication, SHA256 and IPA structure verification succeed; failed transfers preserve the previous app.

```sh
python3 scripts/split_game_data.py /path/to/complete.ipa \
  --source /path/to/prepared/source \
  --work /path/to/large-disk/split-work \
  --aoc-work /path/to/other-disk/split-aoc \
  --data-output /path/to/large-disk/BOTW-1.9.0-DLC.nxrom \
  --app-output /path/to/downloads/BOTW-iPad-Touch-App.ipa
```

To repack an app using an existing data descriptor, use `--metadata /path/to/BOTW-1.9.0-DLC.json` with `--app-output`; this path never opens the IPA's game-data entries. Temporary RomFS/DLC extraction can be removed after both outputs are verified.

The optional `--token-file` and `--public-base` arguments print a link for an existing owner-controlled file server. Game containers, RomFS and keys are excluded from the shader cache.

`scripts/serve_owner_files.py` provides the token-protected file server for build inputs and IPA downloads. It supports HEAD, suffix ranges, bounded ranges and resumed downloads. The token is read from a file. On the owner's server the `botw-feed.service` user service supervises it independently of the coding session.

## Reproducibility

`source-pins.json` records both source commits. Tool archives are pinned by SHA-256, and staging records every applied patch and overlay. SDK/toolchain downloads are used only on the runner and excluded from artifacts. Host artifacts include source and license provenance. The complete IPA job returns the owner's game data through their configured storage or an encrypted artifact.

Trigger **Touch the Wild Windows host and controls** from the Actions tab. The compile and control checks use public sources, synthetic host inputs and a disposable iOS simulator. The complete IPA job runs on an explicit dispatch when `BUILD_IPA` is enabled and reads the owner's inputs from repository secrets.

## Licenses

The upstream and forks are MIT-licensed; their notices are retained in `UPSTREAM-LICENSE`, the staged checkout and artifact provenance. Third-party notices remain beside their sources and in the object artifact. No Apple SDK or Nintendo game content is redistributed in this repository.

## Resource export helper

NXKeyboard 0.3.2 can inspect the listed source resources in the installed app, show their size, and create a local ZIP only after the user selects Export. It does not upload anything. The artifact still targets the old installed DmIOS. The exported resource contents must not be committed to this public repository or uploaded by its public workflows.
