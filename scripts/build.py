#!/usr/bin/env python3
"""Build installable Kodi zips for the add-ons in this repository.

Usage:
    python3 scripts/build.py                 # build every add-on
    python3 scripts/build.py plugin.video.dexhub
    python3 scripts/build.py --out somewhere/

Each zip is written as dist/<addon id>-<version>.zip with the add-on folder
at its root, which is the layout Kodi's "Install from zip file" expects.
The version is read from the add-on's addon.xml.
"""
import argparse
import os
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDONS = ("plugin.video.dexhub", "skin.dexhub")
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".swp")
SKIP_NAMES = {".DS_Store", "Thumbs.db"}


def addon_version(addon_dir):
    root = ET.parse(os.path.join(addon_dir, "addon.xml")).getroot()
    return root.get("id"), root.get("version")


def build(addon, out_dir):
    addon_dir = os.path.join(ROOT, addon)
    addon_id, version = addon_version(addon_dir)
    if addon_id != addon:
        sys.exit("%s/addon.xml declares id %r, expected %r" % (addon, addon_id, addon))
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "%s-%s.zip" % (addon_id, version))
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(addon_dir):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                if name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES):
                    continue
                path = os.path.join(dirpath, name)
                zf.write(path, os.path.relpath(path, ROOT).replace(os.sep, "/"))
                count += 1
    print("built %s (%d files)" % (os.path.relpath(target, ROOT), count))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("addons", nargs="*", metavar="ADDON",
                        help="add-on folder(s) to build: %s (default: all)" % ", ".join(ADDONS))
    parser.add_argument("--out", default=os.path.join(ROOT, "dist"))
    args = parser.parse_args()
    unknown = [a for a in args.addons if a not in ADDONS]
    if unknown:
        parser.error("unknown add-on(s): %s" % ", ".join(unknown))
    for addon in args.addons or ADDONS:
        build(addon, os.path.abspath(args.out))


if __name__ == "__main__":
    main()
