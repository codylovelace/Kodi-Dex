#!/usr/bin/env python3
"""Build installable Kodi zips, and optionally the Kodi repository site.

Usage:
    python3 scripts/build.py                     # zips for every add-on
    python3 scripts/build.py plugin.video.dexhub # zip for one add-on
    python3 scripts/build.py --site              # the full repository site
    python3 scripts/build.py --out somewhere/

Zips are written as <out>/<addon id>-<version>.zip with the add-on folder at
their root, which is the layout Kodi's "Install from zip file" expects.

--site writes the static site GitHub Pages serves (default dist/site):

    index.html                          File Manager source page (links the
                                        repository zip, nothing else)
    repository.kodidex-<ver>.zip        the zip you install from that source
    repo/addons.xml                     index Kodi reads for updates
    repo/addons.xml.sha256              its checksum (Kodi re-downloads the
                                        index only when this changes)
    repo/zips/<id>/<id>-<ver>.zip       each add-on, plus .sha256 beside it
    repo/zips/<id>/<asset paths>        icon / fanart / screenshots for the
                                        Kodi add-on browser

Paths follow Kodi 21's own code: zip path datadir/<id>/<id>-<version>.zip
(xbmc/addons/addoninfo/AddonInfoBuilder.cpp), per-zip hash file <zip>.<hash>
(xbmc/addons/Repository.cpp), and File Manager entries need link text equal
to the href (xbmc/filesystem/HTTPDirectory.cpp).
"""
import argparse
import hashlib
import html
import os
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from addons import ADDONS, REPOSITORY_ADDON, ROOT, addon_xml_path, read_addon  # noqa: E402

SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".swp")
SKIP_NAMES = {".DS_Store", "Thumbs.db"}
# Fixed timestamp for every zip entry, so rebuilding unchanged sources gives
# byte-identical zips (and unchanged sha256 files).
ZIP_DATE = (2020, 1, 1, 0, 0, 0)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def addon_files(addon_dir):
    for dirpath, dirnames, filenames in os.walk(addon_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES):
                continue
            yield os.path.join(dirpath, name)


def build_zip(addon, out_dir):
    addon_dir = os.path.join(ROOT, addon)
    addon_id, version = read_addon(addon)
    if addon_id != addon:
        sys.exit("%s/addon.xml declares id %r, expected %r" % (addon, addon_id, addon))
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "%s-%s.zip" % (addon_id, version))
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in addon_files(addon_dir):
            info = zipfile.ZipInfo(os.path.relpath(path, ROOT).replace(os.sep, "/"), ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (os.stat(path).st_mode & 0o777 | 0o100000) << 16
            with open(path, "rb") as handle:
                zf.writestr(info, handle.read())
            count += 1
    print("built %s (%d files)" % (os.path.relpath(target, ROOT), count))
    return target


def asset_paths(addon):
    """Paths named in <assets> (icon, fanart, screenshots, ...)."""
    root = ET.parse(addon_xml_path(addon)).getroot()
    for ext in root.iter("extension"):
        if ext.get("point") != "xbmc.addon.metadata":
            continue
        assets = ext.find("assets")
        if assets is None:
            continue
        for child in assets:
            if child.text and child.text.strip():
                yield child.text.strip()


def addons_xml(addons):
    """addons.xml: every add-on's <addon> element inside <addons>."""
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<addons>"]
    for addon in addons:
        with open(addon_xml_path(addon), encoding="utf-8") as handle:
            text = handle.read()
        start = text.find("<addon ")
        if start < 0:
            sys.exit("%s/addon.xml has no <addon> element" % addon)
        parts.append(text[start:].rstrip())
    parts.append("</addons>")
    data = "\n".join(parts) + "\n"
    ET.fromstring(data.encode("utf-8"))  # fail the build on malformed XML
    return data


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def index_html(title, links):
    # Kodi only lists <a href="X">X</a> entries (link text == href).
    rows = "\n".join('<a href="%s">%s</a><br>' % (html.escape(n, quote=True), html.escape(n))
                     for n in links)
    return ("<!DOCTYPE html>\n<html>\n<head><meta charset=\"utf-8\"><title>%s</title></head>\n"
            "<body>\n%s\n</body>\n</html>\n" % (html.escape(title), rows))


def build_site(out_dir):
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    zips_dir = os.path.join(out_dir, "repo", "zips")
    repo_zip = None
    for addon in ADDONS:
        addon_dir = os.path.join(zips_dir, addon)
        target = build_zip(addon, addon_dir)
        write_text(target + ".sha256", sha256_of(target) + "\n")
        for asset in asset_paths(addon):
            src = os.path.join(ROOT, addon, asset)
            if not os.path.isfile(src):
                sys.exit("%s/addon.xml names asset %r, which does not exist" % (addon, asset))
            dst = os.path.join(addon_dir, asset)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        if addon == REPOSITORY_ADDON:
            repo_zip = target
    data = addons_xml(ADDONS)
    write_text(os.path.join(out_dir, "repo", "addons.xml"), data)
    write_text(os.path.join(out_dir, "repo", "addons.xml.sha256"),
               hashlib.sha256(data.encode("utf-8")).hexdigest() + "\n")
    # The repository zip again at the top, for the File Manager source.
    name = os.path.basename(repo_zip)
    shutil.copy2(repo_zip, os.path.join(out_dir, name))
    write_text(os.path.join(out_dir, "index.html"), index_html("Kodi-Dex", [name]))
    # GitHub Pages: serve files as they are (no Jekyll processing).
    write_text(os.path.join(out_dir, ".nojekyll"), "")
    print("site ready in %s" % os.path.relpath(out_dir, ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("addons", nargs="*", metavar="ADDON",
                        help="add-on folder(s) to zip: %s (default: all)" % ", ".join(ADDONS))
    parser.add_argument("--site", action="store_true",
                        help="build the Kodi repository site instead of plain zips")
    parser.add_argument("--out", help="output folder (default dist/, or dist/site with --site)")
    args = parser.parse_args()
    if args.site:
        if args.addons:
            parser.error("--site always builds every add-on")
        build_site(os.path.abspath(args.out or os.path.join(ROOT, "dist", "site")))
        return
    unknown = [a for a in args.addons if a not in ADDONS]
    if unknown:
        parser.error("unknown add-on(s): %s" % ", ".join(unknown))
    for addon in args.addons or ADDONS:
        build_zip(addon, os.path.abspath(args.out or os.path.join(ROOT, "dist")))


if __name__ == "__main__":
    main()
