#!/usr/bin/env python3
"""Dex Hub seek preview container: frames of a video stream on request.

Dex Hub on Kodi (plugin.video.dexhub, resources/lib/seekthumbs.py) shows the
picture at the time being sought. Plex, Emby and Jellyfin make those pictures
themselves; a Stremio or debrid stream has nobody to make them, so this
container grabs them with ffmpeg on the Docker host, away from the Kodi box.

  GET /thumb?k=KEY&u=URL&t=SECONDS[&s=STEP][&n=NEAR][&h=HEADERS]
      a JPEG of the frame at t (rounded down to STEP seconds, default 10).
      A frame already grabbed within NEAR seconds of t is answered at once;
      otherwise ffmpeg reads the nearest key frame (a few MB of the stream).
  GET /scan?k=KEY&u=URL&every=SECONDS[&s=STEP][&h=HEADERS]
      grabs a frame every SECONDS in the background, one stream at a time
      (a new scan replaces the old one), so later seeks are answered from disk.
  GET /health
      "ok" (no key needed), for Docker's health check.

The key (DEXHUB_THUMBS_KEY) is required: without it anyone on the network
could make the container fetch any URL. ffmpeg may only open http and https.
HDR (PQ or HLG) frames are tone-mapped so they do not look washed out.
"""
import hashlib
import hmac
import json
import logging
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

KEY = os.environ.get('DEXHUB_THUMBS_KEY', '')
CACHE = os.environ.get('DEXHUB_THUMBS_CACHE', '/cache')
PORT = int(os.environ.get('DEXHUB_THUMBS_PORT', '8765'))
WIDTH = int(os.environ.get('DEXHUB_THUMBS_WIDTH', '384'))
JOBS = int(os.environ.get('DEXHUB_THUMBS_JOBS', '2'))
GRAB_TIMEOUT = float(os.environ.get('DEXHUB_THUMBS_TIMEOUT', '20'))
KEEP_BYTES = int(float(os.environ.get('DEXHUB_THUMBS_CACHE_MB', '2048')) * 1024 * 1024)
KEEP_SECONDS = int(float(os.environ.get('DEXHUB_THUMBS_CACHE_HOURS', '48')) * 3600)
FFMPEG = os.environ.get('FFMPEG', 'ffmpeg')
FFPROBE = os.environ.get('FFPROBE', 'ffprobe')

PROTOCOLS = 'http,https,tcp,tls,crypto'
TONEMAP = ('zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,'
           'tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p')
HDR_TRANSFERS = ('smpte2084', 'arib-std-b67')

log = logging.getLogger('seekthumbs')

_slots = threading.BoundedSemaphore(max(1, JOBS))
_inflight = {}
_inflight_lock = threading.Lock()
_probes = {}
_probe_lock = threading.Lock()
_scan = {'key': None, 'stop': None}
_scan_lock = threading.Lock()
_writes = {'n': 0}


# ------------------------------------------------------------- helpers
def stream_key(url):
    return hashlib.sha1(url.encode('utf-8')).hexdigest()[:24]


def frame_path(skey, secs):
    return os.path.join(CACHE, skey, '%06d.jpg' % secs)


def rounded(t, step):
    step = max(1, step)
    return max(0, int(t) // step * step)


def nearest_cached(skey, secs, near, step):
    """The cached frame closest to secs within near seconds, or None."""
    if near <= 0:
        return None
    for delta in range(0, near + 1, max(1, step)):
        for cand in (secs - delta, secs + delta):
            if cand >= 0 and os.path.isfile(frame_path(skey, cand)):
                return frame_path(skey, cand)
    return None


def header_arg(headers):
    lines = [line.strip() for line in (headers or '').replace('\r', '').split('\n') if ':' in line]
    return ''.join(line + '\r\n' for line in lines)


def allowed_url(url):
    return urlsplit(url).scheme in ('http', 'https')


# -------------------------------------------------------------- ffmpeg
def probe(url, headers):
    """(duration seconds, hdr) of a stream, once per stream."""
    skey = stream_key(url)
    with _probe_lock:
        if skey in _probes:
            return _probes[skey]
    cmd = [FFPROBE, '-v', 'error', '-protocol_whitelist', PROTOCOLS, '-rw_timeout', '15000000']
    if headers:
        cmd += ['-headers', header_arg(headers)]
    cmd += ['-select_streams', 'v:0', '-show_entries', 'stream=color_transfer:format=duration',
            '-of', 'json', url]
    duration, hdr = 0.0, False
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=GRAB_TIMEOUT, check=False).stdout
        data = json.loads(out or b'{}')
        duration = float((data.get('format') or {}).get('duration') or 0)
        streams = data.get('streams') or [{}]
        hdr = (streams[0].get('color_transfer') or '') in HDR_TRANSFERS
    except Exception as exc:
        log.warning('probe failed: %s', exc)
    result = (duration, hdr)
    with _probe_lock:
        _probes[skey] = result
        if len(_probes) > 256:
            _probes.pop(next(iter(_probes)))
    return result


def grab(url, headers, secs, target):
    """Write the key frame at or before secs to target. True when written."""
    _duration, hdr = probe(url, headers)
    scale = 'scale=%d:-2' % WIDTH
    vf = '%s,%s' % (scale, TONEMAP) if hdr else scale
    cmd = [FFMPEG, '-nostdin', '-hide_banner', '-loglevel', 'error',
           '-protocol_whitelist', PROTOCOLS, '-rw_timeout', '15000000']
    if headers:
        cmd += ['-headers', header_arg(headers)]
    # input seek to the key frame before secs, decoding key frames only:
    # a preview does not need the exact frame, and this reads the least
    cmd += ['-skip_frame', 'nokey', '-noaccurate_seek', '-ss', str(secs), '-i', url,
            '-map', '0:v:0', '-frames:v', '1', '-an', '-sn', '-dn', '-vf', vf,
            '-q:v', '5', '-f', 'image2', '-c:v', 'mjpeg', 'pipe:1']
    started = time.monotonic()
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=GRAB_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        log.warning('grab %ss timed out', secs)
        return False
    if done.returncode != 0 or not done.stdout.startswith(b'\xff\xd8'):
        why = done.stderr.decode('utf-8', 'replace')[-300:].strip() or 'no frame (past the end of the stream?)'
        log.warning('grab %ss failed: %s', secs, why)
        return False
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + '.part'
    with open(tmp, 'wb') as handle:
        handle.write(done.stdout)
    os.replace(tmp, target)
    log.info('grab %ss in %.0f ms (%d KB)', secs, (time.monotonic() - started) * 1000, len(done.stdout) // 1024)
    _writes['n'] += 1
    if _writes['n'] % 200 == 0:
        threading.Thread(target=prune, daemon=True).start()
    return True


def frame(url, headers, secs):
    """The cached frame at secs, grabbed once even when asked twice at once."""
    target = frame_path(stream_key(url), secs)
    if os.path.isfile(target):
        return target
    with _inflight_lock:
        event = _inflight.get(target)
        owner = event is None
        if owner:
            event = _inflight[target] = threading.Event()
    if not owner:
        event.wait(GRAB_TIMEOUT + 5)
        return target if os.path.isfile(target) else None
    try:
        with _slots:
            if not os.path.isfile(target):
                grab(url, headers, secs, target)
    finally:
        with _inflight_lock:
            _inflight.pop(target, None)
        event.set()
    return target if os.path.isfile(target) else None


# ---------------------------------------------------------------- scan
def start_scan(url, headers, every, step):
    skey = stream_key(url)
    with _scan_lock:
        if _scan['key'] == skey:
            return 'running'
        if _scan['stop'] is not None:
            _scan['stop'].set()
        stop = threading.Event()
        _scan['key'], _scan['stop'] = skey, stop
    threading.Thread(target=_scan_run, args=(url, headers, every, step, stop, skey), daemon=True).start()
    return 'started'


def _scan_run(url, headers, every, step, stop, skey):
    try:
        duration, _hdr = probe(url, headers)
        if duration <= 0:
            log.warning('scan: no duration, not scanned')
            return
        every = max(step, rounded(every, step))
        started, count = time.monotonic(), 0
        for secs in range(0, int(duration), every):
            if stop.is_set():
                return
            if frame(url, headers, secs):
                count += 1
        log.info('scan of %d frames in %.0f s', count, time.monotonic() - started)
    finally:
        with _scan_lock:
            if _scan['key'] == skey:
                _scan['key'], _scan['stop'] = None, None


# --------------------------------------------------------------- cache
def prune(now=None):
    """Streams unused for KEEP_SECONDS, then the oldest until under KEEP_BYTES."""
    now = now or time.time()
    if not os.path.isdir(CACHE):
        return 0
    entries = []
    for name in os.listdir(CACHE):
        path = os.path.join(CACHE, name)
        if not os.path.isdir(path):
            continue
        size, newest = 0, 0.0
        for fn in os.listdir(path):
            try:
                st = os.stat(os.path.join(path, fn))
            except OSError:
                continue
            size += st.st_size
            newest = max(newest, st.st_mtime)
        entries.append((newest, size, path))
    total = sum(e[1] for e in entries)
    removed = 0
    for newest, size, path in sorted(entries):
        if now - newest > KEEP_SECONDS or total > KEEP_BYTES:
            for fn in os.listdir(path):
                try:
                    os.remove(os.path.join(path, fn))
                except OSError:
                    pass
            try:
                os.rmdir(path)
            except OSError:
                pass
            total -= size
            removed += 1
    return removed


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = 'dexhub-seekthumbs/1'

    def log_message(self, fmt, *args):
        log.debug('%s %s', self.address_string(), fmt % args)

    def _send(self, code, body=b'', ctype='text/plain; charset=utf-8', cache=False):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'max-age=86400' if cache else 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path == '/health':
            return self._send(200, b'ok')
        q = dict((k, v[0]) for k, v in parse_qs(parts.query, keep_blank_values=True).items())
        if not hmac.compare_digest(q.get('k', ''), KEY):
            return self._send(403, b'bad key')
        url = q.get('u', '')
        if not allowed_url(url):
            return self._send(400, b'u must be an http or https URL')
        headers = q.get('h', '')
        try:
            step = max(1, int(q.get('s') or 10))
        except ValueError:
            return self._send(400, b'bad s')
        if parts.path == '/thumb':
            try:
                secs = rounded(float(q.get('t') or 0), step)
                near = max(0, int(q.get('n') or 0))
            except ValueError:
                return self._send(400, b'bad t or n')
            path = (nearest_cached(stream_key(url), secs, near, step)
                    or frame(url, headers, secs))
            if not path:
                return self._send(404, b'no frame')
            with open(path, 'rb') as handle:
                return self._send(200, handle.read(), 'image/jpeg', cache=True)
        if parts.path == '/scan':
            try:
                every = max(step, int(q.get('every') or 30))
            except ValueError:
                return self._send(400, b'bad every')
            state = start_scan(url, headers, every, step)
            return self._send(200, json.dumps({'scan': state}).encode('utf-8'), 'application/json')
        return self._send(404, b'not found')


def main():
    logging.basicConfig(level=os.environ.get('LOG_LEVEL', 'INFO'),
                        format='%(asctime)s %(levelname)s %(message)s')
    if not KEY:
        raise SystemExit('DEXHUB_THUMBS_KEY is not set: refusing to start without a key')
    os.makedirs(CACHE, exist_ok=True)
    prune()
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.daemon_threads = True
    log.info('listening on %d, cache %s', PORT, CACHE)
    server.serve_forever()


if __name__ == '__main__':
    main()
