#!/usr/bin/env python3
"""Walk a Kodi-Dex repository site the way Kodi does, and fail on anything
Kodi would reject.

Usage:
    python3 scripts/verify_site.py dist/site                 # a local build
    python3 scripts/verify_site.py https://codylovelace.github.io/Kodi-Dex/

Checks, in Kodi's order:
  1. index.html lists the repository zip as <a href="X">X</a> (File Manager)
  2. that zip holds repository.kodidex/addon.xml; its <info>, <checksum> and
     <datadir> URLs are read from it (for a local folder, they are mapped
     onto the folder)
  3. addons.xml's sha256 equals the checksum file
  4. every add-on in addons.xml has datadir/<id>/<id>-<version>.zip, its
     .sha256 matches, the zip's addon.xml has that id and version, and every
     <assets> file is present beside it
"""
import hashlib
import io
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

LINK = re.compile(r'<a href="([^"]*)"[^>]*>\s*(.*?)\s*</a>(.+?)(?=<a|</tr|$)', re.I | re.S)


class Site:
    def __init__(self, base):
        self.local = not re.match(r"^https?://", base)
        self.base = base if self.local else base.rstrip("/") + "/"
        self.published_base = None  # the https base named by the repo add-on

    def get(self, ref):
        if self.local:
            path = ref
            if self.published_base and ref.startswith(self.published_base):
                path = ref[len(self.published_base):]
            with open(os.path.join(self.base, path), "rb") as handle:
                return handle.read()
        url = ref if re.match(r"^https?://", ref) else urllib.parse.urljoin(self.base, ref)
        # Cache-busting query so a just-finished deploy is what we check.
        sep = "&" if "?" in url else "?"
        request = urllib.request.Request(url + sep + "t=%d" % time.time(),
                                         headers={"User-Agent": "Kodi-Dex verify_site"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()


def fail(message):
    sys.exit("FAIL: " + message)


def first_line(data):
    return data.decode("utf-8").strip().split()[0].lower()


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    site = Site(sys.argv[1])

    # 1. File Manager page
    page = site.get("index.html").decode("utf-8")
    zips = [m.group(1) for m in LINK.finditer(page)
            if m.group(1) == m.group(2) and m.group(1).endswith(".zip")]
    repo_zips = [z for z in zips if z.startswith("repository.kodidex-")]
    if len(repo_zips) != 1:
        fail("index.html should list exactly one repository.kodidex zip, found %r" % zips)
    print("ok  index.html lists %s" % repo_zips[0])

    # 2. Repository add-on
    with zipfile.ZipFile(io.BytesIO(site.get(repo_zips[0]))) as zf:
        repo_xml = ET.fromstring(zf.read("repository.kodidex/addon.xml"))
    ext = [e for e in repo_xml.iter("extension") if e.get("point") == "xbmc.addon.repository"]
    if not ext:
        fail("repository addon.xml has no xbmc.addon.repository extension")
    dirs = ext[0].findall("dir")
    if len(dirs) != 1:
        fail("expected one <dir> in the repository add-on, found %d" % len(dirs))
    info, checksum, datadir = (dirs[0].findtext(t, "").strip() for t in ("info", "checksum", "datadir"))
    if dirs[0].findtext("hashes", "").strip().lower() != "sha256":
        fail("<hashes> should be sha256")
    if not datadir.endswith("/"):
        fail("<datadir> must end with / (%s)" % datadir)
    if site.local:
        site.published_base = info[:info.index("repo/addons.xml")]
    print("ok  repository add-on %s points at %s" % (repo_xml.get("version"), info))

    # 3. Index and its checksum
    index = site.get(info)
    expected = first_line(site.get(checksum))
    actual = hashlib.sha256(index).hexdigest()
    if actual != expected:
        fail("addons.xml sha256 %s, checksum file says %s" % (actual, expected))
    addons = ET.fromstring(index).findall("addon")
    print("ok  addons.xml (%d add-ons) matches its sha256" % len(addons))

    # 4. Every add-on
    for addon in addons:
        aid, ver = addon.get("id"), addon.get("version")
        zip_ref = "%s%s/%s-%s.zip" % (datadir, aid, aid, ver)
        data = site.get(zip_ref)
        want = first_line(site.get(zip_ref + ".sha256"))
        if hashlib.sha256(data).hexdigest() != want:
            fail("%s does not match its .sha256" % zip_ref)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            bad = zf.testzip()
            if bad:
                fail("%s: corrupt entry %s" % (zip_ref, bad))
            names = zf.namelist()
            if any(not n.startswith(aid + "/") for n in names):
                fail("%s has files outside %s/" % (zip_ref, aid))
            inner = ET.fromstring(zf.read(aid + "/addon.xml"))
        if (inner.get("id"), inner.get("version")) != (aid, ver):
            fail("%s holds %s %s" % (zip_ref, inner.get("id"), inner.get("version")))
        assets = [a.text.strip() for e in addon.iter("extension")
                  if e.get("point") == "xbmc.addon.metadata"
                  for x in e.findall("assets") for a in x if a.text and a.text.strip()]
        for asset in assets:
            site.get("%s%s/%s" % (datadir, aid, asset))
        print("ok  %s %s (%d files, %d assets)" % (aid, ver, len(names), len(assets)))
    print("site OK")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        fail("%s: %s" % (type(exc).__name__, exc))
