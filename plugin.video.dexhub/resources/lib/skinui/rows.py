# -*- coding: utf-8 -*-
"""The skin's Home rows: which rows each tab has, reading them, publishing them.

The rows are the Dex Hub Home's own (homeui): the same Nuvio Home, the same
collections, the same catalogs and Continue Watching, in the order the user
arranged with the layout editor, read through the same routes (capture.py).
Here they become row caches (rows/<tab>_<id>.json) that skin_row hands to
Kodi in a few milliseconds, and Home window properties the skin reads:

  dhs.<tab>.<n>.path    plugin://...?action=skin_row&m=<tab>&id=<row id>&v=<version>
  dhs.<tab>.<n>.title / .sub / .shape
  dhs.ready             the rows are published

The version in the path changes only when a row's content changed, so Kodi
reads a row again exactly then.
"""
import hashlib
import json
import os
import re
import threading
import time
import traceback
from urllib.parse import urlencode

import xbmc
import xbmcvfs

from . import common as C
from .board import (load_specs, find_spec, row_path, _version, bump,  # noqa: F401
                    _publish_tab, _row_brief, is_published, published, update_spot, SPOT)
from ..homeui import rows as R

_api_lock = threading.Lock()
_API = [None]
_publish_lock = threading.Lock()
_fetch_locks = {}
_fetch_locks_lock = threading.Lock()


# --------------------------------------------------------------------------
# the Home's row model without its window
# --------------------------------------------------------------------------

def api():
    """The Dex Hub router, imported once per interpreter."""
    if _API[0] is not None:
        return _API[0]
    with _api_lock:
        if _API[0] is None:
            started = time.monotonic()
            try:
                from ..i18n import reset_language_cache
                reset_language_cache()
            except Exception:
                pass
            from .. import plugin as _plugin
            try:
                _plugin._reset_invocation_state()
            except Exception:
                try:
                    from ..runtime_binding import bind
                    bind(_plugin)
                except Exception:
                    pass
            _API[0] = _plugin
            C.log('router ready in %.0f ms' % ((time.monotonic() - started) * 1000))
    return _API[0]


class HeadlessApp(object):
    """What the Home's row code needs from homeui.app.App, without its threads."""

    def __init__(self):
        from ..homeui.app import Settings
        from ..homeui.layout import Layout
        self.settings = Settings()
        addon = self.settings._addon
        self.addon_path = addon.getAddonInfo('path')
        self.profile = os.path.join(xbmcvfs.translatePath(addon.getAddonInfo('profile')), 'homeui')
        try:
            os.makedirs(self.profile, exist_ok=True)
        except Exception:
            pass
        self.layout = Layout(self.profile)
        self._tmdb_settings = None

    def api(self):
        return api()

    def log(self, msg, level=None):
        C.log(msg, xbmc.LOGINFO if level is None else level)

    def tr(self, text):
        try:
            from ..i18n import tr
            return tr(text)
        except Exception:
            return text

    def media_path(self, name):
        return os.path.join(self.addon_path, 'resources', 'media', 'homeui', name)


def _window_functions(*names):
    from ..homeui import window as W
    out = {}
    for name in names:
        for cls in (W.BrowseWindow, W._BaseWindow):
            if name in cls.__dict__:
                out[name] = cls.__dict__[name]
                break
    return out


class HeadlessHome(object):
    """The Python Home's row list (homeui BrowseWindow) for one tab, without a window."""

    def __init__(self, app, media):
        self.app = app
        self.media = media
        self.s = app.settings
        self.gif_on = self.s.flag('homeui_focus_gif', True)

    def tr(self, text):
        return self.app.tr(text)


class HeadlessEnricher(object):
    """The Python Home's TMDb pass on a title (its plot and logo in the user's
    language, art a catalog lacks), without a window."""

    def __init__(self, app):
        self.app = app
        self.s = app.settings


def _bind_classes():
    if getattr(HeadlessHome, '_bound', False):
        return
    for name, fn in _window_functions('_candidate_rows', '_custom_rows', 'layout_rows',
                                      '_progress_loader', '_catalog_loader').items():
        setattr(HeadlessHome, name, fn)
    for name, fn in _window_functions('tmdb_settings', '_tmdb_language', '_enrich_now', '_tmdb_localize',
                                      '_helper_localized', '_studio_badge', '_rating_badges', '_supplement_badges').items():
        setattr(HeadlessEnricher, name, fn)
    HeadlessHome._bound = True


_APP = [None]
_MEDIA = [None]


def media():
    """The Python Home's cache of collection GIFs (homeui/focusart): Kodi
    animates a GIF only from a local file."""
    if _MEDIA[0] is None:
        from ..homeui.media_cache import MediaCache
        _MEDIA[0] = MediaCache(os.path.join(app().profile, 'focusart'), log=lambda msg: C.log(msg, xbmc.LOGDEBUG))
    return _MEDIA[0]


def _local_gif(tile):
    gif = tile.get('gif') or ''
    if gif and not gif.startswith(('http://', 'https://')) and os.path.exists(gif):
        return gif
    url = tile.get('gif_url') or (gif if gif.startswith(('http://', 'https://')) else '')
    if not url:
        return ''
    try:
        return media().local(url)
    except Exception:
        return ''


def app():
    if _APP[0] is None:
        _bind_classes()
        _APP[0] = HeadlessApp()
    return _APP[0]


def compute_rows(tab):
    """The rows of a tab as the user arranged them (homeui Row objects)."""
    a = app()
    home = HeadlessHome(a, tab)
    rows = a.layout.apply(home.layout_rows(tab), tab)
    return [row for row in rows if not row.meta.get('missing')]


def reset_settings():
    """Fresh settings and locale; keep catalogs cached while relocalizing."""
    from ..homeui.app import Settings
    from ..settings_cache import invalidate
    from ..i18n import reset_language_cache
    invalidate()
    reset_language_cache()
    a = app()
    a.settings = Settings()
    a._tmdb_settings = None
    language = HeadlessEnricher(a)._tmdb_language() or ''
    active = C.prop('dhs.tab') or 'all'
    for tab in [active] + [t for t in C.TABS if t != active]:
        for slot, spec in published(tab):
            path = C.row_file(tab, spec['id'])
            with C.file_lock(path):
                data = C.read_json(path)
                if not data or data.get('lang') == language:
                    continue
                data['lang'] = language
                data['enriched'] = False
                # Old work must not overwrite this newer language generation.
                data['h'] = C.content_hash(data.get('items') or [], {'lang': language})
                C.write_json(path, data)
            C.request_refresh(tab, spec['id'], enrich_only=True)


# --------------------------------------------------------------------------
# specs and publishing
# --------------------------------------------------------------------------

def spec_of(row, tab):
    meta = row.meta or {}
    progress = bool(meta.get('progress')) or row.kind == 'continue'
    params = dict(meta.get('params') or {})
    # what the row reads is part of its id: a row edited to read something
    # else starts from a new cache
    ident = row.key if not params else '%s|%s' % (row.key, json.dumps(params, sort_keys=True))
    return {
        'key': row.key,
        'id': C.short_id(ident),
        'tab': tab,
        'title': row.title or '',
        'sub': row.subtitle or '',
        'shape': row.shape or 'poster',
        'kind': row.kind or 'catalog',
        'params': params,
        'progress': progress,
        'auto_shape': bool(meta.get('auto_shape')),
        'own': row.key in ('continue', 'nextup'),
    }


def publish(tabs=C.TABS, reason='', computed=None):
    """Work out every tab's rows and hand them to the skin (``computed``:
    {tab: rows} the caller worked out a moment ago, used as they are)."""
    with _publish_lock:
        started = time.monotonic()
        total = 0
        C.set_prop(C.PROP_PUBLISHING, '%.1f' % time.time())
        try:
            from . import strings
            strings.publish()
            total = _publish(tabs, computed or {})
        finally:
            C.set_prop(C.PROP_PUBLISHING, '')
        C.set_prop(C.PROP_PUBLISHED, '%.1f' % time.time())
        C.set_prop(C.PROP_READY, '1')
        C.log('rows published (%s): %d in %.0f ms' % (reason or 'start', total, (time.monotonic() - started) * 1000))
        return total


def _tiles_digest(tiles):
    """A fingerprint of the cards a collection row is built from."""
    try:
        text = json.dumps(tiles or [], sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        text = repr(tiles)
    return hashlib.sha1(text.encode('utf-8', 'replace')).hexdigest()[:16]


def refresh_collections(computed):
    """Collection rows whose cards changed since they were written (a
    collection edited in Nuvio, a folder added) are written again and asked
    for again; the others are left alone (v5.10.119). ``computed``: {tab:
    rows} just worked out. Returns how many changed."""
    changed = 0
    for tab, rows in (computed or {}).items():
        for row in rows or []:
            if row.kind != 'collection':
                continue
            spec = spec_of(row, tab)
            digest = _tiles_digest(row.tiles)
            brief = _row_brief(C.row_file(tab, spec['id']))
            if brief is not None and brief.get('src') == digest and brief.get('f') == C.FORMAT:
                continue
            _store(tab, spec, row.tiles, None, enriched=True, src=digest)
            if is_published(tab, spec['id']):
                bump(tab, spec['id'])
            changed += 1
    if changed:
        C.log('collection rows written again: %d' % changed)
    return changed


def _publish(tabs, computed=None):
    total = 0
    for tab in tabs:
        try:
            rows = (computed or {}).get(tab)
            if rows is None:
                rows = compute_rows(tab)
        except Exception:
            C.log('rows of %s failed:\n%s' % (tab, traceback.format_exc()), xbmc.LOGWARNING)
            continue
        specs = []
        for row in rows:
            spec = spec_of(row, tab)
            if row.kind == 'collection':
                # a collection's cards are known now: its cache is written
                # here. v5.10.119: only when they changed (each card is
                # turned into a Kodi item and the row written again: 200
                # cards took 50 to 70 ms on a PC, a box several times that,
                # for every collection row of every tab at every publish)
                digest = _tiles_digest(row.tiles)
                brief = _row_brief(C.row_file(tab, spec['id']))
                if brief is not None and brief.get('src') == digest and brief.get('f') == C.FORMAT:
                    specs.append(spec)
                    continue
                data = _store(tab, spec, row.tiles, None, enriched=True, src=digest)
                served = C.prop('dhs.sv.%s.%s' % (tab, spec['id']))
                if served and served != data.get('h'):
                    # the cards changed (another version of the add-on): a
                    # new path makes Kodi read them again
                    C.set_prop('dhs.v.%s.%s' % (tab, spec['id']), _version(tab, spec['id']) + 1)
            specs.append(spec)
        C.write_json(C.spec_file(tab), {'t': C.now(), 'rows': specs})
        total += _publish_tab(tab, specs)
    return total


# --------------------------------------------------------------------------
# reading a row
# --------------------------------------------------------------------------

def prune(max_age=3600.0):
    """Remove the caches of rows no tab has any more (older than an hour)."""
    keep = set()
    for tab in C.TABS:
        keep.add('%s_%s.json' % (tab, SPOT))
        for spec in load_specs(tab):
            keep.add('%s_%s.json' % (tab, spec.get('id')))
    folder = C.folder('rows')
    removed = 0
    try:
        names = os.listdir(folder)
    except Exception:
        return 0
    for name in names:
        if not name.endswith('.json') or name in keep:
            continue
        path = os.path.join(folder, name)
        try:
            if C.now() - os.path.getmtime(path) > max_age:
                os.remove(path)
                removed += 1
        except Exception:
            pass
    return removed


def _row_lock(tab, row_id):
    with _fetch_locks_lock:
        lock = _fetch_locks.get((tab, row_id))
        if lock is None:
            lock = _fetch_locks[(tab, row_id)] = threading.Lock()
        return lock


def ttl_of(spec):
    if spec.get('kind') == 'collection':
        return C.TTL_COLLECTION
    if spec.get('progress'):
        return C.TTL_PROGRESS
    return C.TTL_CATALOG


EMPTY_GRACE = 10 * 60.0     # a row that had titles stays on show this long when reads come back empty
FAIL_PAUSES = (5 * 60.0, 15 * 60.0, 30 * 60.0, 60 * 60.0)   # a failing row's next reads (v5.10.119)


def fetch(tab, row_id, spec=None, fresh=False, patience=0.0, wait_other=True):
    """Read a row through its route and store it; returns the cache dict or None."""
    spec = spec or find_spec(tab, row_id)
    if spec is None:
        return None
    lock = _row_lock(tab, row_id)
    with lock:
        busy = 'dhs.busy.%s.%s' % (tab, row_id)
        if wait_other and _read_elsewhere(busy):
            # the Home's first read or the service read it meanwhile
            data = C.read_json(C.row_file(tab, row_id))
            if C.fresh_copy(data, ttl_of(spec)):
                return data
        old = C.read_json(C.row_file(tab, row_id))
        if old is not None and old.get('fails') and C.fresh_copy(old, old.get('ttl') or ttl_of(spec)):
            # v5.10.120: a row whose server failed waits out its pause, a
            # read asked by the service included (a box read three failing
            # rows 56 times each in 26 minutes)
            return old
        fresh = bool(fresh or C.fresh_asked())
        C.set_prop(busy, '%.1f' % time.time())
        started = time.monotonic()
        try:
            # v5.10.115: the mark stays until the copy is written: a call
            # waiting for this read (the page's own, serve._first_read) read
            # the row again when it saw the mark go before the file came
            return _fetch_marked(tab, row_id, spec, old, fresh, patience, started)
        finally:
            C.set_prop(busy, '')


def _gone(exc):
    """The catalog is not there (HTTP 404): its row is empty, not offline."""
    text = str(exc)
    return 'HTTP 404' in text or ' 404 ' in (' %s ' % text) or 'Not Found' in text


def _fetch_marked(tab, row_id, spec, old, fresh, patience, started):
    gone = False
    try:
        tiles, more = _load(tab, spec, fresh=fresh, patience=patience)
        tiles = [t for t in tiles or [] if not is_note(t)]
    except Exception as exc:
        C.log('row "%s" failed: %s' % (spec.get('title'), exc), xbmc.LOGWARNING)
        gone = _gone(exc)
        if old is not None and not gone:
            # offline or a slow server: keep what was shown. v5.10.119: and
            # read it again after a pause that grows with each failure (each
            # turn waited for the server's timeout again: the Home's next
            # look, the service's next round, the next playback's end)
            fails = int(old.get('fails') or 0) + 1
            pause = FAIL_PAUSES[min(fails, len(FAIL_PAUSES)) - 1]
            old['fails'] = fails
            old['t'] = time.time() - float(old.get('ttl') or ttl_of(spec)) + pause
            try:
                C.write_json(C.row_file(tab, row_id), old)
            except Exception:
                pass
            C.log('row "%s": the last titles stay, read again in %d min' % (spec.get('title'), pause // 60))
            return old
        tiles, more = [], None
    had_titles = bool(old and old.get('items'))
    if (not tiles and had_titles and not gone and not (old or {}).get('choice_changed')
            and not spec.get('own') and spec.get('kind') != 'collection'):
        # a source that answers with nothing once (a timeout it swallowed,
        # a server waking up) does not empty a row that had titles: the
        # row stays until reads come back empty for EMPTY_GRACE
        since = float(old.get('empty_since') or 0) or time.time()
        if time.time() - since < EMPTY_GRACE:
            old['empty_since'] = since
            # read again once the grace is over (not at every turn before)
            old['t'] = time.time() - float(old.get('ttl') or ttl_of(spec)) + EMPTY_GRACE
            C.write_json(C.row_file(tab, row_id), old)
            C.log('row "%s" came back empty: the last titles stay' % spec.get('title'))
            return old
    if spec.get('auto_shape') and tiles:
        spec['shape'] = R.dominant_shape(tiles, spec.get('shape') or 'poster')
    data = _store(tab, spec, tiles, more, old=old)
    if gone:
        # v5.10.120: a catalog the add-on no longer has (HTTP 404) leaves
        # the Home at once, its old titles with it, and is asked again after
        # the pauses of a failing row
        fails = int((old or {}).get('fails') or 0) + 1
        pause = FAIL_PAUSES[min(fails, len(FAIL_PAUSES)) - 1]
        data['fails'] = fails
        data['t'] = time.time() - float(data.get('ttl') or ttl_of(spec)) + pause
        C.write_json(C.row_file(tab, row_id), data)
    shown = is_published(tab, row_id)
    if shown:
        for slot, current in published(tab):
            if current['id'] == row_id:
                sub = spec.get('sub') or ''
                if sub.strip().lower() == (spec.get('title') or '').strip().lower():
                    sub = ''
                C.set_prop('dhs.%s.%d.sub' % (tab, slot), '  ·  '.join(p for p in (sub, data.get('filter_summary')) if p))
                break
    if tab not in C.TABS:
        # a collection folder's page (folderpage.py): an empty row leaves it
        if not tiles and shown:
            from . import folderpage
            folderpage.republish()
    elif not tiles and shown:
        # an empty row leaves the Home (publishing skips it) until it has titles again
        C.ask_republish('empty row')
    elif tiles and not shown and old is not None and not old.get('items'):
        # a row that was empty has titles again (Continue Watching after a first play)
        C.ask_republish('row back')
    C.log('row "%s" %d item(s) in %.0f ms' % (spec.get('title'), len(tiles), (time.monotonic() - started) * 1000))
    _follow_spot(tab, spec)
    return data


def _follow_spot(tab, spec):
    """A catalog row on show was read again: the tab's spotlight picks may
    come from it (skin.dexhub 2)."""
    if spec.get('own') or spec.get('progress') or (spec.get('kind') or 'catalog') != 'catalog':
        return
    if tab not in C.TABS or not is_published(tab, spec.get('id')):
        return
    try:
        update_spot(tab)
    except Exception as exc:
        C.log('spotlight of %s: %s' % (tab, exc))


def _read_elsewhere(busy, patience=20.0):
    """Another add-on call reads this row now (this interpreter's lock is
    held, so the mark is someone else's): wait for it; True when it did."""
    try:
        since = float(C.prop(busy) or 0)
    except ValueError:
        since = 0.0
    if not since or time.time() - since > 60.0:
        return False            # no mark, or one left by a call that died
    deadline = time.monotonic() + patience
    monitor = xbmc.Monitor()
    while C.prop(busy) and time.monotonic() < deadline:
        if monitor.waitForAbort(0.1):
            break
    return True


_NOTE_RE = re.compile(r'^\[COLOR [^\]]+\].*\[/COLOR\]$', re.S)


def is_note(tile):
    """A line a listing shows in place of titles ("Nothing in Emby"): the
    Home hides an empty row instead of showing it as a card."""
    if not isinstance(tile, dict) or tile.get('kind') == 'work':
        return False
    label = (tile.get('title') or tile.get('label') or '').strip()
    return bool(_NOTE_RE.match(label))


def _load(tab, spec, fresh=False, patience=0.0):
    home = HeadlessHome(app(), tab)
    if spec.get('kind') == 'collection':
        for row in compute_rows(tab):
            if row.key == spec.get('key'):
                return list(row.tiles), None
        return [], None
    if spec.get('own'):
        loader = home._progress_loader(spec['key'], tab)
    else:
        params = spec.get('params') or {}
        if not params.get('action'):
            return [], None
        loader = home._catalog_loader(params, progress=bool(spec.get('progress')))
    if fresh:
        from ..dexhub import client as _client
        with _client.fresh_reads():
            result = loader(patience=patience)
    else:
        result = loader(patience=patience)
    from ..homeui import grid_filters as GF
    spec['_filter_summary'] = getattr(home, '_filter_summaries', {}).get(GF.page_key(spec.get('params') or {}), '')
    return result


ROW_ITEMS = 40          # titles a Home row holds (See all lists the rest)
ENRICH_ITEMS = 24       # the first titles of a row get the TMDb pass


def _store(tab, spec, tiles, more, enriched=False, old=None, src=None):
    ctx = Context(tab)
    ctx.batch = True
    items = []
    limit = ROW_ITEMS if spec.get('kind') != 'collection' else 200
    for index, tile in enumerate((tiles or [])[:limit]):
        try:
            items.append(ctx.item(tile, spec))
        except Exception:
            C.log('item failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    ctx.flush_seeds()
    carried = _carry_enrichment(items, old, ctx.language)
    # See all: the row's whole listing in a grid, with its Sort, Genre and
    # Filter (every catalog row has it, also one whose first page is all)
    if (items and spec.get('kind') == 'catalog' and not spec.get('own')
            and (more or (spec.get('params') or {}).get('action'))):
        items.append(ctx.more_item(spec, tiles, more))
    if not enriched and ctx.language and not spec.get('own'):
        enriched = carried
    data = {
        't': C.now(), 'key': spec.get('key'), 'title': spec.get('title'), 'shape': spec.get('shape'),
        'kind': spec.get('kind'), 'items': items, 'more': more, 'h': C.content_hash(items),
        'lang': ctx.language, 'enriched': bool(enriched or not ctx.language or spec.get('own')),
        'ttl': ttl_of(spec), 'f': C.FORMAT, 'filter_summary': spec.get('_filter_summary') or '',
    }
    from ..meta_policy import source_appearance
    if source_appearance():
        # Source text/art need no row-wide TMDb or badge pass.
        data['enriched'] = True
    if src:
        data['src'] = src           # the cards it was built from (_publish)
    C.write_json(C.row_file(tab, spec['id']), data)
    return data


_ENRICHED_INFO = ('plot', 'genres', 'duration', 'rating')
_ENRICHED_ART = ('clearlogo', 'fanart', 'landscape', 'poster', 'thumb')


def _src_key(item):
    src = (item or {}).get('src') or {}
    return src.get('imdb_id') or src.get('tmdb_id') or src.get('path') or ''


def _carry_enrichment(items, old, language):
    """A row read again keeps the TMDb pass its last copy had (plot, logo,
    genres in the user's language), so the same titles look the same and
    Kodi has nothing to reload. True when every title that gets the pass
    has it."""
    previous = {}
    if old and language and old.get('lang') == language:
        for item in old.get('items') or []:
            src = item.get('src') or {}
            if src.get('enriched') == language and _src_key(item):
                previous[_src_key(item)] = item
    complete = True
    for index, item in enumerate(items):
        props = item.get('props') or {}
        if props.get('kind') != 'work' or index >= ENRICH_ITEMS:
            continue
        before = previous.get(_src_key(item))
        if before is None:
            complete = False
            continue
        info, art = item.setdefault('info', {}), item.setdefault('art', {})
        old_info, old_art = before.get('info') or {}, before.get('art') or {}
        for key in _ENRICHED_INFO:
            if old_info.get(key):
                info[key] = old_info[key]
        for key in _ENRICHED_ART:
            if old_art.get(key) and (key in ('clearlogo', 'fanart') or not art.get(key)):
                art[key] = old_art[key]
        ids = item.setdefault('ids', {})
        for key, value in (before.get('ids') or {}).items():
            if value and not ids.get(key):
                ids[key] = value
        for key in ('dhs.meta', 'dhs.rating', 'dhs.studio_logo', 'dhs.studio_chip', 'dhs.studio_seed'):
            if (before.get('props') or {}).get(key):
                props[key] = before['props'][key]
        src = item.setdefault('src', {})
        old_src = before.get('src') or {}
        for key in ('imdb_id', 'tmdb_id'):
            if old_src.get(key) and not src.get(key):
                src[key] = old_src[key]
        src['enriched'] = language
    return complete


# --------------------------------------------------------------------------
# tiles to cached items
# --------------------------------------------------------------------------

_WORK_TYPES = {'movie': 'فيلم', 'series': 'مسلسل', 'episode': 'حلقة'}
_UI_ART_ACTIONS = frozenset(('play_item', 'streams', 'episode_streams', 'cw_resume'))
_SERVER_ART_ACTIONS = frozenset(('plex_play', 'emby_play', 'jellyfin_play', 'silo_play', 'plex_children'))
_HELPER_ACTIONS = frozenset(('item_open', 'series_meta', 'seasons', 'season', 'play_item', 'streams', 'episode_streams', 'tmdb_player'))
_DEXHUB_ROUTE_RE = re.compile(r'(plugin://plugin\.video\.dexhub/?\?)(?!dh_home=1)')
TMDBH_ID = 'plugin.video.themoviedb.helper'


def episode_code(tile):
    season, episode = (tile or {}).get('season'), (tile or {}).get('episode')
    if not (season or episode):
        return ''
    return 'S%s:E%s' % (season or '?', episode or '?')


def list_url(params, title='', shape='', page_tile=None):
    """A Dex Hub listing opened in the Videos window (skin_list)."""
    query = {'r': urlencode(sorted((k, v) for k, v in (params or {}).items() if v not in (None, '')))}
    if title:
        query['t'] = title
    if shape:
        query['s'] = shape
    return C.url('skin_list', **query)


class Context(object):
    """Settings read once per row: the title page choice, the language."""

    def __init__(self, tab):
        a = app()
        self.app = a
        self.tab = tab
        self.tr = a.tr
        self.arabic = a.settings.arabic
        value = str(a.settings.text('homeui_title_page', 'Dex Hub') or '').strip().lower()
        self.title_page = ('tmdbhelper' if value.startswith('tmdb') else
                           'classic' if (value.startswith('classic') or value.startswith('كلاسيك')) else 'dexhub')
        try:
            enricher = HeadlessEnricher(a)
            self.language = enricher._tmdb_language() or ''
        except Exception:
            self.language = ''
        self._helper = None
        self._favorites = None
        self._seeds = {}
        self.batch = False          # True: art keys are written by flush_seeds()

    def flush_seeds(self):
        """Write the art of the server titles listed (one transaction)."""
        seeds, self._seeds = self._seeds, {}
        if not seeds:
            return
        try:
            from .. import cache_store
            cache_store.put_many('ui_seed', seeds, ttl_hours=24)
        except Exception as exc:
            C.log('art keys not saved: %s' % exc, xbmc.LOGWARNING)

    # ------------------------------------------------------------ helpers
    def helper_installed(self):
        if self._helper is None:
            try:
                self._helper = bool(xbmc.getCondVisibility('System.HasAddon(%s)' % TMDBH_ID))
            except Exception:
                self._helper = False
        return self._helper

    def favorite(self, media, canonical):
        if self._favorites is None:
            try:
                from .. import favorites_store as F
                self._favorites = F
            except Exception:
                self._favorites = False
        if not self._favorites or not canonical:
            return False
        try:
            return bool(self._favorites.is_favorite(media, canonical))
        except Exception:
            return False

    def supported(self, tile):
        """A movie or series that opens on the title page (homeui details.supported)."""
        if tile.get('kind') != 'work' or tile.get('cw'):
            return False
        if (tile.get('media_type') or '') not in ('movie', 'series', 'episode'):
            return False
        path = tile.get('path') or ''
        if path and R.is_dexhub_path(path):
            params = R.params_of(path)
            if params.get('action', '') not in _HELPER_ACTIONS:
                return False
            return bool(params.get('canonical_id') or tile.get('imdb_id') or tile.get('tmdb_id'))
        return bool(tile.get('imdb_id') or tile.get('tmdb_id'))

    def helper_path(self, tile):
        media = tile.get('media_type') or ''
        if tile.get('kind') != 'work' or tile.get('cw') or media not in ('movie', 'series', 'episode'):
            return ''
        path = tile.get('path') or ''
        if R.is_dexhub_path(path) and R.params_of(path).get('action', '') not in _HELPER_ACTIONS:
            return ''
        if not self.helper_installed():
            return ''
        tmdb = str(tile.get('tmdb_id') or '').strip()
        imdb = str(tile.get('imdb_id') or '').strip()
        params = {'info': 'details', 'tmdb_type': 'movie' if media == 'movie' else 'tv'}
        if tmdb.isdigit():
            params['tmdb_id'] = tmdb
        elif imdb.startswith('tt'):
            params['imdb_id'] = imdb
        else:
            return ''
        return 'plugin://%s/?%s' % (TMDBH_ID, urlencode(params))

    def home_route(self, path):
        """A Dex Hub route opened from the Home carries dh_home=1 (see plugin._home_owns_click)."""
        if self.title_page == 'tmdbhelper' or 'dh_home=1' in path:
            return path
        return _DEXHUB_ROUTE_RE.sub(r'\1dh_home=1&', path, count=1)

    def with_ui_art(self, path, action, tile):
        """The title's art for the sources window (homeui window._with_ui_art):
        a server's art goes by a cache key, never in the URL."""
        if action not in _UI_ART_ACTIONS and action not in _SERVER_ART_ACTIONS:
            return path
        params = R.params_of(path)
        if action in _SERVER_ART_ACTIONS and params.get('ui_seed_key'):
            return path
        extra = {}
        if tile.get('clearlogo') and not params.get('ui_clearlogo'):
            extra['ui_clearlogo'] = tile['clearlogo']
        if tile.get('fanart') and not params.get('ui_fanart'):
            extra['ui_fanart'] = tile['fanart']
        if tile.get('poster') and not params.get('ui_poster') and tile.get('media_type') != 'episode':
            extra['ui_poster'] = tile['poster']
        if not extra:
            return path
        if action in _SERVER_ART_ACTIONS:
            # the same art, the same key: a row read again keeps its paths
            # (Kodi is not made to reload it), written once per listing
            key = 'dhs' + hashlib.sha1(json.dumps(extra, sort_keys=True).encode('utf-8')).hexdigest()[:29]
            self._seeds[key] = extra
            if not self.batch:
                self.flush_seeds()
            return path + ('&' if '?' in path else '?') + urlencode({'ui_seed_key': key})
        return path + ('&' if '?' in path else '?') + urlencode(extra)

    # ------------------------------------------------------------ clicks
    def click(self, tile, spec):
        """(dhs.click, dhs.target) of a tile: what OK does on it."""
        kind = tile.get('kind') or ''
        if kind == 'library':
            title = tile.get('title') or ''
            if tile.get('server'):
                title = '%s  •  %s' % (tile.get('server'), title)
            return 'grid', list_url(tile.get('params') or {}, title=title, shape='poster')
        if kind == 'action':
            if tile.get('command') == 'catalog_filter':
                return 'run', C.url('skin_row_facet', m=self.tab, id=spec.get('id') or '')
            if (tile.get('command') or '') == 'setup':
                return 'run', C.url('home_setup')
            return '', ''
        if kind == 'folder':
            ref = tile.get('folder_ref') or {}
            indexes = tile.get('source_indexes') or []
            if len(indexes) == 1:
                params = {'action': 'collection_nuvio_source_open', 'set_id': ref.get('set_id') or '',
                          'group_id': ref.get('group_id') or '', 'folder_id': ref.get('folder_id') or '',
                          'source_index': str(indexes[0]), 'page': '1'}
                return 'grid', list_url(params, title=tile.get('title') or '')
            return 'grid', C.url('skin_folder', set_id=ref.get('set_id') or '', group_id=ref.get('group_id') or '',
                                 folder_id=ref.get('folder_id') or '', media_filter=ref.get('media_filter') or '',
                                 t=tile.get('title') or '')
        path = tile.get('path') or ''
        if kind == 'work' and self.title_page == 'dexhub' and self.supported(tile):
            return 'info', ''
        if not path:
            return '', ''
        helper = self.helper_path(tile) if self.title_page == 'tmdbhelper' else ''
        if helper:
            return 'grid', helper
        dexhub = R.is_dexhub_path(path)
        action = R.params_of(path).get('action', '') if dexhub else ''
        if dexhub:
            path = self.home_route(self.with_ui_art(path, action, tile))
        if action in R.PLAYER_ACTIONS or not tile.get('folder'):
            return ('run' if dexhub else 'play'), path
        if dexhub and action in R.SERVER_FOLDER_ACTIONS:
            title = tile.get('title') or tile.get('label') or ''
            show = tile.get('show') or ''
            if show and title and show != title:
                title = '%s  •  %s' % (show, title)
            shape = 'landscape' if (tile.get('season_card') or action == 'silo_episodes') else 'poster'
            return 'grid', list_url(R.params_of(path), title=title, shape=shape)
        if not dexhub or action in R.WORK_FOLDER_ACTIONS or kind == 'work':
            return 'grid', path
        return 'grid', list_url(R.params_of(path), title=tile.get('title') or tile.get('label') or '')

    # ------------------------------------------------------------ display
    def meta(self, tile):
        kind = tile.get('kind')
        out = []
        if kind == 'folder':
            count = int(tile.get('source_count') or 0)
            if count > 1:
                out.append(self.tr('%d كتالوج') % count)
            elif count == 1:
                out.append(self.tr('كتالوج واحد'))
        elif kind == 'work':
            if tile.get('year'):
                out.append(str(tile['year']))
            duration = int(tile.get('duration') or 0)
            if duration >= 60:
                hours, minutes = divmod(duration // 60, 60)
                if self.arabic:
                    out.append(('%dس %dد' % (hours, minutes)) if hours else ('%dد' % minutes))
                else:
                    out.append(('%dh %dm' % (hours, minutes)) if hours else ('%dm' % minutes))
            genres = tile.get('genres') or []
            if genres:
                out.append(' / '.join(genres[:3]))
            if tile.get('mpaa'):
                out.append(str(tile['mpaa']))
            if tile.get('cw') and tile.get('label2') and tile.get('label2') != tile.get('hint'):
                out.append(str(tile['label2']))
        elif kind == 'library':
            out.append(tile.get('brand') or '')
        return '   •   '.join([m for m in out if m])

    @staticmethod
    def rating(tile):
        """The rating shown after the meta line, by a star picture (the
        interface font has no star glyph)."""
        if tile.get('kind') != 'work':
            return ''
        try:
            value = float(tile.get('rating') or 0)
        except Exception:
            return ''
        return ('%.1f' % value) if value > 0 else ''

    def badge(self, tile):
        kind = tile.get('kind')
        media = tile.get('media_type') or ''
        if kind == 'library':
            return self.tr('مكتبة')
        if kind == 'action':
            return self.tr('إعداد')
        if kind == 'folder':
            return self.tr('مجموعة')
        if tile.get('season_card'):
            return self.tr('موسم')
        if media in _WORK_TYPES:
            return self.tr(_WORK_TYPES[media])
        return ''

    def remaining(self, tile):
        progress = int(tile.get('progress') or 0)
        if progress and tile.get('total'):
            left = max(0, int(float(tile.get('total')) - float(tile.get('resume') or 0)) // 60)
            if left:
                return self.tr('متبقي %d دقيقة') % left
        return ''

    def item(self, tile, spec):
        """One cached item for a tile of a row (or a grid, or a related list)."""
        tile = R.strip_tile(tile)
        kind = tile.get('kind') or ''
        media = tile.get('media_type') or ''
        title = tile.get('title') or tile.get('label') or ''
        show = tile.get('show') or ''
        label = title
        episode_name = ''
        if media == 'episode':
            episode_name = tile.get('episode_name') or R.episode_title(title, show)
        if media == 'episode' and show:
            label = show
        subtitle, hero_sub = '', ''
        if media == 'episode' and (tile.get('season') or tile.get('episode')):
            parts = [episode_code(tile), episode_name]
            subtitle = '  ·  '.join([p for p in parts if p])
            if show:
                hero_sub = subtitle
            if spec.get('grid') and (episode_name or title):
                # a season's page: the cards name the episodes, not the show
                label = episode_name or title
                subtitle = episode_code(tile)
        elif tile.get('hint'):
            subtitle = hero_sub = tile.get('hint')
        elif tile.get('cw') and tile.get('label2'):
            subtitle = tile.get('label2')
        poster = tile.get('poster') or tile.get('landscape') or ''
        landscape = tile.get('landscape') or tile.get('fanart') or tile.get('poster') or ''
        fanart = tile.get('fanart') or tile.get('landscape') or ''
        if kind in ('library', 'action'):
            fanart = tile.get('fanart') or ''
        if kind == 'folder' and not fanart:
            fanart = tile.get('group_fanart') or ''
        art = {'poster': poster, 'thumb': poster if spec.get('shape') != 'landscape' else landscape,
               'landscape': landscape, 'fanart': fanart, 'clearlogo': tile.get('clearlogo') or ''}
        if kind == 'channel':
            art['icon'] = tile.get('logo') or ''
        if kind == 'folder' and self.app.settings.flag('homeui_focus_gif', True):
            # a collection's animated cover, once it is on the device (fetch_gifs)
            gif = _local_gif(tile)
            if gif:
                art['gif'] = gif
        click, target = self.click(tile, spec)
        if kind == 'work' and not tile.get('studio_logo') and (tile.get('studios') or tile.get('studio')):
            try:
                from .. import studio_art
                logo = studio_art.resolve(tile.get('studios') or tile.get('studio'))
                if logo:
                    tile['studio_logo'] = logo
                    tile['studio_logo_chip'] = False
            except Exception:
                pass
        hero_title = show if (media == 'episode' and show) else title
        props = {
            'kind': kind,
            'hide_title': '1' if tile.get('hide_title') else '',
            'show_title': '1' if tile.get('season_card') else '',
            'initials': R.initials(label),
            'subtitle': subtitle,
            'dhs.click': click,
            'dhs.target': target,
            'dhs.badge': self.badge(tile),
            'dhs.meta': self.meta(tile),
            'dhs.rating': self.rating(tile),
            'dhs.hero_video': (tile.get('hero_video') or '') if kind == 'folder' else '',
            'dhs.gif_key': C.short_id(tile.get('gif_url') or '') if kind == 'folder' and tile.get('gif_url') else '',
            'dhs.studio_logo': tile.get('studio_logo') or '',
            'dhs.studio_chip': '1' if tile.get('studio_logo_chip') else '',
            'dhs.sub': hero_sub,
            'dhs.remaining': self.remaining(tile),
            'dhs.long': '1' if len(hero_title) > 26 else '',
        }
        if kind == 'folder' and click == 'grid' and '?action=skin_folder&' in (target or ''):
            # skin.dexhub 3.2: the folder opens as a page of rows (folderpage.py);
            # an older skin and Arctic Fuse 3 keep the cards page (dhs.target)
            from .folderpage import page_url
            props['dhs.fold'] = page_url(tile.get('folder_ref') or {}, tile.get('title') or '')
        if click == 'info' or self.info_page(tile) or self.supported(tile):
            props.update(self.title_props(tile))
            props['dhs.open_title'] = '1' if self.supported(tile) else ''
            props['dhs.raw'] = tile.get('path') or ''
        mediatype = {'movie': 'movie', 'series': 'tvshow', 'episode': 'episode'}.get(media, 'video')
        if tile.get('season_card'):
            mediatype = 'season'
        info = {
            'title': hero_title if kind == 'work' else title,
            'mediatype': mediatype,
            'plot': tile.get('plot') or tile.get('tagline') or '',
            'year': tile.get('year') or '',
            'genres': list(tile.get('genres') or [])[:3],
            'rating': tile.get('rating') or 0,
            'duration': int(tile.get('duration') or 0),
            'mpaa': tile.get('mpaa') or '',
            'tvshowtitle': show,
            'season': tile.get('season') or '',
            'episode': tile.get('episode') or '',
        }
        ids = {'imdb': tile.get('imdb_id') or '', 'tmdb': tile.get('tmdb_id') or '', 'tvdb': tile.get('tvdb_id') or ''}
        menu = [list(m) for m in (tile.get('menu') or []) if isinstance(m, (list, tuple)) and len(m) > 1]
        if spec.get('kind') == 'catalog' and not spec.get('own') and not spec.get('progress') and (spec.get('params') or {}).get('action'):
            menu.append([self.tr('خيارات الكتالوج'), 'RunPlugin(%s)' % C.url('skin_row_facet', m=self.tab, id=spec.get('id') or '')])
        return {
            'label': label,
            'label2': tile.get('show') or '',
            'path': target if click in ('grid', 'run', 'play') else (tile.get('path') or ''),
            'folder': click == 'grid',
            'art': art,
            'info': info,
            'ids': ids,
            'progress': int(tile.get('progress') or 0),
            'watched': bool(tile.get('playcount')) and not int(tile.get('progress') or 0),
            'props': props,
            'menu': menu,
            'src': {'imdb_id': tile.get('imdb_id') or '', 'tmdb_id': tile.get('tmdb_id') or '',
                    'media_type': media, 'title': title, 'show': show, 'year': tile.get('year') or '',
                    'path': tile.get('path') or '', 'enriched': '', 'studios': tile.get('studios') or tile.get('studio') or [],
                    'gif_url': (tile.get('gif_url') or '') if kind == 'folder' else ''},
        }

    def info_page(self, tile):
        """A continue watching title: OK resumes it, Info opens its title page
        (the series' for an episode) with what is left of it (v5.10.110)."""
        if self.title_page != 'dexhub' or tile.get('kind') != 'work':
            return False
        if (tile.get('media_type') or '') not in ('movie', 'series', 'episode'):
            return False
        path = tile.get('path') or ''
        if path and not R.is_dexhub_path(path):
            return False
        params = R.params_of(path) if path else {}
        return bool(params.get('canonical_id') or tile.get('imdb_id') or tile.get('tmdb_id'))

    def title_props(self, tile):
        """What the title page needs (dhs.q is its add-on query, dhs.play the play route)."""
        media = 'series' if tile.get('media_type') in ('series', 'episode') else 'movie'
        path = tile.get('path') or ''
        params = R.params_of(path) if R.is_dexhub_path(path) else {}
        canonical = params.get('canonical_id') or tile.get('imdb_id') or ''
        if media == 'series':
            match = re.fullmatch(r'(tt\d+|tmdb:\d+):\d+:\d+', str(canonical))
            if match:
                canonical = match.group(1)
        if not canonical and tile.get('tmdb_id'):
            canonical = 'tmdb:%s' % tile['tmdb_id']
        media_type = params.get('media_type') if params.get('media_type') in ('series', 'anime') else media
        title_id = C.short_id('%s|%s' % (media, canonical))
        query = [('m', media_type), ('id', canonical), ('i', title_id)]
        # continue watching rows name a virtual source: the title's own
        # metadata add-ons find it instead
        source = params.get('source_provider_id') or ''
        if source.startswith('virtual.'):
            source = ''
        name = (tile.get('show') if tile.get('media_type') == 'episode' else '') or tile.get('title') or ''
        for key, value in (('src', source), ('tmdb', tile.get('tmdb_id') or ''),
                           ('imdb', tile.get('imdb_id') or ''), ('title', name),
                           ('year', tile.get('year') or '')):
            if value:
                query.append((key, value))
        play = ''
        if path and params.get('action', '') in R.PLAYER_ACTIONS:
            play = self.home_route(self.with_ui_art(path, params.get('action', ''), tile))
        return {
            'dhs.q': urlencode(query),
            'dhs.id': title_id,
            'dhs.media': 'series' if media == 'series' else 'movie',
            'dhs.play': play,
            'dhs.fav': '1' if self.favorite(media_type if media == 'series' else 'movie', canonical) else '',
        }

    def more_item(self, spec, tiles, more):
        art = self.app.media_path('more_tile.png')
        label = self.tr('عرض الكل')
        params = spec.get('params') or {}
        return {
            'label': label, 'path': list_url(params, title=spec.get('title') or '', shape=spec.get('shape') or ''),
            'folder': True, 'art': {'poster': art, 'landscape': art},
            'props': {'kind': 'more', 'dhs.click': 'grid',
                      'dhs.target': list_url(params, title=spec.get('title') or '', shape=spec.get('shape') or '')},
        }


# --------------------------------------------------------------------------
# the TMDb pass (the user's language), made by the service after a row is read
# --------------------------------------------------------------------------

def enrich(tab, row_id, limit=24, stop=None, batch=6):
    """Localize the first titles of a cached row; True when something changed."""
    path = C.row_file(tab, row_id)
    data = C.read_json(path)
    ctx = Context(tab)
    if not data or (data.get('enriched') and data.get('lang') == ctx.language):
        return False
    from ..meta_policy import source_appearance
    if source_appearance():
        data['enriched'], data['lang'] = True, ctx.language
        C.write_json(path, data)
        return False
    # without a TMDb language the titles still get the art their catalog
    # lacks (logo, backdrop) and the badges (v5.10.114)
    language_key = ctx.language or '-'
    enricher = HeadlessEnricher(app())
    spec = find_spec(tab, row_id) or {'shape': data.get('shape') or 'poster'}
    changed = False
    processed = 0
    targets = list(data.get('items') or [])[:limit]
    for index, item in enumerate(targets):
        if (stop is not None and stop()) or processed >= batch:
            break
        src = item.get('src') or {}
        props = item.get('props') or {}
        if (props.get('kind') != 'work' or src.get('enriched') == language_key
                or src.get('enrich_failed') == language_key):
            continue
        tile = {'kind': 'work', 'media_type': src.get('media_type') or '', 'title': src.get('title') or '',
                'show': src.get('show') or '', 'year': src.get('year') or '', 'path': src.get('path') or '',
                'imdb_id': src.get('imdb_id') or '', 'tmdb_id': src.get('tmdb_id') or '',
                'studios': src.get('studios') or [],
                'plot': (item.get('info') or {}).get('plot') or '',
                'genres': list((item.get('info') or {}).get('genres') or []),
                'duration': (item.get('info') or {}).get('duration') or 0,
                'poster': (item.get('art') or {}).get('poster') or '',
                'fanart': (item.get('art') or {}).get('fanart') or '',
                'landscape': (item.get('art') or {}).get('landscape') or '',
                'clearlogo': (item.get('art') or {}).get('clearlogo') or ''}
        before = dict(tile)
        processed += 1
        try:
            enricher._enrich_now(tile, ctx.language or '')
        except Exception:
            src['enrich_failed'] = language_key
            item['src'] = src
            continue
        src['enriched'] = language_key
        if tile.get('imdb_id') and not src.get('imdb_id'):
            src['imdb_id'] = tile['imdb_id']
        if tile.get('tmdb_id') and not src.get('tmdb_id'):
            src['tmdb_id'] = tile['tmdb_id']
        info = item.setdefault('info', {})
        art = item.setdefault('art', {})
        if tile.get('plot') and tile['plot'] != before['plot']:
            info['plot'] = tile['plot']
            changed = True
        if tile.get('genres') and tile['genres'] != before['genres']:
            info['genres'] = list(tile['genres'])[:3]
            changed = True
        if tile.get('duration') and not before['duration']:
            info['duration'] = tile['duration']
            changed = True
        for key in ('clearlogo', 'fanart'):
            if tile.get(key) and tile[key] != before[key]:
                art[key] = tile[key]
                changed = True
        if tile.get('fanart') and not art.get('landscape'):
            art['landscape'] = tile['fanart']
        if tile.get('poster') and not before['poster']:
            art['poster'] = tile['poster']
            if spec.get('shape') != 'landscape':
                art['thumb'] = tile['poster']
            changed = True
        ids = item.setdefault('ids', {})
        for key, name in (('imdb_id', 'imdb'), ('tmdb_id', 'tmdb')):
            if tile.get(key) and not ids.get(name):
                ids[name] = tile[key]
        # the hero's meta line follows the new genres and runtime
        merged = {'kind': 'work', 'year': info.get('year') or '', 'rating': info.get('rating') or 0,
                  'duration': info.get('duration') or 0, 'genres': info.get('genres') or [],
                  'mpaa': info.get('mpaa') or ''}
        if tile.get('rating') and not info.get('rating'):
            info['rating'] = tile['rating']
            merged['rating'] = tile['rating']
            changed = True
        for name, value in (('dhs.studio_logo', tile.get('studio_logo') or ''),
                            ('dhs.studio_chip', '1' if tile.get('studio_logo_chip') else '')):
            if 'studio_logo' not in tile:
                continue  # focused badges are published separately, never erase source badges
            if value != props.get(name, ''):
                props[name] = value
                changed = True
        props['dhs.meta'] = ctx.meta(merged)
        props['dhs.rating'] = ctx.rating(merged)
        if tile.get('_studio_rows'):
            seed = json.dumps(tile['_studio_rows'][:3], ensure_ascii=False)
            if seed != props.get('dhs.studio_seed'):
                props['dhs.studio_seed'] = seed
                changed = True
        item['props'] = props
        item['src'] = src
    # a title in the next language pass is told apart from this one
    current = C.read_json(path)
    if current is not None and current.get('h') != data.get('h'):
        return False        # the row was read again meanwhile: that copy wins
    data['enriched'] = all((i.get('props') or {}).get('kind') != 'work'
                           or (i.get('src') or {}).get('enriched') == language_key
                           or (i.get('src') or {}).get('enrich_failed') == language_key for i in targets)
    data['lang'] = ctx.language
    if changed:
        data['h'] = C.content_hash(data.get('items') or [])
    C.write_json(path, data)
    if changed:
        # the spotlight shows the localized plots and logos too
        _follow_spot(tab, spec if spec.get('id') else (find_spec(tab, row_id) or {}))
    return changed


GIF_FETCH = 12          # collection covers fetched per row and pass


def focused_gif_target():
    try:
        layout = C.layout()
        slot = int(C.prop('dhs.fr') or 0)
        # v5.10.140: the focused control's id and the window's, not
        # getCondVisibility (a pause in Kodi's frame each, guistate.py)
        if C.served() == 'af3' or layout.hubs:
            container = int(xbmc.getInfoLabel('Window(Home).Property(TMDbHelper.WidgetContainer)') or 0)
            if not container or int(xbmc.getInfoLabel('System.CurrentControlId') or 0) != container:
                return None
        else:
            from .. import guistate
            if guistate.active() != guistate.HOME:
                return None
            container = layout.row_list(slot) if slot else layout.spotlight
        prefix = 'Container(%d).ListItem.Property(' % container
        key = xbmc.getInfoLabel(prefix + 'dhs.gif_key)') or ''
        row = xbmc.getInfoLabel(prefix + 'dhs.row)') or ''
        if key and ':' in row:
            tab, row_id = row.split(':', 1)
            if tab in C.TABS and row_id:
                return tab, row_id, key
    except Exception:
        pass
    return None


def focused_gif_key():
    target = focused_gif_target()
    return target[2] if target else ''


def fetch_gifs(tab, row_id, stop=None):
    """The animated covers of a collection row's cards, onto the device
    (skin.dexhub 3 plays a card's cover while it has the focus); True when a
    card got its cover (the row is written again: the caller bumps it)."""
    if not app().settings.flag('homeui_focus_gif', True):
        return False
    light = app().settings.flag('homeui_light_mode', True)
    wanted = focused_gif_key() if light else ''
    if light and not wanted:
        return False
    path = C.row_file(tab, row_id)
    data = C.read_json(path)
    if not data or data.get('kind') != 'collection':
        return False
    changed = False
    fetched = 0
    for item in data.get('items') or []:
        if stop is not None and stop():
            break
        if (item.get('props') or {}).get('kind') != 'folder' or (item.get('art') or {}).get('gif'):
            continue
        if light and (item.get('props') or {}).get('dhs.gif_key') != wanted:
            continue
        url = (item.get('src') or {}).get('gif_url') or ''
        if not url:
            continue
        local = media().local(url)
        if not local and fetched < GIF_FETCH:
            fetched += 1
            local = media().fetch(url)
        if local:
            item.setdefault('art', {})['gif'] = local
            changed = True
    if changed:
        data['h'] = C.content_hash(data.get('items') or [])
        C.write_json(path, data)
    return changed
