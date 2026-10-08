# -*- coding: utf-8 -*-
"""Row and tile models for the Dex Hub Home window.

Every catalog row is filled by running the same Dex Hub route the native
listing uses (see capture.py), so artwork, click paths and context menus stay
identical to what the user already knows. Collection rows are built from the
Nuvio collection objects themselves, because the native listing flattens the
details that make Nuvio's collection rows look the way they do: the cover
shape, the hidden title, the title logo, the hero backdrop and the animated
focus art.
"""
import json
import re
import threading
import time
from urllib.parse import parse_qsl, urlsplit

from .. import capture as _capture

ADDON_ID = 'plugin.video.dexhub'
BASE = 'plugin://%s/' % ADDON_ID

MORE_LABELS = frozenset(('المزيد', 'More', 'more', 'Load more', 'Next page'))
PLAYER_ACTIONS = frozenset((
    'tmdb_player', 'streams', 'episode_streams', 'play', 'play_item',
    'play_next_same_source', 'plex_play', 'emby_play', 'jellyfin_play',
    'silo_play', 'native_resume', 'cw_resume', 'cw_play_from_start', 'home_vod_play'))
WORK_FOLDER_ACTIONS = frozenset(('item_open', 'series_meta', 'season', 'seasons'))
# v5.10.108: a server show's seasons and episodes (Plex, Emby, Jellyfin,
# Silo) open in the Home's own grid; Kodi's Videos window could lose the way
# back to Dex Hub
SERVER_FOLDER_ACTIONS = frozenset(('plex_children', 'emby_children', 'jellyfin_children',
                                   'silo_series', 'silo_episodes'))


def is_show_page(params):
    """A server show's seasons or a season's episodes (not Plex's recently
    added or a collection, which use plex_children too)."""
    params = params or {}
    action = params.get('action') or ''
    if action == 'plex_children':
        return str(params.get('key') or '').startswith('/library/metadata/')
    return action in SERVER_FOLDER_ACTIONS
WORK_MEDIATYPES = frozenset(('movie', 'tvshow', 'episode', 'season', 'musicvideo'))

SHAPES = ('poster', 'landscape', 'square')


def _txt(value):
    return str(value or '').strip()


# v5.10.110: Continue Watching puts a grey block naming the source (server,
# library, quality, size, folder) on top of the plot, for other skins' info
# panels. The Home names the source on the meta line, and the block took the
# hero's first lines, so only a line of the story showed.
_FACTS_BLOCK = re.compile(r'^\s*\[COLOR grey\].*?\[/COLOR\]\s*', re.S)


def strip_facts(plot):
    text = _txt(plot)
    match = _FACTS_BLOCK.match(text)
    return text[match.end():].strip() if match else text


def params_of(path):
    try:
        parts = urlsplit(str(path or ''))
    except Exception:
        return {}
    return dict(parse_qsl(parts.query, keep_blank_values=True))


def is_dexhub_path(path):
    return str(path or '').startswith(BASE)


def _list(value):
    if isinstance(value, (list, tuple)):
        return [_txt(v) for v in value if _txt(v)]
    text = _txt(value)
    if not text:
        return []
    return [p.strip() for p in re.split(r'\s*[/,|]\s*', text) if p.strip()]


def _num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _self_art(url):
    low = str(url or '').lower()
    return ADDON_ID in low and ('/resources/media/' in low or 'fanart.jpg' in low
                                or 'icon.png' in low)


def _usable_art(url):
    """An artwork address Kodi can load here ('' for one it cannot)."""
    url = _txt(url)
    try:
        from ..art import unreachable_art
        if unreachable_art(url):
            return ''
    except Exception:
        pass
    return url


def _clean_art(url):
    url = _txt(url)
    return '' if (not url or _self_art(url)) else url


_SXE_RE = re.compile(r'\bS(\d{1,3})\s*[:.\-]?\s*E(\d{1,4})\b', re.I)
_NXN_RE = re.compile(r'\b(\d{1,2})x(\d{1,3})\b')
_AR_SE_RE = re.compile(u'\u0627\u0644\u0645\u0648\u0633\u0645\\s*(\\d+).{0,12}?\u0627\u0644\u062d\u0644\u0642\u0629\\s*(\\d+)')
_PROGRESS_ROW_RE = re.compile(
    u'continue|resume|next\\s*up|up\\s*next|in\\s*progress|'
    u'\u0645\u062a\u0627\u0628\u0639\u0629|\u0627\u0633\u062a\u0643\u0645\u0627\u0644|'
    u'\u0627\u0644\u062d\u0644\u0642\u0629 \u0627\u0644\u062a\u0627\u0644\u064a\u0629', re.I)


def episode_title(title, show, season='', episode=''):
    """The episode's own name out of a Continue Watching title.

    The native row titles an episode "Show — S01E03" (or "S01E03 — Name"
    when the show is unknown); the name is whatever is left once the show
    and the SxxEyy marker are taken out.
    """
    text = _txt(title)
    if not text:
        return ''
    stripped = _SXE_RE.sub('', text)
    show = _txt(show)
    if show:
        # the show's name only where the native title puts it, never inside
        # the episode's own name ("Broken Arrow" in the series "Arrow")
        sep = u'[\\s\u2014\u2013\\-:\u00b7|\u2022]*'
        if stripped.strip() == show:
            return ''
        stripped = re.sub(u'^\\s*' + re.escape(show) + u'(?=' + sep + u'$|[\\s]*[\u2014\u2013\\-:\u00b7|\u2022])', '', stripped)
        stripped = re.sub(u'(?:[\u2014\u2013\\-:\u00b7|\u2022]\\s*|^\\s*)' + re.escape(show) + u'\\s*$', '', stripped)
    stripped = re.sub(u'^[\\s\u2014\u2013\\-:\u00b7|\u2022]+|[\\s\u2014\u2013\\-:\u00b7|\u2022]+$', '', stripped).strip()
    if not stripped or stripped == _txt(show):
        return ''
    if re.match(r'^(?:episode|\u0627\u0644\u062d\u0644\u0642\u0629)\s*\d+$', stripped, re.I):
        return ''
    return stripped


def episode_hint(*texts):
    """"S1:E3" when a label or its second line names an episode, else ''."""
    for text in texts:
        text = _txt(text)
        if not text:
            continue
        m = _SXE_RE.search(text) or _NXN_RE.search(text) or _AR_SE_RE.search(text)
        if m:
            try:
                return 'S%d:E%d' % (int(m.group(1)), int(m.group(2)))
            except Exception:
                continue
    return ''


def is_progress_row(title):
    """Continue Watching / Next Up rows of any source (landscape, titles shown)."""
    return bool(_PROGRESS_ROW_RE.search(_txt(title)))


def drop_brand_logos(tiles):
    """Clear a "title logo" that is really the add-on's own logo.

    A catalog whose items have no logo of their own sometimes carries the
    add-on's logo in every item (in the metadata or as fallback art). A real
    title logo belongs to one title, so a logo shared by several different
    titles on one page, or one that is simply the tile's own poster, goes.
    """
    owners = {}
    for tile in tiles or []:
        logo = tile.get('clearlogo') or ''
        if logo:
            owners.setdefault(logo, set()).add(tile.get('imdb_id') or tile.get('path') or tile.get('title') or id(tile))
    for tile in tiles or []:
        logo = tile.get('clearlogo') or ''
        if not logo:
            continue
        if len(owners.get(logo) or ()) > 1 or logo in (tile.get('poster'), tile.get('landscape')):
            tile['clearlogo'] = ''
    return tiles


class Row(object):
    """One horizontal row. ``loader`` returns (tiles, more_params)."""

    def __init__(self, key, title, shape='poster', kind='catalog', loader=None,
                 tiles=None, subtitle='', meta=None, priority=5):
        self.key = key
        self.title = title
        self.subtitle = subtitle
        self.shape = shape if shape in SHAPES else 'poster'
        self.kind = kind
        self.loader = loader
        self.tiles = list(tiles or [])
        self.state = 'ready' if (tiles is not None and loader is None) else 'pending'
        if tiles is not None and loader is None and not self.tiles:
            self.state = 'empty'
        self.selected = 0
        self.more = None
        self.meta = dict(meta or {})
        self.priority = priority
        self.version = 0
        self.error = ''
        self.loaded_at = 0.0

    @property
    def visible(self):
        return self.state in ('pending', 'loading') or bool(self.tiles)

    def snapshot(self):
        return {
            'key': self.key, 'title': self.title, 'subtitle': self.subtitle,
            'shape': self.shape, 'kind': self.kind, 'meta': self.meta,
            'more': self.more,
            'tiles': [strip_tile(t) for t in self.tiles[:40]],
        }


_TAGS_RE = re.compile(r'\[/?(?:B|I|COLOR|UPPERCASE|LOWERCASE|CAPITALIZE|LIGHT|CR)\b[^\]]*\]', re.I)


def initials(label):
    """Two letters for a card without art (Kodi's [B] and [COLOR] tags left out)."""
    text = _TAGS_RE.sub('', str(label or '')).strip()
    return text[:2].upper()


def strip_tile(tile):
    return {k: v for k, v in (tile or {}).items() if not k.startswith('_')}


# --------------------------------------------------------------------------
# captured Dex Hub items -> tiles
# --------------------------------------------------------------------------

def _media_type(info, props, path):
    mt = _txt(info.get('mediatype')).lower()
    if mt == 'movie':
        return 'movie'
    if mt in ('tvshow', 'season'):
        return 'series'
    if mt == 'episode':
        return 'episode'
    p = params_of(path)
    raw = _txt(p.get('media_type') or p.get('video_type') or props.get('media_type') or props.get('dexhub.media_type')).lower()
    if raw in ('series', 'tv', 'show', 'anime', 'tvshow'):
        return 'series'
    if raw == 'episode' or (p.get('season') and p.get('episode')):
        return 'episode'
    if raw == 'movie':
        return 'movie'
    return ''


def tile_from_entry(entry, default_shape='poster'):
    info = dict(entry.get('info') or {})
    art = dict(entry.get('art') or {})
    props = dict(entry.get('props') or {})
    ids = dict(entry.get('ids') or {})
    path = entry.get('path') or ''
    label = _txt(entry.get('label'))
    mediatype = _txt(info.get('mediatype')).lower()
    action = params_of(path).get('action', '') if is_dexhub_path(path) else ''
    work = (props.get('dexhub.work_item') == '1' or mediatype in WORK_MEDIATYPES
            or action in PLAYER_ACTIONS)
    title = _txt(info.get('title')) or label
    show = _txt(info.get('tvshowtitle'))
    for key in ('imdb_id', 'tmdb_id', 'tvdb_id'):
        if not ids.get(key) and props.get(key):
            ids[key] = props.get(key)
    route = params_of(path)
    for key in ('imdb_id', 'tmdb_id', 'tvdb_id'):
        if not ids.get(key) and _txt(route.get(key)):
            ids[key] = _txt(route.get(key))
    cid = _txt(route.get('canonical_id'))
    if not ids.get('imdb_id'):
        m = re.match(r'^(tt\d{5,10})', cid)
        if m:
            ids['imdb_id'] = m.group(1)
    if not ids.get('tmdb_id'):
        m = re.match(r'^tmdb[:_](\d+)', cid, re.I)
        if m:
            ids['tmdb_id'] = m.group(1)
    poster = _clean_art(art.get('poster') or art.get('tvshow.poster') or art.get('thumb')
                        or art.get('icon'))
    fanart = _clean_art(art.get('fanart') or art.get('landscape') or props.get('fanart_image'))
    landscape = _clean_art(art.get('landscape') or art.get('thumb') or art.get('fanart'))
    clearlogo = _clean_art(art.get('clearlogo') or art.get('logo') or art.get('tvshow.clearlogo'))
    try:
        progress = int(float(props.get('WatchedProgress') or props.get('PercentPlayed') or 0))
    except Exception:
        progress = 0
    label2 = _txt(entry.get('label2'))
    episode_name = ''
    if mediatype == 'episode':
        episode_name = episode_title(title, show, info.get('season'), info.get('episode'))
    tile = {
        'kind': 'work' if work else 'nav',
        'label': label,
        'title': title,
        'show': show,
        'path': path,
        'folder': bool(entry.get('folder')),
        'action': action,
        'media_type': _media_type(info, props, path),
        'plot': strip_facts(info.get('plot')),
        'tagline': _txt(info.get('tagline')),
        'year': _txt(info.get('year')) if _txt(info.get('year')) not in ('0',) else '',
        'rating': round(_num(info.get('rating')), 1),
        'genres': _list(info.get('genre'))[:3],
        'studio': _list(info.get('studio'))[:1],
        'duration': int(_num(info.get('duration'))),
        'mpaa': _txt(info.get('mpaa')),
        'season': _txt(info.get('season')),
        'episode': _txt(info.get('episode')),
        'poster': poster,
        'fanart': fanart,
        'landscape': landscape,
        'clearlogo': clearlogo,
        'progress': max(0, min(100, progress)),
        'playcount': int(_num(info.get('playcount'))),
        'resume': _num(info.get('resumetime')),
        'total': _num(info.get('totaltime')),
        'imdb_id': _txt(ids.get('imdb_id')),
        'tmdb_id': _txt(ids.get('tmdb_id')),
        'tvdb_id': _txt(ids.get('tvdb_id')),
        'menu': [list(m) for m in (entry.get('menu') or [])],
        'shape': default_shape,
        'provider': _txt(props.get('dexhub.provider_name')),
        'label2': label2,
        # quality and size of a server copy, for the meta line (v5.10.110)
        'facts': ' • '.join(x for x in (_txt(props.get('dexhub.quality')),
                                        _txt(props.get('dexhub.size'))) if x),
        'episode_name': episode_name,
        'season_card': mediatype == 'season',
        'hint': episode_hint(label, label2),
    }
    if not tile['year']:
        m = re.search(r'(19|20)\d{2}', _txt(info.get('premiered')))
        tile['year'] = m.group(0) if m else ''
    return tile


def split_more(entries):
    """(work/nav entries, params of the "More" pager item or None)."""
    items, more = [], None
    for entry in entries or []:
        label = _txt(entry.get('label'))
        title = _txt((entry.get('info') or {}).get('title'))
        props = entry.get('props') or {}
        if ((label in MORE_LABELS or title in MORE_LABELS)
                and props.get('dexhub.work_item') != '1'
                and is_dexhub_path(entry.get('path'))):
            more = params_of(entry.get('path'))
            continue
        items.append(entry)
    return items, more


def keep_entry(entry, works_only=False):
    """Drop Dex Hub toolbar rows (filters, sort, refresh) from a captured page."""
    props = entry.get('props') or {}
    info = entry.get('info') or {}
    if props.get('dexhub.work_item') == '1':
        return True
    if _txt(info.get('mediatype')).lower() in WORK_MEDIATYPES:
        return True
    if works_only:
        return False
    path = _txt(entry.get('path'))
    action = params_of(path).get('action', '')
    if action in ('filters_menu', 'clear_filters', 'select_filter', 'sort_menu',
                  'catalog_toolbar', 'refresh', 'noop', 'cache_clear'):
        return False
    label = _txt(entry.get('label'))
    if label.startswith('[COLOR') and ('◉' in label or '⟲' in label or '⚙' in label):
        return False
    return bool(path)


# v5.10.106 (after Nuvio Hub): pages read ahead of the user, e.g. the rows of
# a collection folder while the cursor rests on its card, kept for a few
# minutes so opening the folder takes them from memory
_MEMO = {}
_MEMO_ORDER = []
_MEMO_TTL = 300.0
_MEMO_LIMIT = 48
_memo_lock = threading.Lock()


# route params that do not change what a page lists (v5.10.108)
_MEMO_SKIP = ('ui_seed_key', 'dh_home', 'ui_poster', 'ui_fanart', 'ui_clearlogo')


def _memo_key(params):
    return json.dumps(dict((k, v) for k, v in dict(params or {}).items() if k not in _MEMO_SKIP),
                      sort_keys=True)


def page_memo_key(params):
    return _memo_key(params)


def remember_page(params, tiles, more):
    key = _memo_key(params)
    with _memo_lock:
        if key in _MEMO:
            _MEMO_ORDER.remove(key)
        _MEMO[key] = (time.time(), list(tiles or []), more)
        _MEMO_ORDER.append(key)
        while len(_MEMO_ORDER) > _MEMO_LIMIT:
            _MEMO.pop(_MEMO_ORDER.pop(0), None)


def recall_page(params):
    """(tiles, more) read ahead for these params in the last few minutes, or None."""
    key = _memo_key(params)
    with _memo_lock:
        hit = _MEMO.get(key)
    if not hit or time.time() - hit[0] > _MEMO_TTL:
        return None
    return [dict(t) for t in hit[1]], hit[2]


def known_page(params):
    return recall_page(params) is not None


def take_page(params):
    """Like recall_page, and forgets it (a page read ahead is used once)."""
    key = _memo_key(params)
    with _memo_lock:
        hit = _MEMO.pop(key, None)
        if hit is not None:
            try:
                _MEMO_ORDER.remove(key)
            except ValueError:
                pass
    if not hit or time.time() - hit[0] > _MEMO_TTL:
        return None
    return [dict(t) for t in hit[1]], hit[2]


def capture_tiles(params, api, shape='poster', works_only=False, limit=60, patience=0.0):
    entries, _elapsed = _capture.run_route(params, api, patience=patience)
    if not entries and _capture.last_error():
        # a crash inside the route is a failure, not an empty catalog
        raise RuntimeError(_capture.last_error())
    entries, more = split_more(entries)
    tiles = []
    for entry in entries:
        if not keep_entry(entry, works_only=works_only):
            continue
        tile = tile_from_entry(entry, default_shape=shape)
        tile['_src_li'] = entry.get('listitem')
        tiles.append(tile)
        if len(tiles) >= limit:
            break
    return drop_brand_logos(tiles), more


# v5.10.117: Continue Watching and Next Up show on three tabs (all, movies,
# series) and every tab read the whole route again: kodi.log on a Ugoos had
# each refresh after playback read Continue Watching three times, 2 to 17
# seconds each. One read serves the three tabs for a few seconds, as long as
# no progress was written meanwhile.
_PROGRESS_MEMO = {}
_PROGRESS_LOCK = threading.Lock()
_PROGRESS_MEMO_SECONDS = 25.0


def _progress_mark():
    """Changes when watch progress is written (the playback store's files)."""
    try:
        import os
        from ..dexhub import playback_store
        path = playback_store.DB_PATH
    except Exception:
        return None
    out = []
    for name in (path, path + '-wal'):
        try:
            out.append(os.path.getmtime(name))
        except OSError:
            out.append(0.0)
    return tuple(out)


def progress_tiles(action, api, patience=0.0, limit=30):
    """(tiles, more) of a progress route (continue, nextup), read once for
    the tabs that show it."""
    from ..dexhub import client as _client
    fresh = getattr(_client._FRESH, 'on', False)
    with _PROGRESS_LOCK:
        mark = _progress_mark()
        hit = _PROGRESS_MEMO.get(action)
        if (hit and hit[1] == mark and time.monotonic() - hit[0] < _PROGRESS_MEMO_SECONDS
                and not (fresh and time.monotonic() - hit[0] > 5.0)):
            return [dict(t) for t in hit[2]], hit[3]
        tiles, more = capture_tiles({'action': action}, api, 'landscape',
                                    works_only=True, limit=limit, patience=patience)
        _PROGRESS_MEMO[action] = (time.monotonic(), _progress_mark(), list(tiles), more)
        return [dict(t) for t in tiles], more


def forget_progress():
    with _PROGRESS_LOCK:
        _PROGRESS_MEMO.clear()


# --------------------------------------------------------------------------
# search results -> tiles (v5.10.102)
# --------------------------------------------------------------------------

def _runtime_seconds(value):
    """'2h 10m', '130 min', 'PT2H10M' or 130 (minutes) as seconds."""
    if isinstance(value, (int, float)):
        return int(value) * 60 if value < 1000 else int(value)
    text = _txt(value)
    if not text:
        return 0
    if text.isdigit():
        return int(text) * 60
    hours = re.search(r'(\d+)\s*h', text, re.I)
    minutes = re.search(r'(\d+)\s*m(?!s)', text, re.I)
    if hours or minutes:
        return ((int(hours.group(1)) if hours else 0) * 60 + (int(minutes.group(1)) if minutes else 0)) * 60
    m = re.search(r'(\d+)', text)
    return int(m.group(1)) * 60 if m else 0


def _rating(value):
    try:
        return round(float(_txt(value).split('/')[0].replace(',', '.')), 1)
    except Exception:
        return 0.0


def search_tile(entry):
    """A tile for one result of Dex Hub's unified search (plugin.hub_search_collect)."""
    entry = entry or {}
    path = _txt(entry.get('path'))
    title = _txt(entry.get('title'))
    if not path or not title:
        return None
    media = _txt(entry.get('media_type')).lower()
    person = media in ('person', 'people') or bool(entry.get('is_person'))
    if person:
        media_type = ''
    elif media == 'movie':
        media_type = 'movie'
    elif media in ('series', 'anime', 'show', 'tvshow', 'tv'):
        media_type = 'series'
    else:
        media_type = ''
    dexhub = is_dexhub_path(path)
    source = _txt(entry.get('source_label'))
    tile = {
        'kind': 'nav' if person else 'work',
        'label': title,
        'title': title,
        'show': '',
        'path': path,
        'folder': _txt(entry.get('is_folder')).lower() in ('1', 'true', 'yes'),
        'action': params_of(path).get('action', '') if dexhub else '',
        'media_type': media_type,
        'plot': _txt(entry.get('plot')) or (source if person else ''),
        'tagline': '',
        'year': _txt(entry.get('year')),
        'rating': _rating(entry.get('rating')),
        'genres': _list(entry.get('genre'))[:3],
        'studio': [],
        'duration': _runtime_seconds(entry.get('runtime')),
        'mpaa': '',
        'season': '',
        'episode': '',
        'poster': _clean_art(entry.get('poster')),
        'fanart': _clean_art(entry.get('fanart')),
        'landscape': _clean_art(entry.get('landscape') or entry.get('fanart')),
        'clearlogo': _clean_art(entry.get('clearlogo')),
        'progress': 0,
        'resume': 0.0,
        'total': 0.0,
        'imdb_id': _txt(entry.get('imdb_id')),
        'tmdb_id': _txt(entry.get('tmdb_id')),
        'tvdb_id': _txt(entry.get('tvdb_id')),
        'menu': [],
        'shape': 'poster',
        'provider': source,
        'label2': '',
        'episode_name': '',
        'hint': '',
        '_score': float(entry.get('score') or 0.0),
    }
    if not tile['imdb_id'] or not tile['tmdb_id']:
        # the click path names the ids the result has
        route = params_of(path)
        for key in ('imdb_id', 'tmdb_id', 'tvdb_id'):
            if not tile[key] and _txt(route.get(key)):
                tile[key] = _txt(route.get(key))
        cid = _txt(route.get('canonical_id'))
        m = re.match(r'^(tt\d{5,10})', cid)
        if m and not tile['imdb_id']:
            tile['imdb_id'] = m.group(1)
        m = re.match(r'^tmdb[:_](\d+)', cid, re.I)
        if m and not tile['tmdb_id']:
            tile['tmdb_id'] = m.group(1)
    if not tile['imdb_id'].startswith('tt'):
        tile['imdb_id'] = ''
    return tile


# --------------------------------------------------------------------------
# Nuvio collection folders -> tiles
# --------------------------------------------------------------------------

def folder_shape(folder):
    payload = (folder or {}).get('payload') or {}
    shape = _txt(payload.get('tileShape')).upper()
    if not shape:
        layout = _txt((folder or {}).get('layout')).lower()
        shape = 'LANDSCAPE' if layout in ('wide', 'landscape', 'banner') else 'POSTER'
    return {'POSTER': 'poster', 'LANDSCAPE': 'landscape', 'SQUARE': 'square',
            'WIDE': 'landscape', 'BANNER': 'landscape'}.get(shape, 'poster')


def _media_filters():
    """(folder_supports_media, source_matches_media, compat) or Nones."""
    try:
        from .. import collection_sets as cs
        from .. import nuvio_collection_ui as ncu
        return cs.folder_supports_media, cs.source_matches_media, ncu._compat_nuvio_source
    except Exception:
        return None, None, None


def matching_sources(folder, media_filter=''):
    """Indexes of the folder's sources shown for ``media_filter`` (all when empty)."""
    sources = ((folder or {}).get('payload') or {}).get('sources') or []
    if not media_filter:
        return list(range(len(sources)))
    _folder_ok, source_ok, compat = _media_filters()
    if source_ok is None:
        return list(range(len(sources)))
    out = []
    for index, source in enumerate(sources):
        try:
            if source_ok(compat(source), media_filter):
                out.append(index)
        except Exception:
            out.append(index)
    return out


def folder_tiles(entry, set_id, wrap=None, gif_enabled=True, media_filter=''):
    """Tiles for one Nuvio collection (nuvioGroup entry).

    ``media_filter`` ('movie' or 'series') applies the same folder and source
    filter as the native Movies and TV Shows pages.
    """
    wrap = wrap or (lambda url: url)
    payload = (entry or {}).get('payload') or {}
    folders = [f for f in (payload.get('folders') or []) if isinstance(f, dict)]
    folder_ok = _media_filters()[0] if media_filter else None
    tiles = []
    for folder in folders:
        if folder_ok is not None:
            try:
                if not folder_ok(folder, media_filter):
                    continue
            except Exception:
                pass
        fp = folder.get('payload') or {}
        # The folder filter decides visibility, as on the native Movies and
        # TV Shows pages; sources whose type cannot be told still open.
        indexes = matching_sources(folder, media_filter) or matching_sources(folder, '')
        # v5.10.117: a cover only Nuvio's own app can show (its local asset
        # server) is no cover here: the folder shows its name instead
        cover = _usable_art(folder.get('poster')) or _usable_art(folder.get('background'))
        hero = _usable_art(folder.get('background')) or cover
        gif = _usable_art(fp.get('focusGifUrl')) if (gif_enabled and fp.get('focusGifEnabled', True)) else ''
        tiles.append({
            'kind': 'folder',
            'label': _txt(folder.get('name')),
            'title': _txt(folder.get('name')),
            'hide_title': bool(folder.get('hide_title')) and bool(cover),
            'shape': folder_shape(folder),
            'poster': wrap(cover),
            'landscape': wrap(cover),
            'fanart': wrap(hero),
            'clearlogo': wrap(_usable_art(folder.get('clearlogo'))),
            'gif_url': gif,
            'emoji': _txt(fp.get('coverEmoji')),
            'hero_video': _txt(fp.get('heroVideoUrl')),
            'source_count': len(indexes),
            'source_indexes': indexes,
            'group_title': _txt(entry.get('name')),
            'group_fanart': wrap(_usable_art(entry.get('background'))),
            'group_logo': wrap(_usable_art(entry.get('clearlogo'))),
            'show_all': bool(((entry.get('payload') or {}).get('showAllTab', True))),
            'folder_ref': {'set_id': set_id, 'group_id': _txt(entry.get('id')),
                           'folder_id': _txt(folder.get('id')),
                           'media_filter': media_filter or ''},
            'plot': '',
            'path': '',
            'folder': True,
            'menu': [],
        })
    return tiles


def dominant_shape(tiles, default='poster'):
    counts = {}
    for tile in tiles or []:
        shape = tile.get('shape') or default
        counts[shape] = counts.get(shape, 0) + 1
    if not counts:
        return default
    return max(counts.items(), key=lambda kv: kv[1])[0]


# --------------------------------------------------------------------------
# row sets
# --------------------------------------------------------------------------

def _project_all(contract, items, collections):
    """Nuvio Home in one list: pinned collections first, then the saved order."""
    items = [dict(r) for r in (items or []) if isinstance(r, dict)]
    out, pinned = [], set()
    for summary in collections or []:
        if not isinstance(summary, dict) or not summary.get('pin_to_top'):
            continue
        cid = _txt(summary.get('id'))
        if not cid:
            continue
        match = None
        for item in items:
            if item.get('is_collection') and cid in contract.collection_id_variants(item.get('collection_id')):
                match = item
                break
        if match is not None and match.get('enabled') is False:
            continue
        out.append(dict(match or {'is_collection': True, 'collection_id': cid,
                                  'key': contract.collection_key(cid), 'enabled': True,
                                  'order': -1, 'type': '', 'addon_id': '', 'catalog_id': ''}))
        pinned.update(contract.collection_id_variants(cid))
    for item in items:
        if item.get('enabled') is False:
            continue
        if item.get('is_collection'):
            if contract.collection_id_variants(item.get('collection_id')) & pinned:
                continue
        out.append(dict(item))
    return out


def nuvio_tabs(api, media):
    """Nuvio Home tabs for 'all', 'movie' or 'series' (same builders as native)."""
    from .. import nuvio_home_ui as nh
    if media in ('movie', 'series'):
        _snapshot, _items, tabs = nh._media_model(media)
        return tabs, bool(_items)
    snapshot = nh._mirror.load()
    specs = nh._account_provider_specs(snapshot)
    prefs = nh._profile_prefs.normalized_tmdb()
    completed = nh._contract.complete_home_items(
        snapshot.get('items') or [], snapshot.get('collections') or [], specs,
        follow_addons_order=prefs.get('follow_addons_order', False))
    items = _project_all(nh._contract, completed, snapshot.get('collections') or [])
    index = nh._catalog_index(specs)
    summaries = {}
    for summary in snapshot.get('collections') or []:
        if isinstance(summary, dict):
            for variant in nh._contract.collection_id_variants(summary.get('id')):
                summaries.setdefault(str(variant), summary)
    tabs = []
    for item in items:
        try:
            if item.get('is_collection'):
                tab = nh._collection_tab(item, '', summaries=summaries)
            else:
                media_type = nh._contract.catalog_type(item.get('type')) or 'movie'
                tab = nh._catalog_tab(item, media_type, catalog_index=index)
        except Exception:
            tab = None
        if tab:
            tabs.append(tab)
    return tabs, bool(items)


def betterposters_tabs(api, media):
    try:
        from .. import nuvio_home_ui as nh
        kinds = ('movie', 'series') if media not in ('movie', 'series') else (media,)
        out = []
        for kind in kinds:
            out.extend(nh._bp_home.tabs(kind, api.ADDON, api.build_url, nh._betterposters_art))
        return out
    except Exception:
        return []


def fallback_collection_entries():
    """Collections from the active local set (Kaptain built-in by default)."""
    try:
        from .. import collection_sets as cs
        rows = cs.active_sets() or []
    except Exception:
        return []
    out = []
    for row in rows:
        set_id = _txt(row.get('id'))
        for entry in row.get('entries') or []:
            if isinstance(entry, dict) and entry.get('kind') == 'nuvioGroup':
                out.append((set_id, entry))
    return out
