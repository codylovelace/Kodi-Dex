# Kodi-Dex

A working copy of **Dex Hub** (`plugin.video.dexhub`) and its optional
**Dex Hub skin** (`skin.dexhub`) for Kodi, imported from the upstream
release so we can read, modify and rebuild it.

## Where this came from

| Component | Version | Upstream release asset | SHA-256 of the upstream zip |
|---|---|---|---|
| `plugin.video.dexhub` | 5.10.143 | [V5.10.143 / plugin.video.dexhub-5.10.143.zip](https://github.com/6ahd/plugin.video.dexhub/releases/tag/V5.10.143) | `3760cb42378247c666fd7db6a28f498b29ffb043f3f7b240bc0e5b8a8bd8d1c6` |
| `skin.dexhub` | 3.19.1 | [V5.10.143 / skin.dexhub-3.19.1.zip](https://github.com/6ahd/plugin.video.dexhub/releases/tag/V5.10.143) | `6aeaf2e97033932911099a46b67029c9c98d694e52b19f9a2efb1d1e781b141d` |

Notes on the import (verified 2026-10-08):

* The upstream **git source tree is stale**: `addon.xml` on its `main` branch
  (commit `990b253`) is still version **3.9.81**. The current code only exists
  in the release zips, so this repo is built from the zips, not from upstream git.
* Upstream's zip shipped a stray `plugin.video.dexhub/__pycache__/default.cpython-313.pyc`.
  It was dropped (build artifact, not source); everything else is byte-identical
  to the release zips.
* `scripts/build.py` rebuilds both zips; their contents were checked with
  `diff -r` against the extracted upstream zips and match exactly.

## Layout

```
plugin.video.dexhub/   the add-on (Python 3, ~272 .py files)
  default.py           plugin entry point
  service.py           background service (Home rows, sync, player info)
  resources/lib/       the bulk of the code (plugin.py, player_runtime.py, ...)
  resources/skins/     the add-on's own windows (used in any skin)
  resources/settings.xml
skin.dexhub/           the optional Dex Hub skin (Estuary based, Kodi 21+)
scripts/build.py       builds installable zips into dist/
```

## Building installable zips

```sh
python3 scripts/build.py                       # both add-ons
python3 scripts/build.py plugin.video.dexhub   # just the add-on
```

Output: `dist/<addon id>-<version>.zip` (the version comes from each
`addon.xml`). Install with **Kodi → Add-ons → Install from zip file**.
Bump `version` in `addon.xml` when you change something, or Kodi will treat
the zip as the version it already has installed.

> If the official DexWorld repository is installed, Kodi can auto-update the
> add-on and overwrite your build. Disable auto-update for Dex Hub
> (add-on info → Auto-update off), or give our build a higher version.

## Pulling in a newer upstream release

1. Download the new `plugin.video.dexhub-<ver>.zip` / `skin.dexhub-<ver>.zip`
   from the upstream releases page.
2. Extract them over the folders here on a branch, delete any `__pycache__`.
3. Review `git diff` and merge with our own changes.
4. Update the table above with the new versions and SHA-256s.

## License

Dex Hub is MIT licensed by 6ahd (see `LICENSE`). The skin keeps the licenses
of the work it builds on: CC BY-SA 4.0 (Estuary), CC BY-NC-SA 4.0 (Arctic
Fuse 3 player) and GPL 2.0. See `skin.dexhub/LICENSE.txt`, `skin.dexhub/LICENSE-AF3-PLAYER.txt` and the upstream
README for credits.
