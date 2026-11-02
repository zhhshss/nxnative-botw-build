# nxnative BOTW build workspace

Public source-only build orchestration for Touch the Wild and the NXKeyboard plugin. It combines the pinned `devinhurry/touch-the-wild` implementation with newer `seedds/nxnative` changes and the reviewed `xbunax/nxnative` Windows build support. The Windows job compiles ARM64 iOS host objects without Nintendo game files, keys, RomFS, shaders or saves; the optional complete IPA job reads the owner's private inputs.

The Touch the Wild integration adds native scene aspect correction, independent UI proportions, native menu and map touch controls, and relative camera input. A local camera patch lets a new eligible finger take control without a position jump and clears ended or canceled owners. These features require freshly rewritten game modules; the old NXKeyboard plugin cannot add them to the old executable by itself.

## Verified cloud build

[Actions run 41344688605](https://github.com/zhhshss/nxnative-botw-build/actions/runs/41344688605) built the current Touch the Wild source successfully on 2026-11-01. Native input and UIKit checks, render/frame-generation compilation, source clocks, feedback ThreadSanitizer, legacy data import and graphics packaging passed. The delivered unsigned IPA is 41,456,369 bytes; its independent graphics ZIP is 2,212,361,936 bytes. Both passed authenticated pickup and integrity checks. The original 16,522,934,469-byte NXROM is unchanged. Actual app, graphics and data reuse was verified without requesting any artifact downloads. Physical iPad rendering and sustained frame rates remain untested.

For the current shader chain, the cold IPA step took 2 h 11 m 52 s; the final cache-backed step took 14 m 23 s. The new encrypted cache was saved successfully. These measurements depend on unchanged shader inputs and an available compatible cache.

Use `feat/upstream-framegen-graphics` for the next cached dispatch. The current cache belongs to that Actions branch; `fix/touch-harness-camera-probe` has the same implementation, but Actions does not share a sibling branch's cache automatically.

Earlier build measurements follow for the previous shader chain:

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

The current touch source is pinned at `c671dd0b27f4`, with frame generation, live launcher settings and independent graphics packages. iOS now uses Xcode 27.2 and chain `ca8b9c5b4e34`. `prepare_touch_upstream.py` verifies source hashes and restores the original NXROM reader, so existing imported game-data/DLC folders and the previously published `.nxrom` remain usable. The app and graphics ZIP are authenticated independently; `fetch_ipa.py --with-graphics` picks up compatible graphics once and otherwise reuses the local package. App-only pickup remains the default. This update requires a newly built graphics package; real/generated frame rates and sustained iPad behavior need device validation.


The 2026-11-01 render backport carries Touch the Wild's guarded identical-target continuation and compression-eligible feedback snapshots. Its **Memory transfer optimization** setting defaults on and can restore the previous policy for a fresh-process comparison. Necessary feedback copies remain. The original shader chain and `.nxrom` identity are retained; sustained iPad FPS requires device comparison. The build records both upstream commit IDs and exact source hashes in `render_pass_patch` provenance.

Set the secrets documented in `.github/workflows/build-ipa.yml` and set the `BUILD_IPA` repository variable to `true`. `LEGACY_GAME_DATA_JSON` must contain the descriptor of the existing NXROM. Dispatch **Touch the Wild Windows host and controls** on the intended branch. Its default `separate` mode writes the small app-only `out/BOTW-iPad-Touch.ipa` and an independent `out/BOTW-iPad-Touch.graphics.v1.zip`; `nxrom` selects the same current packaging path. App and graphics are published as separate authenticated artifacts. Current builds reuse the existing NXROM rather than republishing game data. The existing `IPA_ZIP_PASSWORD` secret supplies the encryption password.

Install the app IPA over the existing app with the same bundle identifier to preserve Documents. Select the new graphics ZIP through **Import shaders** once for this update. For the first installation using external data, select the existing `.nxrom` through **Import ROM** once. Already imported game-data and DLC directories remain usable. Later app builds with the same data identity and compatible graphics use the existing resources. Game/DLC content or graphics compatibility changes require their matching package. Frame generation is experimental, off by default and requires a supported GPU; on a 60 Hz display, the 30 FPS source setting with frame generation can target 60 displayed pictures.

If **Open** does not respond while running inside LiveContainer, enable its per-app **Fix File Picker**, disable **(Legacy) Fix File Picker**, and fully restart LiveContainer. Its modern fix corrects the system picker's host identity; the legacy fix copies selected files into Inbox and can duplicate these large packages. See the [LiveContainer implementation](https://github.com/LiveContainer/LiveContainer/blob/9108ae624ea001f7d7696d48407f890b48fa2653/TweakLoader/DocumentPicker.m). Device confirmation remains necessary; changing the IPA's allowed file types does not correct a container's host identity.

The current workflow does not publish another game-data artifact, including when `publish_game_data` is set; that input is retained for older dispatch compatibility. The app carries the existing data descriptor. For an owner who already has a complete IPA, `scripts/split_game_data.py` can generate the original-format `.nxrom` and an import-ready app IPA locally; the executable, frameworks and shaders are preserved byte for byte.

The reviewed `--ipa-ci` stage releases downloaded container copies synchronously after all game and DLC extraction succeeds. Reclamation requires the explicit CI flag and the runner's `RUNNER_TEMP/nsp` folder. Compile products remain available throughout Xcode's build.

Completed shaders and GPU pipeline archives are cached as an AES-256-GCM encrypted archive. The cache namespace covers game-file contents, shader sources and profiles, translator binaries, compiler/SDK versions, GPU and password rotation. Native build fingerprints still validate restored outputs. Translator fingerprints use binary contents, and compiler fingerprints exclude Metal's randomly mounted `InstalledDir`, so new checkouts and runner mounts can reuse identical results. A completed shader build can be cached even if a later Xcode or packing step fails. The first cache population still runs the complete shader build.

For a cache made by the earlier workflow, `LEGACY_METAL_COMPILER_ROOT` can identify its old `Metal.xctoolchain` mount. The workflow reconstructs the old key using every current input, authenticates the cache, validates the native shader fingerprint, and saves migrated outputs under the stable key. `Shader compiler cache diagnostics` records the compiler versions, paths and binary digests for investigating misses.

Pickup reads the Actions ZIP64 central directory with HTTP ranges, then streams the selected entry directly into one IPA. It verifies the entry CRC, authenticates AES-GCM ciphertext, verifies the IPA SHA256 against the successful build log, and checks its app entries before publishing the file. Reading the central directory avoids the ambiguous large data descriptors that make forward-only libarchive extraction fail above 4 GiB. The older encrypted ZIP format remains readable. It needs `gh`, `bsdtar` (`libarchive-tools` on Ubuntu), Python `cryptography`, and enough space for one IPA plus 512 MiB:

```sh
python3 scripts/fetch_ipa.py <successful-run-id> \
  --output-dir /path/to/downloads --password-file /path/to/ipa-password.txt
```

Current split builds default to `BOTW-iPad-Touch-App.ipa`. Add `--with-graphics` for the first graphics pickup, optionally `--graphics-dir /path/to/large-disk`; an existing compatible graphics ZIP is verified and reused. `--with-game-data --data-dir /path/to/existing/data` verifies and reuses the existing `BOTW-<version>-DLC.nxrom`. It can download a missing NXROM only from an older run that published a data artifact. Subsequent pickups without either flag request only the app artifact. Identical existing app, graphics and data files do not request another artifact download. Replacement app/graphics files are authenticated and validated before replacing previous files.

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
