"""Seek previews: the Kodi side (plugin.video.dexhub resources/lib/seekthumbs.py)
and the preview container (docker/seekthumbs/server.py), outside Kodi.

    python3 -m unittest discover -s tests
"""
import importlib.util
import os
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix='seekthumbs-test-')


def _stub_kodi():
    """Just enough of Kodi's modules for seekthumbs to import and run."""
    props = {}
    settings = {}
    xbmc = types.ModuleType('xbmc')
    xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING = 0, 1, 2
    xbmc.log = lambda msg, level=0: None
    xbmc.labels = {}
    xbmc.conditions = {}
    xbmc.getInfoLabel = lambda name: xbmc.labels.get(name, '')
    xbmc.getCondVisibility = lambda name: bool(xbmc.conditions.get(name))

    class Player(object):
        def getPlayingFile(self):
            return xbmc.labels.get('playing', '')
    xbmc.Player = Player
    xbmcgui = types.ModuleType('xbmcgui')

    class Window(object):
        def __init__(self, wid):
            pass

        def setProperty(self, key, value):
            props[key] = value

        def getProperty(self, key):
            return props.get(key, '')

        def clearProperty(self, key):
            props.pop(key, None)
    xbmcgui.Window = Window
    xbmcaddon = types.ModuleType('xbmcaddon')

    class Addon(object):
        def __init__(self, addon_id=None):
            pass

        def getSetting(self, key):
            return settings.get(key, '')
    xbmcaddon.Addon = Addon
    xbmcvfs = types.ModuleType('xbmcvfs')
    xbmcvfs.translatePath = lambda path: os.path.join(TMP, path.replace('special://temp/', ''))
    for mod in (xbmc, xbmcgui, xbmcaddon, xbmcvfs):
        sys.modules[mod.__name__] = mod
    return props, settings, xbmc


PROPS, SETTINGS, XBMC = _stub_kodi()


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ST = _load('seekthumbs', 'plugin.video.dexhub/resources/lib/seekthumbs.py')
os.environ['DEXHUB_THUMBS_CACHE'] = os.path.join(TMP, 'sidecar')
SC = _load('seekthumbs_server', 'docker/seekthumbs/server.py')


def make_bif(frames, unit=1000):
    """A BIF file whose frame i shows at i * 10 units, frame bytes as given."""
    head = ST.BIF_MAGIC + struct.pack('<III', 0, len(frames), unit) + b'\0' * 44
    offset = 64 + (len(frames) + 1) * 8
    table = b''
    for i, data in enumerate(frames):
        table += struct.pack('<II', i * 10, offset)
        offset += len(data)
    table += struct.pack('<II', 0xffffffff, offset)
    return head + table + b''.join(frames)


class BifTest(unittest.TestCase):
    def test_index(self):
        times, offsets = ST.parse_bif_index(make_bif([b'a', b'bb', b'ccc']))
        self.assertEqual(times, [0, 10000, 20000])
        self.assertEqual(offsets[3] - offsets[0], 6)

    def test_unit_zero_means_seconds(self):
        times, _ = ST.parse_bif_index(make_bif([b'a', b'b'], unit=0))
        self.assertEqual(times, [0, 10000])

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            ST.parse_bif_index(b'\xff\xd8' + b'\0' * 100)
        with self.assertRaises(ValueError):
            ST.parse_bif_index(make_bif([b'a', b'b'])[:70])

    def test_frame_index(self):
        times = [0, 10000, 20000]
        self.assertEqual([ST.frame_index(times, ms) for ms in (-5, 0, 9999, 10000, 99999)], [0, 0, 0, 1, 2])
        self.assertEqual(ST.frame_index([], 5), -1)

    def test_source_cuts_frames(self):
        folder = os.path.join(TMP, 'bif')
        os.makedirs(folder, exist_ok=True)
        jpgs = [b'\xff\xd8one', b'\xff\xd8two', b'\xff\xd8three']
        with open(os.path.join(folder, 'index.bif'), 'wb') as handle:
            handle.write(make_bif(jpgs))
        source = ST.BifSource('plex', 'http://unused', folder, direct=lambda ms: 'http://srv/%d' % ms)
        source.prefetch(threading.Event())
        key, props = source.frame(15000)
        self.assertEqual(props[ST.P_MODE], 'frame')
        with open(props[ST.P_PATH], 'rb') as handle:
            self.assertEqual(handle.read(), b'\xff\xd8two')
        self.assertEqual(source.frame(15500)[0], key)

    def test_direct_url_before_copy(self):
        source = ST.BifSource('plex', 'http://unused', os.path.join(TMP, 'none'),
                              direct=lambda ms: 'http://srv/%d' % ms)
        key, props = source.frame(123456)
        self.assertEqual(props[ST.P_PATH], 'http://srv/120000')


class DownloadTest(unittest.TestCase):
    def test_copy_over_http_at_capped_speed(self):
        import functools
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        served = os.path.join(TMP, 'served')
        os.makedirs(served, exist_ok=True)
        frames = [b'\xff\xd8' + bytes([i]) * 20000 for i in range(5)]
        with open(os.path.join(served, 'sd.bif'), 'wb') as handle:
            handle.write(make_bif(frames))
        handler = functools.partial(SimpleHTTPRequestHandler, directory=served)
        handler.log_message = lambda *a: None
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        old_rate, ST.RATE = ST.RATE, 200 * 1024
        try:
            folder = os.path.join(TMP, 'dl')
            source = ST.BifSource('plex', 'http://127.0.0.1:%d/sd.bif' % server.server_port, folder)
            started = time.monotonic()
            source.prefetch(threading.Event())
            took = time.monotonic() - started
            # 100 KB at 200 KB/s: about half a second, never at once
            self.assertGreater(took, 0.35)
            _key, props = source.frame(41000)
            with open(props[ST.P_PATH], 'rb') as handle:
                self.assertEqual(handle.read(), frames[4])
            stop = threading.Event()
            stop.set()
            self.assertIsNone(ST._download(source.bif_url, os.path.join(folder, 'again.bif'), stop))
            self.assertFalse(os.path.exists(os.path.join(folder, 'again.bif.part')))
        finally:
            ST.RATE = old_rate
            server.shutdown()


class TimeTest(unittest.TestCase):
    def test_seek_time(self):
        self.assertEqual(ST.parse_seek_time('01:02:03'), 3723000)
        self.assertEqual(ST.parse_seek_time('02:03'), 123000)
        self.assertIsNone(ST.parse_seek_time(''))
        self.assertIsNone(ST.parse_seek_time('abc'))

    def test_aspect(self):
        self.assertEqual(ST.aspect_bucket(320, 180), 178)
        self.assertEqual(ST.aspect_bucket(320, 134), 239)
        self.assertEqual(ST.aspect_bucket(320, 240), 133)
        self.assertEqual(ST.aspect_bucket(0, 0), 178)

    def test_kodi_url(self):
        self.assertEqual(ST.split_kodi_url('http://a/b.mkv|User-Agent=x%20y&Referer=r'),
                         ('http://a/b.mkv', {'User-Agent': 'x y', 'Referer': 'r'}))
        self.assertEqual(ST.split_kodi_url('http://a/b.mkv'), ('http://a/b.mkv', {}))


class TileTest(unittest.TestCase):
    def test_cell(self):
        info = {'Interval': 10000, 'ThumbnailCount': 250, 'Width': 320, 'Height': 134,
                'TileWidth': 10, 'TileHeight': 10}
        source = ST.TileSource(lambda n: 'http://jf/%d.jpg' % n, os.path.join(TMP, 'jf'), info)
        _key, props = source.frame(1234 * 1000)       # frame 123: sheet 1, row 2, col 3
        self.assertEqual((props[ST.P_PATH], props[ST.P_ROW], props[ST.P_COL]), ('http://jf/1.jpg', '2', '3'))
        self.assertEqual(props[ST.P_ASPECT], '239')
        _key, props = source.frame(10 ** 9)           # past the end: the last frame
        self.assertEqual((props[ST.P_PATH], props[ST.P_ROW], props[ST.P_COL]), ('http://jf/2.jpg', '4', '9'))


class ChooseTest(unittest.TestCase):
    def setUp(self):
        SETTINGS.clear()

    def test_plex(self):
        ctx = {'server_type': 'plex', 'server_url': 'http://plex:32400', 'token': 'T',
               'stream_url': 'http://plex:32400/library/parts/555/1600000/file.mkv?X-Plex-Token=T'}
        source = ST._server_source(ctx, ctx['stream_url'])
        self.assertEqual(source.bif_url, 'http://plex:32400/library/parts/555/indexes/sd?X-Plex-Token=T')
        self.assertEqual(source.direct(20000), 'http://plex:32400/library/parts/555/indexes/sd/20000?X-Plex-Token=T')

    def test_emby(self):
        ctx = {'server_type': 'emby', 'server_url': 'http://emby:8096', 'token': 'T',
               'item_id': '42', 'media_source_id': 'ms1'}
        url = ST._server_source(ctx, 'http://emby:8096/emby/Videos/42/stream.mkv?static=true').bif_url
        self.assertTrue(url.startswith('http://emby:8096/emby/Videos/42/index.bif?'))
        self.assertEqual(parse_qs(urlsplit(url).query),
                         {'width': ['320'], 'api_key': ['T'], 'MediaSourceId': ['ms1']})

    def test_stale_context_is_not_used(self):
        plex = {'server_type': 'plex', 'server_url': 'http://plex:32400', 'token': 'T',
                'stream_url': 'http://plex:32400/library/parts/555/1/file.mkv'}
        self.assertIsNone(ST._server_source(plex, 'http://plex:32400/library/parts/999/1/other.mkv'))
        emby = {'server_type': 'emby', 'server_url': 'http://emby:8096', 'token': 'T', 'item_id': '42'}
        self.assertIsNone(ST._server_source(emby, '/storage/videos/film.mkv'))
        self.assertIsNone(ST._server_source(emby, 'http://emby:8096/emby/Videos/421/stream'))

    def test_stremio_needs_container(self):
        self.assertIsNone(ST.choose({}, 'https://debrid/x.mkv'))
        SETTINGS.update({'seekthumb_sidecar_url': 'http://nas:8765/', 'seekthumb_sidecar_token': 'K'})
        source = ST.choose({}, 'https://debrid/x.mkv|User-Agent=Kodi')
        self.assertEqual(source.name, 'sidecar')
        _key, props = source.frame(73500)
        url = urlsplit(props[ST.P_PATH])
        self.assertEqual(url.netloc + url.path, 'nas:8765/thumb')
        q = parse_qs(url.query)
        self.assertEqual((q['u'][0], q['h'][0], q['t'][0], q['k'][0], q['n'][0]),
                         ('https://debrid/x.mkv', 'User-Agent: Kodi', '70', 'K', '15'))

    def test_disabled(self):
        SETTINGS.update({'seekthumb_enabled': 'false', 'seekthumb_sidecar_url': 'http://nas:8765',
                         'seekthumb_sidecar_token': 'K'})
        self.assertIsNone(ST.choose({}, 'https://debrid/x.mkv'))

    def test_local_files_never_go_to_the_container(self):
        SETTINGS.update({'seekthumb_sidecar_url': 'http://nas:8765', 'seekthumb_sidecar_token': 'K'})
        self.assertIsNone(ST.choose({}, '/storage/videos/x.mkv'))


class FollowTest(unittest.TestCase):
    def test_follow_sets_properties_and_ends(self):
        SETTINGS.clear()
        SETTINGS.update({'seekthumb_sidecar_url': 'http://nas:8765', 'seekthumb_sidecar_token': 'K',
                         'seekthumb_sidecar_scan': '0'})
        ST.START_DELAY = 0
        XBMC.labels['playing'] = 'https://debrid/x.mkv'
        ST.start({}, 'https://debrid/x.mkv')
        deadline = time.time() + 2
        while PROPS.get(ST.P_READY) != 'true' and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(PROPS.get(ST.P_READY), 'true')
        XBMC.labels['Player.SeekTime(hh:mm:ss)'] = '00:01:05'
        XBMC.conditions['Player.Seeking'] = True
        ST.on_notification('plugin.video.dexhub', 'Other.dexhub_seek_start', '')
        deadline = time.time() + 2
        while 't=60' not in PROPS.get(ST.P_PATH, '') and time.time() < deadline:
            time.sleep(0.01)
        self.assertIn('t=60', PROPS.get(ST.P_PATH, ''))
        session = ST._STATE['session']
        XBMC.conditions['Player.Seeking'] = False
        ST.on_notification('plugin.video.dexhub', 'Other.dexhub_seek_stop', '')
        deadline = time.time() + 2
        while session.follower is not None and time.time() < deadline:
            time.sleep(0.01)
        self.assertIsNone(session.follower)
        ST.stop()
        self.assertIsNone(ST._STATE['session'])

    def test_other_senders_ignored(self):
        ST.on_notification('someone.else', 'Other.dexhub_seek_start', '')


class PurgeTest(unittest.TestCase):
    def test_old_folders_go(self):
        root = ST.root_dir()
        old = os.path.join(root, 'old')
        new = os.path.join(root, 'new')
        for path in (old, new):
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, 'a.jpg'), 'wb') as handle:
                handle.write(b'x')
        past = time.time() - ST.KEEP_SECONDS - 10
        os.utime(os.path.join(old, 'a.jpg'), (past, past))
        self.assertGreaterEqual(ST.purge(), 1)
        self.assertFalse(os.path.isdir(old))
        self.assertTrue(os.path.isdir(new))


class ContainerTest(unittest.TestCase):
    def test_rounding_and_near(self):
        self.assertEqual(SC.rounded(73.9, 10), 70)
        skey = SC.stream_key('http://x/y.mkv')
        path = SC.frame_path(skey, 60)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').close()
        self.assertEqual(SC.nearest_cached(skey, 70, 15, 10), path)
        self.assertIsNone(SC.nearest_cached(skey, 90, 15, 10))
        self.assertIsNone(SC.nearest_cached(skey, 70, 0, 10))

    def test_only_web_urls(self):
        self.assertTrue(SC.allowed_url('https://debrid/x.mkv'))
        for url in ('file:///etc/passwd', 'concat:a|b', '/etc/passwd', 'ftp://x/y'):
            self.assertFalse(SC.allowed_url(url))

    def test_headers(self):
        self.assertEqual(SC.header_arg('User-Agent: Kodi\r\nReferer: r\r\njunk'),
                         'User-Agent: Kodi\r\nReferer: r\r\n')


if __name__ == '__main__':
    unittest.main()
