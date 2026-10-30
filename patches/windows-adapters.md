# Windows adapters for the Touch the Wild source stage

These adapters preserve the pinned xbunax Windows build hooks while retaining the
Touch the Wild source base and the reviewed seedds upstream merge.

- `windows-0002-gen-embed.patch` keeps POSIX assembly paths and
  `regular,no_dead_strip` sections. Its context includes the newer fourth `x2`
  section and `BACK_LEN` support; neither is removed.
- `windows-0005-build-ipa.patch` retains the progress event and optional RomFS ZIP
  hooks. It leaves the newer `.nxrom` path and upstream rewrite fingerprinting
  intact. `--separate-romfs` and `--separate-rom` select different archive formats
  and are rejected when enabled together.
- Original Windows hooks 1, 3, 4 and 6 are replayed with unique complete context.
- Original hooks 7–9 modify Swift launchers replaced by the pinned Objective-C
  launcher. They are recorded as superseded, not applied to the evolved Swift UI.

The generated provenance records the original patch, effective adapter,
regenerated diff, and before/after content identities for every applied hook.
The source stage keeps the current native startup and import callbacks. The
Windows object build does not compile Swift: `.nxrom` receipt validation/import,
the visual touch editor, and the cloud-save folder picker still require a real
Objective-C port or a Swift-capable final build. Missing bridge functions are
reported in provenance rather than replaced by success stubs. This stage is not
a linkable complete app.
