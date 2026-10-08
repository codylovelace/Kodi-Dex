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

## Install on a Kodi box (auto-updates)

The repository is published at **https://codylovelace.github.io/Kodi-Dex/**
(GitHub Pages, rebuilt on every push to `main`).

1. **Settings → System → Add-ons**: turn on **Unknown sources**.
2. **Settings → File manager → Add source**: enter
   `https://codylovelace.github.io/Kodi-Dex/` and name it **Kodi-Dex**.
3. **Add-ons → Install from zip file → Kodi-Dex** →
   `repository.kodidex-1.0.0.zip`.
4. **Add-ons → Install from repository → Kodi-Dex Repository** →
   Video add-ons → **Dex Hub** (and, if you want it, Look and feel → Skin →
   **Dex Hub**).

After that Kodi updates both from this repository by itself.

**Already have Dex Hub from DexWorld?** Kodi only updates a third-party
add-on from the repository it was installed from (`xbmc/addons/AddonRepos.cpp`,
`CAddonRepos::DoAddonUpdateCheck`, Kodi 21: "we check for updates in the origin
repo only"). So after step 3, open **Dex Hub → Information → Versions**, pick
the `5.10.143.x` version from **Kodi-Dex Repository**, and repeat for the
skin. From then on the box follows this repository, and DexWorld can no
longer overwrite it. Your Dex Hub settings (`addon_data`) are kept.

**When updates arrive:** Kodi checks each repository every 24 hours
(`xbmc/addons/Repository.cpp`, default when the server sends no
`X-Kodi-Recheck-After`, which GitHub Pages cannot). To pull a change right
away: **Add-ons → Install from repository → Kodi-Dex Repository →** context
menu (long-press / `C`) **→ Check for updates**. Auto-update must be on
(Settings → System → Add-ons → Updates: *Install updates automatically*).

**One-time GitHub setup:** in the repository's **Settings → Pages → Build
and deployment**, set **Source** to **GitHub Actions**. The publish
workflow cannot switch that on itself.

## Making a change

1. Work on a branch; edit `plugin.video.dexhub/` or `skin.dexhub/`.
2. Raise the version, with a changelog line (Kodi only installs a *higher*
   version; CI fails if a changed add-on keeps its version):

   ```sh
   python3 scripts/bump.py plugin.video.dexhub "Fix: what changed"
   ```

   Our builds add a fourth number to the upstream version: upstream
   `5.10.143` → ours `5.10.143.1`, `5.10.143.2`, ... (Kodi orders
   `5.10.143.1` above `5.10.143`). Dex Hub only reads the first three
   numbers of a version (`resources/lib/player_badges.py`, `skin_outdated`),
   so the fourth is safe.
3. Open a pull request. **CI** lints, checks versions, builds the repository
   and verifies it, and attaches the zips (artifact `kodi-zips`) so you can
   test with *Install from zip file* before merging.
4. Merge into `main`. **Publish Kodi repository** builds, verifies, deploys
   to Pages, then verifies the live site. Boxes update on their next check.

## Measuring performance

Speed work here is measured on the box, not guessed. To capture a log:

1. On the box: **Settings → System → Logging → Enable debug logging** (on).
2. Restart Kodi, wait for the Home, open the Home, scroll through the rows,
   open a few grids, then go back to the Home once more.
3. Copy `kodi.log` (CoreELEC: `/storage/.kodi/temp/kodi.log`, or the
   `Logfiles` Samba share; Windows: `%APPDATA%\Kodi\kodi.log`).
4. Turn debug logging off again (it slows Kodi down a little).
5. `python3 scripts/analyze_log.py kodi.log`

For each Dex Hub call it reports the **total** time (Kodi's own
`CPythonInvoker` start/finish lines, which include starting a fresh
Python), Dex Hub's **own** time (its `"<action> in N ms"` debug lines), and
the **overhead** in between. It also shows how many calls ran at once, each
Home opening as one burst, the slowest calls, thread waits, and the
service's timings.

## Layout

```
plugin.video.dexhub/   the add-on (Python 3, ~272 .py files)
  default.py           plugin entry point
  service.py           background service (Home rows, sync, player info)
  resources/lib/       the bulk of the code (plugin.py, player_runtime.py, ...)
  resources/skins/     the add-on's own windows (used in any skin)
  resources/settings.xml
skin.dexhub/           the optional Dex Hub skin (Estuary based, Kodi 21+)
repository.kodidex/    the Kodi repository add-on (points at GitHub Pages)
scripts/
  build.py             zips, or the whole repository site (--site)
  bump.py              raise a version + changelog entry
  check_versions.py    CI: changed add-on => higher version
  lint.py              CI: Python 3.8 syntax, XML well-formed
  verify_site.py       CI: read a built or live site the way Kodi does
  analyze_log.py       where Dex Hub's time goes, from a Kodi debug log
  addons.py            shared helpers, incl. a port of Kodi's version ordering
.github/workflows/
  ci.yml               pull requests and branches
  publish.yml          main -> GitHub Pages
```

## Building by hand

```sh
python3 scripts/build.py                       # every add-on's zip into dist/
python3 scripts/build.py plugin.video.dexhub   # just one
python3 scripts/build.py --site                # the repository site, dist/site
python3 scripts/verify_site.py dist/site       # check it like Kodi would
```

The site layout follows Kodi 21's own code: add-on zips at
`repo/zips/<id>/<id>-<version>.zip` (`AddonInfoBuilder.cpp`), a `.sha256`
next to each (`Repository.cpp`, `<hashes>sha256</hashes>`), `addons.xml`
verified against `addons.xml.sha256` (`<checksum verify="sha256">`), and an
`index.html` whose links read `<a href="X">X</a>`, the only form Kodi's File
Manager lists (`xbmc/filesystem/HTTPDirectory.cpp`). Zips are reproducible
(fixed timestamps), so an unchanged add-on keeps the same hash.

`scripts/addons.py` ports Kodi's `CAddonVersion` ordering; it was checked
against all 81 ordering/equality assertions in Kodi's
`xbmc/addons/test/TestAddonVersion.cpp` (Omega branch) with no mismatch.

## Pulling in a newer upstream release

1. Download the new `plugin.video.dexhub-<ver>.zip` / `skin.dexhub-<ver>.zip`
   from the upstream releases page.
2. Extract them over the folders here on a branch, delete any `__pycache__`.
3. Review `git diff` and merge with our own changes.
4. Set the versions with `scripts/bump.py <addon> --to <upstream>.1 "Merged upstream <upstream>"`
   (e.g. `5.10.150.1`), so the box sees it as an update.
5. Update the table above with the new versions and SHA-256s.

## License

Dex Hub is MIT licensed by 6ahd (see `LICENSE`). The skin keeps the licenses
of the work it builds on: CC BY-SA 4.0 (Estuary), CC BY-NC-SA 4.0 (Arctic
Fuse 3 player) and GPL 2.0. See `skin.dexhub/LICENSE.txt`, `skin.dexhub/LICENSE-AF3-PLAYER.txt` and the upstream
README for credits.
