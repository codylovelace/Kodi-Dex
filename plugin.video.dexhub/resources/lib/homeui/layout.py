# -*- coding: utf-8 -*-
"""The user's own arrangement of the Dex Hub Home rows (v5.10.98).

Each page ('all' is the Home, then 'movie' and 'series') keeps, in
<profile>/homeui/layout.json:

  order   the row keys in the order the user put them;
  hidden  row key -> true (hidden) or false (shown on purpose, for a row
          that starts hidden such as Next Up);
  custom  rows the user added: a Dex Hub listing (a source catalog, a Plex,
          Emby, Jellyfin or Silo library view) or a collection.

Row keys are the Home's own ('continue', 'nextup', 'cat:...', 'col:...'), and
'my:<id>' for added rows. A row the saved order does not know yet (a catalog
added to Nuvio later) is placed right after the row it follows in the Home's
own order, so nothing new is buried at the bottom.

This module never touches the GUI; the editor is layout_window.py.
"""
import json
import os
import threading
import time
import uuid

PAGES = ('all', 'movie', 'series')


def page_key(media):
    media = str(media or 'all')
    return media if media in PAGES else 'all'


def custom_key(spec):
    return 'my:%s' % (spec or {}).get('id', '')


def new_custom(spec):
    spec = dict(spec or {})
    spec['id'] = uuid.uuid4().hex[:10]
    spec['added'] = int(time.time())
    return spec


def _hidden(row, hidden):
    value = hidden.get(row.key)
    if value is None:
        return bool((row.meta or {}).get('default_hidden'))
    return bool(value)


def arrange(rows, page):
    """[(row, hidden)] for every row, in the saved order."""
    rows = list(rows or [])
    order = [k for k in (page or {}).get('order') or [] if k]
    hidden = (page or {}).get('hidden') or {}
    if not order:
        return [(row, _hidden(row, hidden)) for row in rows]
    position = {}
    for index, key in enumerate(order):
        position.setdefault(key, index)
    out = sorted([r for r in rows if r.key in position], key=lambda r: position[r.key])
    placed = set(r.key for r in out)
    for index, row in enumerate(rows):
        if row.key in placed:
            continue
        # after the nearest earlier row (in the Home's own order) already placed
        at = 0
        for previous in reversed(rows[:index]):
            if previous.key in placed:
                at = next(i for i, r in enumerate(out) if r.key == previous.key) + 1
                break
        out.insert(at, row)
        placed.add(row.key)
    return [(row, _hidden(row, hidden)) for row in out]


def _skin_follows():
    """skin.dexhub publishes its rows again after a change (v5.10.109)."""
    try:
        import xbmc
        if xbmc.getSkinDir() == 'skin.dexhub':
            import xbmcgui
            xbmcgui.Window(10000).setProperty('dhs.republish', '%.3f layout' % time.time())
    except Exception:
        pass


class Layout(object):
    def __init__(self, profile):
        self._path = os.path.join(profile, 'layout.json')
        self._lock = threading.RLock()
        self._data = None
        self._mtime = None

    # ------------------------------------------------------------ storage
    def _load(self):
        try:
            mtime = os.path.getmtime(self._path)
        except OSError:
            mtime = None
        if self._data is not None and mtime == self._mtime:
            return self._data
        data = {}
        if mtime is not None:
            try:
                with open(self._path, 'r', encoding='utf-8') as handle:
                    data = json.load(handle) or {}
            except Exception:
                data = {}
        pages = data.get('pages') if isinstance(data, dict) else None
        self._data = {'version': 1, 'pages': pages if isinstance(pages, dict) else {}}
        self._mtime = mtime
        return self._data

    def _save(self):
        tmp = self._path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(self._data, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
            self._mtime = os.path.getmtime(self._path)
            _skin_follows()
            return True
        except Exception:
            return False

    def page(self, media):
        with self._lock:
            raw = self._load()['pages'].get(page_key(media)) or {}
            return {
                'order': [str(k) for k in (raw.get('order') or []) if k],
                'hidden': dict((str(k), bool(v)) for k, v in (raw.get('hidden') or {}).items()),
                'custom': [dict(c) for c in (raw.get('custom') or [])
                           if isinstance(c, dict) and c.get('id')],
            }

    def save_page(self, media, order=None, hidden=None, custom=None):
        with self._lock:
            data = self._load()
            page = self.page(media)
            if order is not None:
                page['order'] = [str(k) for k in order if k]
            if hidden is not None:
                page['hidden'] = dict((str(k), bool(v)) for k, v in hidden.items())
            if custom is not None:
                page['custom'] = [dict(c) for c in custom if isinstance(c, dict) and c.get('id')]
            data['pages'][page_key(media)] = page
            return self._save()

    def reset_page(self, media):
        """Back to the Home's own order and visibility; added rows stay (at the end)."""
        with self._lock:
            page = self.page(media)
            return self.save_page(media, order=[], hidden={}, custom=page['custom'])

    def hide(self, media, key, hidden=True):
        with self._lock:
            page = self.page(media)
            page['hidden'][key] = bool(hidden)
            return self.save_page(media, hidden=page['hidden'])

    def customized(self, media):
        page = self.page(media)
        return bool(page['order'] or page['hidden'] or page['custom'])

    def stamp(self):
        """Changes whenever the saved layout changes (any page)."""
        with self._lock:
            self._load()
            return self._mtime

    # ------------------------------------------------------------ applying
    def arrange(self, rows, media):
        return arrange(rows, self.page(media))

    def apply(self, rows, media):
        """The rows to show, in the user's order."""
        return [row for row, hidden in self.arrange(rows, media) if not hidden]
