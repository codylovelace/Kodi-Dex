# -*- coding: utf-8 -*-
"""Picture previews while seeking (Kodi-Dex).

Nothing here runs while a video simply plays. The work is split so the
playing video is never competed with:

  play start      CompanionPlayer.onAVStarted calls start(): the source of
                  the previews is chosen from the play context (no network)
  30 s later      one background thread copies the server's previews to
                  special://temp/dexhub_trick/ at a capped speed, or asks the
                  preview container (docker/seekthumbs) to pre-scan the stream
  a seek          skin.dexhub's timer "dexhub_seekthumbs" (Timers.xml) sends
                  NotifyAll(plugin.video.dexhub,dexhub_seek_start); the
                  service's monitor calls seek_started(), and a thread follows
                  Player.SeekTime every 100 ms until the seek ends, setting
                  the Home properties DialogSeekBar.xml draws

Sources, best first:

  Plex      the part's BIF index (/library/parts/<id>/indexes/sd); until it
            is copied, the server's image for one moment (.../sd/<ms>)
  Emby      /Videos/<id>/index.bif
  Jellyfin  trickplay tile sheets (Jellyfin 10.9+, /Videos/<id>/Trickplay/);
  (Silo)    one sheet holds a grid of frames, cropped by the skin
  any       the preview container on the user's Docker host grabs frames
            with ffmpeg (Stremio and debrid streams, and server items the
            server has no previews for)

A BIF file is kept whole on disk; a frame is cut out of it (a byte copy, no
image library) the first time the seek reaches it.
"""
import bisect
import hashlib
import json
import os
import re
import shutil
import struct
import threading
import time
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

SENDER = 'plugin.video.dexhub'
SEEK_START = 'dexhub_seek_start'
SEEK_STOP = 'dexhub_seek_stop'

# Home window properties read by skin.dexhub (Timers.xml, DialogSeekBar.xml)
P_READY = 'dexhub.seekthumb.ready'      # 'true' while the playing item has previews
P_PATH = 'dexhub.seekthumb'             # the image (a file or a URL)
P_MODE = 'dexhub.seekthumb.mode'        # 'frame' or 'tile'
P_COL = 'dexhub.seekthumb.col'          # tile mode: the frame's column, 0-9
P_ROW = 'dexhub.seekthumb.row'          # tile mode: the frame's row, 0-9
P_ASPECT = 'dexhub.seekthumb.aspect'    # tile mode: the frame shape (ASPECTS)

START_DELAY = 30.0          # seconds after AV start before any download
RATE = 2 * 1024 * 1024      # bytes per second, at most, for the copy
MAX_BYTES = 96 * 1024 * 1024
POLL = 0.1                  # seconds between two reads of Player.SeekTime
SEEK_GRACE = 1.0            # seconds Player.Seeking may be false before the follow ends
SEEK_LIMIT = 300.0          # a follow never runs longer than this
URL_STEP_MS = 10000         # images by URL are asked on a 10 s grid (cache hits)
TIMEOUT = 15
KEEP_SECONDS = 24 * 3600
KEEP_BYTES = 400 * 1024 * 1024

# Tile sheets the skin can crop: 10x10 frames, these frame shapes (width /
# height x 100). The skin has one cropping list per shape.
TILE_GRID = (10, 10)
ASPECTS = (133, 178, 185, 200, 220, 239)

BIF_MAGIC = b'\x89BIF\r\n\x1a\n'

_LOCK = threading.Lock()
_STATE = {'session': None}


def _log(msg, level=xbmc.LOGDEBUG):
    try:
        xbmc.log('[DexHub] seekthumb: %s' % msg, level)
    except Exception:
        pass


def _home():
    return xbmcgui.Window(10000)


def _setting(name, default=''):
    try:
        value = xbmcaddon.Addon(SENDER).getSetting(name)
    except Exception:
        return default
    return default if value in (None, '') else value


def root_dir():
    return xbmcvfs.translatePath('special://temp/dexhub_trick/')


# ------------------------------------------------------------------- BIF
def parse_bif_index(head):
    """The frame table of a BIF file: (times_ms, offsets).

    offsets has one entry more than times (the end of the last frame).
    Format: Roku's BIF spec, as written by Plex and Emby: 8 byte magic,
    uint32 version, uint32 frame count, uint32 ms per time unit (0 = 1000),
    44 reserved bytes, then count + 1 pairs of uint32 (time unit, offset),
    the last time being 0xffffffff."""
    if len(head) < 64 or head[:8] != BIF_MAGIC:
        raise ValueError('not a BIF file')
    count, unit = struct.unpack('<II', head[12:20])
    unit = unit or 1000
    need = 64 + (count + 1) * 8
    if count <= 0 or len(head) < need:
        raise ValueError('BIF index incomplete (%d frames)' % count)
    times, offsets = [], []
    for i in range(count + 1):
        stamp, offset = struct.unpack('<II', head[64 + i * 8:72 + i * 8])
        offsets.append(offset)
        if i < count:
            times.append(stamp * unit)
    if any(b < a for a, b in zip(offsets, offsets[1:])):
        raise ValueError('BIF offsets out of order')
    return times, offsets


def frame_index(times, ms):
    """The frame shown at ms: the last frame starting at or before it."""
    if not times:
        return -1
    return max(0, bisect.bisect_right(times, max(0, ms)) - 1)


# ------------------------------------------------------------- seek time
def parse_seek_time(label):
    """Player.SeekTime(hh:mm:ss) as ms; None when it is not a time."""
    parts = (label or '').strip().split(':')
    if len(parts) not in (2, 3):
        return None
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        nums.insert(0, 0)
    hours, mins, secs = nums
    if hours < 0 or mins < 0 or secs < 0:
        return None
    return ((hours * 60 + mins) * 60 + secs) * 1000


def aspect_bucket(width, height):
    if not width or not height:
        return 178
    ratio = 100.0 * width / height
    return min(ASPECTS, key=lambda a: abs(a - ratio))


def split_kodi_url(url):
    """A Kodi path 'url|Header=value&...' as (url, headers dict)."""
    base, _, tail = (url or '').partition('|')
    return base, dict(parse_qsl(tail, keep_blank_values=True)) if tail else {}


# --------------------------------------------------------------- sources
class Source(object):
    """Where the previews of one playing item come from."""
    name = 'none'

    def prefetch(self, stop):
        """Background work before any seek (runs START_DELAY after AV start)."""

    def frame(self, ms):
        """(key, props) for the frame at ms; key changes when the image does."""
        return None, None


class BifSource(Source):
    """Plex and Emby: one BIF file. Frames are cut from it on first use."""

    def __init__(self, name, bif_url, folder, direct=None):
        self.name = name
        self.bif_url = bif_url
        self.folder = folder
        self.direct = direct        # ms -> URL of one frame, while the BIF is not there
        self.times = None
        self.offsets = None
        self.path = os.path.join(folder, 'index.bif')

    def prefetch(self, stop):
        if not os.path.isfile(self.path):
            started = time.monotonic()
            size = _download(self.bif_url, self.path, stop)
            if size is None:
                return
            _log('fetch %s in %.0f ms (%d KB)' % (self.name, (time.monotonic() - started) * 1000, size // 1024),
                 xbmc.LOGINFO)
        try:
            with open(self.path, 'rb') as handle:
                head = handle.read(64)
                count = struct.unpack('<I', head[12:16])[0] if len(head) >= 16 else 0
                head += handle.read((count + 1) * 8)
            self.times, self.offsets = parse_bif_index(head)
        except Exception as exc:
            _log('%s BIF unreadable: %s' % (self.name, exc), xbmc.LOGWARNING)
            _remove(self.path)

    def frame(self, ms):
        if self.times:
            index = frame_index(self.times, ms)
            target = os.path.join(self.folder, '%05d.jpg' % index)
            if not os.path.isfile(target) and not self._cut(index, target):
                return None, None
            return target, {P_MODE: 'frame', P_PATH: target}
        if self.direct:
            url = self.direct(ms - ms % URL_STEP_MS)
            return url, {P_MODE: 'frame', P_PATH: url}
        return None, None

    def _cut(self, index, target):
        try:
            start, end = self.offsets[index], self.offsets[index + 1]
            with open(self.path, 'rb') as handle:
                handle.seek(start)
                data = handle.read(end - start)
            tmp = target + '.part'
            with open(tmp, 'wb') as out:
                out.write(data)
            os.replace(tmp, target)
            return True
        except Exception as exc:
            _log('frame %d not cut: %s' % (index, exc), xbmc.LOGWARNING)
            return False


class TileSource(Source):
    """Jellyfin trickplay: sheets of TILE_GRID frames, cropped by the skin."""
    name = 'jellyfin'

    def __init__(self, sheet_url, folder, info):
        self.sheet_url = sheet_url      # sheet index -> URL
        self.folder = folder
        self.interval = max(1, int(info.get('Interval') or 10000))
        self.count = int(info.get('ThumbnailCount') or 0)
        self.per_sheet = TILE_GRID[0] * TILE_GRID[1]
        self.aspect = str(aspect_bucket(info.get('Width'), info.get('Height')))

    def prefetch(self, stop):
        started = time.monotonic()
        total = 0
        for sheet in range((self.count + self.per_sheet - 1) // self.per_sheet):
            path = self._sheet_path(sheet)
            if os.path.isfile(path):
                continue
            size = _download(self.sheet_url(sheet), path, stop)
            if size is None:
                return
            total += size
        if total:
            _log('fetch jellyfin in %.0f ms (%d KB)' % ((time.monotonic() - started) * 1000, total // 1024),
                 xbmc.LOGINFO)

    def _sheet_path(self, sheet):
        return os.path.join(self.folder, 'sheet%03d.jpg' % sheet)

    def frame(self, ms):
        if self.count <= 0:
            return None, None
        thumb = min(self.count - 1, max(0, ms) // self.interval)
        sheet, cell = divmod(thumb, self.per_sheet)
        row, col = divmod(cell, TILE_GRID[0])
        path = self._sheet_path(sheet)
        image = path if os.path.isfile(path) else self.sheet_url(sheet)
        return thumb, {P_MODE: 'tile', P_PATH: image, P_COL: str(col), P_ROW: str(row),
                       P_ASPECT: self.aspect}


class SidecarSource(Source):
    """The preview container on the user's Docker host (docker/seekthumbs)."""
    name = 'sidecar'

    def __init__(self, base, token, stream_url, headers, scan_every):
        self.base = base.rstrip('/')
        self.token = token
        self.stream_url = stream_url
        self.headers = headers
        self.scan_every = scan_every

    def _query(self, **extra):
        params = [('k', self.token), ('u', self.stream_url)]
        if self.headers:
            params.append(('h', '\r\n'.join('%s: %s' % kv for kv in sorted(self.headers.items()))))
        params.extend(sorted(extra.items()))
        return urlencode(params, quote_via=quote)

    def prefetch(self, stop):
        if self.scan_every <= 0:
            return
        url = '%s/scan?%s' % (self.base, self._query(every=self.scan_every, s=URL_STEP_MS // 1000))
        try:
            with urlopen(Request(url, headers={'Accept': 'application/json'}), timeout=TIMEOUT) as resp:
                _log('sidecar scan: %s' % resp.read(200).decode('utf-8', 'replace'))
        except Exception as exc:
            _log('sidecar scan not started: %s' % exc, xbmc.LOGWARNING)

    def frame(self, ms):
        secs = (ms - ms % URL_STEP_MS) // 1000
        near = max(URL_STEP_MS // 1000, self.scan_every // 2) if self.scan_every > 0 else 0
        url = '%s/thumb?%s' % (self.base, self._query(t=secs, s=URL_STEP_MS // 1000, n=near))
        return secs, {P_MODE: 'frame', P_PATH: url}


class _ChainSource(Source):
    """A server source that turns out empty hands over to the next one."""

    def __init__(self, sources):
        self.sources = [s for s in sources if s is not None]
        self.current = self.sources[0] if self.sources else Source()
        self.name = self.current.name

    def prefetch(self, stop):
        for source in self.sources:
            if stop.is_set():
                return
            self.current, self.name = source, source.name
            source.prefetch(stop)
            if _usable(source):
                return

    def frame(self, ms):
        return self.current.frame(ms)


def _usable(source):
    if isinstance(source, BifSource):
        return bool(source.times)
    if isinstance(source, TileSource):
        return source.count > 0
    return True


# ------------------------------------------------------------ transfers
def _download(url, path, stop):
    """Copy url to path at no more than RATE. Size in bytes, None if not."""
    tmp = path + '.part'
    started = time.monotonic()
    size = 0
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with urlopen(Request(url, headers={'Accept': '*/*', 'User-Agent': 'DexHub-Kodi'}), timeout=TIMEOUT) as resp, \
                open(tmp, 'wb') as out:
            while True:
                if stop.is_set():
                    raise InterruptedError('playback ended')
                chunk = resp.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('larger than %d MB' % (MAX_BYTES // (1024 * 1024)))
                out.write(chunk)
                ahead = size / float(RATE) - (time.monotonic() - started)
                if ahead > 0 and stop.wait(ahead):
                    raise InterruptedError('playback ended')
        os.replace(tmp, path)
        return size
    except InterruptedError:
        _remove(tmp)
        return None
    except Exception as exc:
        _remove(tmp)
        _log('%s not copied: %s' % (_redact(url), exc), xbmc.LOGINFO)
        return None


def _get_json(url):
    with urlopen(Request(url, headers={'Accept': 'application/json'}), timeout=TIMEOUT) as resp:
        return json.loads(resp.read(4 * 1024 * 1024).decode('utf-8'))


def _redact(url):
    return re.sub(r'(?i)(token|api_key|k)=[^&]+', r'\1=***', url or '')


def _remove(path):
    try:
        os.remove(path)
    except Exception:
        pass


# ------------------------------------------------------------- choosing
def _folder(key):
    return os.path.join(root_dir(), hashlib.sha1(key.encode('utf-8')).hexdigest()[:20])


def _server_source(ctx, playing=''):
    """The server's previews when the play context names a server item and
    the playing file is that item (a context left from an earlier play, or a
    remote or STRM source the server only points to, gets none)."""
    kind = str(ctx.get('server_type') or '').lower()
    base = str(ctx.get('server_url') or '').rstrip('/')
    token = str(ctx.get('token') or '')
    if not (kind and base and token):
        return None
    if kind == 'plex':
        match = re.search(r'/library/parts/(\d+)/', str(ctx.get('stream_url') or ''))
        if not match or '/library/parts/%s/' % match.group(1) not in (playing or ''):
            return None
        part = match.group(1)
        root = '%s/library/parts/%s/indexes/sd' % (base, part)
        tok = urlencode({'X-Plex-Token': token})
        return BifSource('plex', '%s?%s' % (root, tok), _folder('plex|%s|%s' % (base, part)),
                         direct=lambda ms: '%s/%d?%s' % (root, ms, tok))
    item = str(ctx.get('item_id') or '')
    if not item or ('/videos/%s/' % item.lower()) not in (playing or '').lower():
        return None
    source_id = str(ctx.get('media_source_id') or '')
    if kind == 'emby':
        params = {'width': 320, 'api_key': token}
        if source_id:
            params['MediaSourceId'] = source_id
        return BifSource('emby', '%s/emby/Videos/%s/index.bif?%s' % (base, quote(item, safe=''), urlencode(params)),
                         _folder('emby|%s|%s|%s' % (base, item, source_id)))
    if kind in ('jellyfin', 'silo'):
        return _trickplay_source(base, token, item, source_id)
    return None


def _trickplay_source(base, token, item, source_id):
    """Jellyfin 10.9+: the item's Trickplay field names the sheet sizes."""
    try:
        data = _get_json('%s/Items/%s?%s' % (base, quote(item, safe=''),
                                              urlencode({'Fields': 'Trickplay', 'api_key': token})))
    except Exception as exc:
        _log('trickplay info not read: %s' % exc, xbmc.LOGINFO)
        return None
    sizes = (data.get('Trickplay') or {})
    sizes = sizes.get(source_id) or (next(iter(sizes.values())) if sizes else {})
    if not sizes:
        return None
    # the width closest to 320 px: the frame the skin shows is 384 px wide
    width = min(sizes, key=lambda w: abs(int(w) - 320) if str(w).isdigit() else 1 << 30)
    info = sizes[width]
    if (int(info.get('TileWidth') or 0), int(info.get('TileHeight') or 0)) != TILE_GRID:
        _log('trickplay grid %sx%s not supported (the skin crops %dx%d sheets)' % (
            info.get('TileWidth'), info.get('TileHeight'), TILE_GRID[0], TILE_GRID[1]), xbmc.LOGINFO)
        return None
    params = {'api_key': token}
    if source_id:
        params['MediaSourceId'] = source_id
    query = urlencode(params)
    root = '%s/Videos/%s/Trickplay/%s' % (base, quote(item, safe=''), width)
    return TileSource(lambda sheet: '%s/%d.jpg?%s' % (root, sheet, query),
                      _folder('jf|%s|%s|%s|%s' % (base, item, source_id, width)), info)


def _sidecar_source(playing):
    base = (_setting('seekthumb_sidecar_url') or '').strip()
    token = (_setting('seekthumb_sidecar_token') or '').strip()
    url, headers = split_kodi_url(playing)
    if not (base and token and urlsplit(url).scheme in ('http', 'https')):
        return None
    try:
        every = int(float(_setting('seekthumb_sidecar_scan', '30')))
    except ValueError:
        every = 30
    return SidecarSource(base, token, url, headers, max(0, every))


def choose(ctx, playing):
    """The previews for this play, or None. No network unless Jellyfin's
    trickplay info has to be read (one small request)."""
    if (_setting('seekthumb_enabled', 'true') or 'true').lower() == 'false':
        return None
    server = None
    try:
        server = _server_source(ctx or {}, split_kodi_url(playing)[0])
    except Exception as exc:
        _log('server previews not chosen: %s' % exc, xbmc.LOGWARNING)
    sidecar = _sidecar_source(playing)
    if server is None and sidecar is None:
        return None
    return _ChainSource([server, sidecar])


# --------------------------------------------------------------- session
class _Session(object):
    def __init__(self, ctx, playing):
        self.ctx = ctx
        self.playing = playing
        self.source = None
        self.stop = threading.Event()
        self.seek_end = threading.Event()
        self.follower = None
        self.starts = 0         # seeks announced; the follower checks it as it ends

    def run(self):
        """Off the player's callback thread: Jellyfin's choice reads the
        server once, and nothing here may hold up Kodi's other callbacks."""
        started = time.monotonic()
        try:
            source = choose(self.ctx, self.playing)
        except Exception as exc:
            _log('no previews: %s' % exc, xbmc.LOGWARNING)
            return
        if source is None or self.stop.is_set():
            return
        self.source = source
        _home().setProperty(P_READY, 'true')
        _log('%s chosen in %.0f ms' % (source.name, (time.monotonic() - started) * 1000), xbmc.LOGINFO)
        if self.stop.wait(START_DELAY):
            return
        try:
            if xbmc.Player().getPlayingFile() != self.playing:
                return
        except Exception:
            return
        try:
            source.prefetch(self.stop)
        except Exception as exc:
            _log('prefetch failed: %s' % exc, xbmc.LOGWARNING)
        if not self.stop.is_set() and not _usable(getattr(source, 'current', source)):
            # the server has no previews for this item and no container is set
            _home().clearProperty(P_READY)
            _log('%s has no previews for this item' % source.name, xbmc.LOGINFO)

    def follow(self):
        """Follow Player.SeekTime until the seek is over (runs only then)."""
        win = _home()
        last = None
        started = time.monotonic()
        while True:
            with _LOCK:
                seen = self.starts
            not_seeking = None
            while not self.stop.is_set() and time.monotonic() - started < SEEK_LIMIT:
                t0 = time.monotonic()
                ms = parse_seek_time(xbmc.getInfoLabel('Player.SeekTime(hh:mm:ss)'))
                if ms is not None:
                    key, props = self.source.frame(ms)
                    if props and key != last:
                        for name, value in props.items():
                            win.setProperty(name, value)
                        last = key
                        _log('step in %.1f ms' % ((time.monotonic() - t0) * 1000))
                if xbmc.getCondVisibility('Player.Seeking'):
                    not_seeking = None
                elif not_seeking is None:
                    not_seeking = time.monotonic()
                elif self.seek_end.is_set() or time.monotonic() - not_seeking >= SEEK_GRACE:
                    break
                # paced by the playback-end event: seek_end, once set, would not wait
                self.stop.wait(POLL)
            with _LOCK:
                if self.starts == seen or self.stop.is_set():
                    self.follower = None
                    return
                # a new seek began while this one was ending


def start(ctx, playing):
    """CompanionPlayer.onAVStarted: previews for what is playing now."""
    with _LOCK:
        old = _STATE['session']
        if old is not None and old.playing == playing:
            return              # onAVChange: same item, a new audio track
        if old is not None:
            old.stop.set()
            old.seek_end.set()
        session = _Session(dict(ctx or {}), playing)
        _STATE['session'] = session
    _clear()
    threading.Thread(target=session.run, name='DexHubSeekThumbs', daemon=True).start()


def stop():
    """Playback stopped or ended. Touches no window property: Kodi may be
    switching display modes (companion.onPlayBackStopped); start() clears
    them for the next play."""
    with _LOCK:
        session, _STATE['session'] = _STATE['session'], None
    if session is not None:
        session.stop.set()
        session.seek_end.set()


def seek_started():
    with _LOCK:
        session = _STATE['session']
        if session is None or session.source is None:
            return
        session.starts += 1
        session.seek_end.clear()
        if session.follower is not None:
            return              # the running follower takes this seek too
        session.follower = threading.Thread(target=session.follow, name='DexHubSeekFollow', daemon=True)
        session.follower.start()


def seek_stopped():
    with _LOCK:
        session = _STATE['session']
    if session is not None:
        session.seek_end.set()


def on_notification(sender, method, data):
    """The service monitor's onNotification, for the skin timer's NotifyAll."""
    if sender != SENDER:
        return
    if method.endswith(SEEK_START):
        seek_started()
    elif method.endswith(SEEK_STOP):
        seek_stopped()


def _clear():
    win = _home()
    for name in (P_READY, P_PATH, P_MODE, P_COL, P_ROW, P_ASPECT):
        win.clearProperty(name)


def purge(now=None):
    """Folders unused for KEEP_SECONDS, then the oldest until under KEEP_BYTES."""
    root = root_dir()
    if not os.path.isdir(root):
        return 0
    now = now or time.time()
    keep = None
    with _LOCK:
        session = _STATE['session']
        if session is not None and session.source is not None:
            keep = getattr(getattr(session.source, 'current', None), 'folder', None)
    folders = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if not os.path.isdir(path) or path == keep:
            continue
        size, newest = 0, 0.0
        for dirpath, _dirs, files in os.walk(path):
            for fn in files:
                try:
                    st = os.stat(os.path.join(dirpath, fn))
                except OSError:
                    continue
                size += st.st_size
                newest = max(newest, st.st_mtime, st.st_atime)
        folders.append((newest, size, path))
    removed = 0
    total = sum(f[1] for f in folders)
    for newest, size, path in sorted(folders):
        if now - newest > KEEP_SECONDS or total > KEEP_BYTES:
            shutil.rmtree(path, ignore_errors=True)
            total -= size
            removed += 1
    return removed
