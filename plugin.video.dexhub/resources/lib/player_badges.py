# -*- coding: utf-8 -*-
"""Player badges (v5.10.141): the style the skin draws, Badger packs kept on
the device, and a folder of the user's own images.

Nothing is fetched when a video starts or the player's controls open. A
pack is read once, when it is chosen, together with its images, which are
kept in the add-on's folder (Kodi cannot draw SVG: an SVG badge is drawn as
its name, framed in the pack's colours). The skin draws a badge image at the
row's height with no plate behind it, in a box as wide as the image's shape
(badge_shapes: the Elite and Gold images are cut to their drawing), so a
pack looks the way it was drawn.

The folder style: the skin's own badges, any of them replaced by an image of
the same name in a folder (badges/ in the add-on's data folder, or one the
user picks). The badge is chosen the way the skin chooses its colourful
badges (detect()), from what Kodi reports of the stream.
"""
import hashlib
import json
import os
import re
import shutil
import time
from urllib.parse import unquote, urlsplit

import xbmc
import xbmcgui
import xbmcvfs

from . import badge_shapes

STYLES = ('colorful', 'minimal', 'compact', 'custom', 'folder', 'off')
PRESETS = (
    # key, Arabic, English, the pack's JSON
    ('elite', 'رسمية · Elite', 'Official · Elite',
     'https://raw.githubusercontent.com/leonevz/Elite-Badges/main/badges.json'),
    ('gold', 'ذهبية · Gold', 'Gold',
     'https://raw.githubusercontent.com/k45sle/NUVIO_BADGES/refs/heads/main/GOLD-NONAMES.json'),
    ('white', 'بيضاء بسيطة · Minimalist', 'Minimalist white',
     'https://raw.githubusercontent.com/sweatycab/nuvio-minimalist-badges/main/badges-white.json'),
)
SLOTS = 8
PER_GROUP = 2
DEFAULT_FOLDER = 'special://profile/addon_data/plugin.video.dexhub/badges/'
_IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
_MAX_JSON = 2 * 1024 * 1024
_MAX_IMAGE = 12 * 1024 * 1024
_MAX_TOTAL = 150 * 1024 * 1024
_LABELS = ('VideoPlayer.VideoCodec', 'VideoPlayer.AudioCodec', 'VideoPlayer.AudioChannels',
           'VideoPlayer.VideoResolution', 'VideoPlayer.HdrType', 'Player.FileNameAndPath',
           'Player.Process(amlogic.vs10.mode)', 'Player.Process(amlogic.eoft_gamut)')
_PROPS = ('image', 'w', 'x', 'text', 'look', 'fill', 'border', 'color')
_RULES = {}
_SIZES = {}
_LISTING = {}

# the skin's badges (media/dexhub/badges/<name>.png), in the order it shows
# them; a file of the same name in the folder replaces one
BUILTIN = (
    ('other_3d', 'ثلاثي الأبعاد 3D', '3D'),
    ('src_web_uhd', 'ويب 4K (WEB-DL و WEBRip)', 'WEB at 4K (WEB-DL, WEBRip)'),
    ('src_web', 'ويب (WEB-DL و WEBRip)', 'WEB (WEB-DL, WEBRip)'),
    ('src_bluray_uhd', 'بلوراي 4K', 'Ultra HD Blu-ray'),
    ('src_bluray', 'بلوراي و Remux', 'Blu-ray and Remux'),
    ('src_hdtv', 'HDTV', 'HDTV'),
    ('src_dvd', 'DVD', 'DVD'),
    ('res_8k', 'دقة 8K', '8K'),
    ('res_4k', 'دقة 4K', '4K'),
    ('res_1080', 'دقة 1080', '1080p'),
    ('res_720', 'دقة 720', '720p'),
    ('res_576', 'دقة 576', '576p'),
    ('res_540', 'دقة 540', '540p'),
    ('res_480', 'دقة 480', '480p'),
    ('hdr_dv', 'Dolby Vision', 'Dolby Vision'),
    ('hdr_10plus', 'HDR10+', 'HDR10+'),
    ('hdr_10', 'HDR10', 'HDR10'),
    ('hdr_hlg', 'HLG', 'HLG'),
    ('hdr_generic', 'HDR عام', 'HDR (other)'),
    ('codec_hevc', 'HEVC و H.265', 'HEVC, H.265'),
    ('codec_h264', 'H.264 و AVC', 'H.264, AVC'),
    ('codec_av1', 'AV1', 'AV1'),
    ('codec_vc1', 'VC 1', 'VC-1'),
    ('codec_mpeg2', 'MPEG 2', 'MPEG-2'),
    ('audio_atmos', 'Dolby Atmos', 'Dolby Atmos'),
    ('audio_dtsx_imax', 'DTS:X IMAX', 'DTS:X IMAX'),
    ('audio_dtsx', 'DTS:X', 'DTS:X'),
    ('audio_dtshd_ma', 'DTS HD Master Audio', 'DTS-HD Master Audio'),
    ('audio_dtshd_hra', 'DTS HD High Resolution', 'DTS-HD High Resolution'),
    ('audio_dts', 'DTS', 'DTS'),
    ('audio_truehd', 'Dolby TrueHD', 'Dolby TrueHD'),
    ('audio_ddplus', 'Dolby Digital Plus (DD+)', 'Dolby Digital Plus (DD+)'),
    ('audio_dd', 'Dolby Digital (DD)', 'Dolby Digital (DD)'),
    ('audio_aac', 'AAC', 'AAC'),
    ('audio_flac', 'FLAC', 'FLAC'),
    ('audio_opus', 'Opus', 'Opus'),
    ('audio_mp3', 'MP3', 'MP3'),
    ('audio_pcm', 'PCM', 'PCM'),
    ('audio_vorbis', 'Vorbis', 'Vorbis'),
    ('ch_714', 'قنوات 7.1.4', '7.1.4 channels'),
    ('ch_514', 'قنوات 5.1.4', '5.1.4 channels'),
    ('ch_10', 'قنوات 9.1', '9.1 channels'),
    ('ch_8', 'قنوات 7.1', '7.1 channels'),
    ('ch_7', 'قنوات 6.1', '6.1 channels'),
    ('ch_6', 'قنوات 5.1', '5.1 channels'),
    ('ch_5', 'قنوات 4.1', '4.1 channels'),
    ('ch_4', 'قنوات 4.0', '4.0 channels'),
    ('ch_3', 'قنوات 2.1', '2.1 channels'),
    ('ch_2', 'قنوات 2.0 ستيريو', '2.0 stereo'),
    ('ch_1', 'قناة 1.0', '1.0 mono'),
)
SAMPLE = ('src_bluray_uhd', 'res_4k', 'hdr_dv', 'codec_hevc', 'audio_atmos', 'ch_714')
SAMPLE_BLOB = 'REMUX 2160p 4K UHD Dolby Vision DV H.265 HEVC TrueHD Dolby TrueHD Atmos Dolby Atmos 7.1'


def _addon():
    from .settings_cache import cached_addon
    return cached_addon()


def _tr(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return en


def _profile():
    return xbmcvfs.translatePath(_addon().getAddonInfo('profile'))


def _set(key, value):
    """A setting written only when it changes: every write makes Kodi tell
    the service, which reads the whole file again."""
    addon = _addon()
    if (addon.getSetting(key) or '') != value:
        addon.setSetting(key, value)


def style():
    value = (_addon().getSetting('player_badge_style') or 'colorful').strip()
    return value if value in STYLES else 'colorful'


def preset_of(url):
    url = str(url or '').strip()
    return next((p for p in PRESETS if p[3] == url), None)


def pack_name(url=None):
    url = _addon().getSetting('player_badges_json_url') if url is None else url
    preset = preset_of(url)
    if preset:
        return _tr(preset[1], preset[2])
    return _tr('حزمة برابط', 'Pack from a link') if url else ''


def style_key(current=None):
    """The badge page's item for the style in use: a style, a preset's key,
    or 'url' (a pack from a link)."""
    current = current or style()
    if current != 'custom':
        return current
    url = _addon().getSetting('player_badges_json_url') or ''
    return (preset_of(url) or ('url',))[0]


def style_label(current=None):
    current = current or style()
    if current == 'custom':
        return pack_name() or _tr('حزمة برابط', 'Pack from a link')
    return {'colorful': _tr('مؤطرة', 'Framed'), 'minimal': _tr('بسيطة', 'Minimal'),
            'compact': _tr('مدمجة', 'Compact'), 'folder': _tr('من مجلدي', 'From my folder'),
            'off': _tr('بدون رموز', 'No badges')}.get(current, current)


def publish_style():
    win = xbmcgui.Window(10000)
    current = style()
    for prop, value in (('dhs.osd.badge.style', current), ('dhs.osd.badge.key', style_key(current)),
                        ('dhs.osd.badge.packname', pack_name() if current == 'custom' else '')):
        if win.getProperty(prop) != value:
            win.setProperty(prop, value)


# --------------------------------------------------------------- packs
def _path(url):
    key = hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]
    return os.path.join(_profile(), 'player_badges_%s.json' % key)


def _pack_dir(url):
    key = hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]
    return os.path.join(_profile(), 'badge_packs', key)


def _entries(data, group='other'):
    if isinstance(data, list):
        for value in data:
            yield from _entries(value, group)
    elif isinstance(data, dict):
        if any(data.get(k) for k in ('pattern', 'regex', 'match')):
            yield group, data
            return
        if isinstance(data.get('groups'), (dict, list)):
            groups = data['groups'].values() if isinstance(data['groups'], dict) else data['groups']
            for node in groups:
                if isinstance(node, dict):
                    yield from _entries(node, str(node.get('id') or node.get('name') or group))
        found = False
        for key in ('filters', 'badges', 'rules', 'items'):
            if key in data:
                found = True
                coll = data[key]
                yield from _entries(list(coll.values()) if isinstance(coll, dict) else coll, group)
        if not found:
            for key, value in data.items():
                if key != 'groups' and isinstance(value, (list, dict)):
                    yield from _entries(value, group)


def kodi_color(value, default):
    value = str(value or '').strip().lstrip('#')
    if not re.fullmatch(r'[0-9a-fA-F]{6}([0-9a-fA-F]{2})?', value):
        return default
    return ('ff' + value if len(value) == 6 else value[6:] + value[:6]).lower()


_NOT_DRAWN = re.compile(r'[\U00010000-\U0010ffff\ufe0f\u200d\u2060-\u2064]')


def _words(text):
    """A badge's name as Kodi's fonts draw it (flags and other emoji out)."""
    return re.sub(r'\s+', ' ', _NOT_DRAWN.sub('', str(text or ''))).strip()[:32]


def parse_pack(data):
    """[{'group', 'rx', 'image', 'name', 'text', 'look', 'fill', 'border', 'color', 'w'}]."""
    rules = []
    for inherited, row in _entries(data):
        if row.get('isEnabled') is False or row.get('enabled') is False:
            continue
        pattern = row.get('pattern') or row.get('regex') or row.get('match') or ''
        if len(str(pattern)) > 1024:
            continue
        try:
            rx = re.compile(str(pattern).replace('(?i)', ''), re.I)
        except re.error:
            continue
        image = str(row.get('imageURL') or row.get('imageUrl') or row.get('image_url') or row.get('image') or row.get('icon') or row.get('url') or '').strip()
        if image and urlsplit(image).scheme.lower() not in ('http', 'https'):
            image = ''
        ext = os.path.splitext(urlsplit(image).path)[1].lower() if image else ''
        if ext and ext not in _IMAGE_EXT:
            image = ''          # SVG and the like: Kodi cannot draw them, the name instead
        name = _words(row.get('name') or row.get('label') or '')
        if not image and not name:
            continue
        group = str(row.get('groupId') or row.get('group') or row.get('category') or inherited)
        tag = str(row.get('tagStyle') or '').lower()
        fill = kodi_color(row.get('tagColor'), '00000000')
        border = kodi_color(row.get('borderColor'), '00000000')
        color = kodi_color(row.get('textColor'), 'ffffffff')
        if color.startswith('00'):
            color = 'ffffffff'          # a word must be readable
        look = 'fill' if ('fill' in tag and not fill.startswith('00')) else 'outline'
        if look == 'outline' and border.startswith('00'):
            border = color
        rules.append({'group': group, 'rx': rx, 'image': image, 'name': name,
                      'text': '' if image else name, 'look': look,
                      'fill': fill, 'border': border, 'color': color, 'w': '', 'pad': (0, 0)})
        if len(rules) == 512:
            break
    return rules


def _read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _cached_rules(url):
    """The saved pack's rules, its images on the device where they were kept."""
    if not url:
        return []
    path = _path(url)
    manifest_path = os.path.join(_pack_dir(url), 'manifest.json')
    try:
        stamp = (os.stat(path).st_mtime_ns,
                 os.stat(manifest_path).st_mtime_ns if os.path.isfile(manifest_path) else 0)
    except OSError:
        return []
    cached = _RULES.get(url)
    if cached and cached[0] == stamp:
        return cached[1]
    data = _read_json(path)
    if data is None:
        return []
    rules = parse_pack(data)
    manifest = _read_json(manifest_path) if stamp[1] else None
    kept = (manifest or {}).get('images') or {}
    folder = os.path.dirname(manifest_path)
    for rule in rules:
        if not rule['image']:
            continue
        local = kept.get(rule['image'])
        if local:
            size, trim = (local.get('w'), local.get('h')), local.get('trim')
            rule['w'] = badge_shapes.width_class(size, trim)
            rule['pad'] = badge_shapes.paddings(rule['w'], size, trim)
            rule['image'] = os.path.join(folder, local['file'])
        elif manifest is not None:
            rule['image'], rule['text'] = '', rule['name']      # not on the device: its name
        else:
            rule['w'] = badge_shapes.FALLBACK       # saved before 5.10.141: from the web, as then
    _RULES[url] = (stamp, rules)
    return rules


def _fetch(url, limit, timeout=12):
    from urllib.request import Request, urlopen
    if urlsplit(url).scheme.lower() not in ('https', 'http'):
        raise ValueError('not a web address')
    with urlopen(Request(url, headers={'User-Agent': 'DexHub Kodi'}), timeout=timeout) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('too large')
    return raw


def _sniff(raw):
    if raw[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if raw[:6] in (b'GIF87a', b'GIF89a'):
        return '.gif'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return '.webp'
    if raw[:2] == b'\xff\xd8':
        return '.jpg'
    return ''


def load_pack(url, progress=None):
    """Explicit choice only: the pack's JSON and its images onto the device.
    The previous pack stays when this one cannot be read."""
    data = json.loads(_fetch(url, _MAX_JSON).decode('utf-8-sig'))
    rules = parse_pack(data)
    if not rules:
        raise ValueError('No enabled compatible badges in JSON')
    images = []
    for rule in rules:
        if rule['image'] and rule['image'] not in images:
            images.append(rule['image'])
    folder = _pack_dir(url)
    tmp = folder + '.new'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    kept, total = {}, 0
    for index, image in enumerate(images):
        if progress is not None:
            if progress.iscanceled():
                shutil.rmtree(tmp, ignore_errors=True)
                raise ValueError('cancelled')
            progress.update(int(100 * index / max(1, len(images))),
                            _tr('تحميل صور الرموز %d من %d', 'Badge images %d of %d') % (index + 1, len(images)))
        try:
            raw = _fetch(image, _MAX_IMAGE)
        except Exception as exc:
            xbmc.log('[DexHub] badge image unavailable (%s): %s' % (os.path.basename(urlsplit(image).path), exc), xbmc.LOGINFO)
            continue
        ext = _sniff(raw)
        if not ext or total + len(raw) > _MAX_TOTAL:
            continue
        name = '%03d%s' % (index, ext)
        with open(os.path.join(tmp, name), 'wb') as handle:
            handle.write(raw)
        total += len(raw)
        size = badge_shapes.image_size(os.path.join(tmp, name)) or (0, 0)
        entry = {'file': name, 'w': size[0], 'h': size[1]}
        trim = badge_shapes.trim_of(os.path.basename(unquote(urlsplit(image).path)), size)
        if trim:
            entry['trim'] = list(trim)
        kept[image] = entry
    with open(os.path.join(tmp, 'manifest.json'), 'w', encoding='utf-8') as handle:
        json.dump({'url': url, 'images': kept}, handle)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(os.path.dirname(folder), exist_ok=True)
    os.replace(tmp, folder)
    path = _path(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(path + '.tmp', path)
    _set('player_badges_json_url', url)
    _RULES.pop(url, None)
    return len(rules), len(kept), len(images)


def pack_ready(url):
    return bool(url) and os.path.exists(_path(url)) and os.path.isfile(os.path.join(_pack_dir(url), 'manifest.json'))


def stream_blob(labels):
    """Real stream details take priority; do not match tokens or query strings."""
    video = labels.get('VideoPlayer.VideoCodec', '')
    audio = labels.get('VideoPlayer.AudioCodec', '')
    hdr = labels.get('VideoPlayer.HdrType', '')
    channels = labels.get('VideoPlayer.AudioChannels', '')
    channels = {'2': '2.0', '6': '5.1', '7': '6.1', '8': '7.1'}.get(channels, channels)
    resolution = labels.get('VideoPlayer.VideoResolution', '')
    aliases = {'h264': 'H.264 AVC', 'avc': 'H.264 AVC', 'avc1': 'H.264 AVC', 'h265': 'H.265 HEVC',
               'hevc': 'H.265 HEVC', 'eac3': 'EAC3 DD+ DDP Dolby Digital Plus',
               'ac3': 'AC3 DD Dolby Digital', 'eac3_ddp_atmos': 'EAC3 DD+ DDP Dolby Digital Plus Atmos Dolby Atmos',
               'truehd_atmos': 'TrueHD Dolby TrueHD Atmos Dolby Atmos', 'truehd': 'TrueHD Dolby TrueHD',
               'dtshd_ma_x': 'DTS:X DTS-X', 'dtshd_ma_x_imax': 'DTS:X DTS-X IMAX',
               'dtshd_ma': 'DTS-HD MA DTS-HD Master Audio', 'dtshd_hra': 'DTS-HD HRA DTS-HD',
               'dca': 'DTS', 'dts': 'DTS', 'aac': 'AAC', 'flac': 'FLAC', 'opus': 'Opus',
               'dolbyvision': 'Dolby Vision DV', 'hdr10plus': 'HDR10+ HDR10Plus', 'hdr10': 'HDR10',
               'hlg': 'HLG'}
    filename = unquote(urlsplit(labels.get('Player.FileNameAndPath', '')).path).rsplit('/', 1)[-1]
    if not hdr:
        # some outputs report no HDR type: the release name's own
        for pattern, name in ((r'(?:^|[ ._-])(?:dv|dovi|dolby[ ._-]?vision)(?:[ ._-]|$)', 'dolbyvision'),
                              (r'hdr10(?:\+|plus|p(?![a-z]))', 'hdr10plus'), (r'hdr10', 'hdr10'),
                              (r'(?:^|[ ._-])hdr(?:[ ._-]|$)', 'hdr')):
            if re.search(pattern, filename, re.I):
                hdr = name
                break
    bits = [aliases.get(video.lower(), video), aliases.get(audio.lower(), audio),
            aliases.get(hdr.lower(), hdr.upper()) or ('SDR' if video else ''), channels,
            ('%sp' % resolution if resolution.isdigit() else resolution)]
    if resolution in ('2160', '4K'):
        bits.append('2160p 4K UHD')
    # the actual HDR label supersedes release-name hints; only known release
    # sources come from the name, never digits from a channel, URL or title
    for pattern, name in ((r'web[ ._-]?dl', 'WEB-DL'), (r'web[ ._-]?rip', 'WEBRip'),
                          (r'blu[ ._-]?ray|bdrip|brrip', 'BluRay'), (r'\bremux\b', 'REMUX'),
                          (r'\bimax\b', 'IMAX'), (r'\bhdtv\b', 'HDTV')):
        if re.search(pattern, filename, re.I):
            bits.append(name)
    return ' '.join(str(b) for b in bits if b)


def matched_badges(rules, blob, limit=SLOTS):
    """The pack's badges for this stream: in the pack's order, at most two a
    group (DV before HDR10, Atmos with TrueHD), one image or word once."""
    out, seen, counts = [], set(), {}
    for rule in rules:
        key = (rule['image'], rule['text'])
        if counts.get(rule['group'], 0) >= PER_GROUP or key in seen:
            continue
        if rule['rx'].search(blob):
            out.append(rule)
            counts[rule['group']] = counts.get(rule['group'], 0) + 1
            seen.add(key)
            if len(out) >= limit:
                break
    return out


# --------------------------------------------------------------- folder
def folder_path():
    path = (_addon().getSetting('player_badges_folder') or '').strip() or DEFAULT_FOLDER
    return path if path.endswith(('/', '\\')) else path + '/'


def folder_shown(path=None):
    """The folder as the user finds it (its real path on this device)."""
    return xbmcvfs.translatePath(path or folder_path())


def _skin_badges():
    """skin.dexhub's own badge images on the device ('' without the skin)."""
    try:
        import xbmcaddon
        root = xbmcaddon.Addon('skin.dexhub').getAddonInfo('path')
    except Exception:
        return ''
    path = os.path.join(xbmcvfs.translatePath(root), 'media', 'dexhub', 'badges')
    return path if os.path.isdir(path) else ''


def readme():
    lines = [_tr('رموز المشغّل من مجلدك', 'Player badges from your folder'), '',
             _tr('ضع صورة بنفس الاسم لأي رمز تبغى تبدله، وباقي الرموز تبقى من السكين.',
                 'Put an image with the same name for any badge you want to replace; the rest stay the skin\'s.'),
             _tr('يقبل PNG و JPG و WebP و GIF. الأفضل PNG شفاف مقصوص على الشعار بدون فراغ حوله.',
                 'PNG, JPG, WebP and GIF. Best: a transparent PNG cut to the logo, with no empty margin.'),
             _tr('يرسم الرمز بارتفاع واحد وعرضه حسب شكل صورتك (حتى ستة أضعاف ونص ارتفاعه).',
                 'Drawn at one height, as wide as your image\'s shape (up to six and a half times its height).'),
             _tr('بعد التبديل افتح أزرار المشغّل من جديد أو شغّل مقطع جديد.',
                 'After a change, open the player controls again or start another video.'), '']
    for name, ar, en in BUILTIN:
        lines.append('%-18s %s' % (name + '.png', _tr(ar, en)))
    return '\n'.join(lines) + '\n'


def _listing(folder, fresh=False):
    """{badge name: file name} of the folder's images (kept 20 seconds)."""
    hit = _LISTING.get(folder)
    if hit and not fresh and time.time() - hit[0] < 20:
        return hit[1]
    try:
        files = xbmcvfs.listdir(folder)[1] if xbmcvfs.exists(folder) else []
    except Exception:
        files = []
    mine = {}
    for name in files:
        stem, ext = os.path.splitext(name)
        if ext.lower() in _IMAGE_EXT:
            mine.setdefault(stem.lower(), name)
    _LISTING[folder] = (time.time(), mine)
    return mine


def prepare_folder(path=None, refill=False):
    """The folder ready: created, the skin's badges copied in when it holds
    none (or on request), and a list of the names. Its path."""
    folder = path or folder_path()
    if not folder.endswith(('/', '\\')):
        folder += '/'
    if not xbmcvfs.exists(folder) and not xbmcvfs.mkdirs(folder):
        raise OSError('cannot create %s' % folder)
    have = _listing(folder, fresh=True)
    source = _skin_badges()
    if source and (refill or not have):
        for name, _ar, _en in BUILTIN:
            src = os.path.join(source, name + '.png')
            if os.path.isfile(src) and (refill or name not in have):
                xbmcvfs.copy(src, folder + name + '.png')
    handle = xbmcvfs.File(folder + 'README.txt', 'w')
    try:
        handle.write(bytearray(readme().encode('utf-8')))
    finally:
        handle.close()
    _LISTING.pop(folder, None)
    _SIZES.clear()
    return folder


def detect(labels, stereo=False):
    """The skin's colourful badges for this stream (Includes_DexPlayer.xml's
    DhB_* conditions): the first that fits in each group, in its order."""
    def lower(key):
        return str(labels.get(key) or '').lower()
    res = str(labels.get('VideoPlayer.VideoResolution') or '')
    hdr, vc, ac = lower('VideoPlayer.HdrType'), lower('VideoPlayer.VideoCodec'), lower('VideoPlayer.AudioCodec')
    try:
        ch = int(labels.get('VideoPlayer.AudioChannels') or 0)
    except ValueError:
        ch = 0
    path = unquote(lower('Player.FileNameAndPath'))
    vs10, eotf = lower('Player.Process(amlogic.vs10.mode)'), lower('Player.Process(amlogic.eoft_gamut)')

    def has(*words):
        return any(w in path for w in words)
    keys = ['other_3d'] if stereo else []
    web = has('web-dl', 'webdl', 'web.dl', 'web_dl', 'webrip', 'web-rip', 'web.rip')
    uhd = has('2160p', 'uhd', 'ultrahd', 'ultra.hd', 'ultra-hd') or res in ('4K', '2160')
    bluray = has('blu-ray', 'bluray', 'blu.ray', 'bdrip', 'brrip', 'bdremux', 'remux', 'bd25', 'bd50', 'bdmv')
    if web:
        keys.append('src_web_uhd' if uhd else 'src_web')
    elif bluray:
        keys.append('src_bluray_uhd' if uhd else 'src_bluray')
    elif has('hdtv', 'pdtv'):
        keys.append('src_hdtv')
    elif has('dvdrip', '.dvd.', 'dvd5', 'dvd9', 'dvd-r'):
        keys.append('src_dvd')
    resolution = {'8K': 'res_8k', '4320': 'res_8k', '4K': 'res_4k', '2160': 'res_4k', '1080': 'res_1080',
                  '720': 'res_720', '576': 'res_576', '540': 'res_540', '480': 'res_480'}.get(res)
    if resolution:
        keys.append(resolution)
    if hdr == 'dolbyvision' or 'dolby vision' in vs10 or has('.dv.', '.dovi.', 'dolby.vision', 'dolbyvision', '-dv-', '_dv_'):
        keys.append('hdr_dv')
    elif hdr == 'hdr10plus' or has('hdr10plus', 'hdr10p', '.hdrplus.'):
        keys.append('hdr_10plus')
    elif hdr == 'hdr10' or 'hdr10' in vs10:
        keys.append('hdr_10')
    elif hdr == 'hlg' or 'hlg' in eotf:
        keys.append('hdr_hlg')
    elif hdr or has('.hdr.', '-hdr-', '.hdr10.'):
        keys.append('hdr_generic')
    codec = next((key for key, names in (
        ('codec_hevc', ('hevc', 'h265', 'hev1', 'hvc1')), ('codec_h264', ('h264', 'avc1', 'avc')),
        ('codec_av1', ('av1',)), ('codec_vc1', ('vc1', 'wvc1')), ('codec_mpeg2', ('mpeg2video', 'mpeg2'))) if vc in names), '')
    if codec:
        keys.append(codec)
    audio = next((key for key, names in (
        ('audio_atmos', ('eac3_ddp_atmos', 'truehd_atmos')), ('audio_dtsx_imax', ('dtshd_ma_x_imax',)),
        ('audio_dtsx', ('dtshd_ma_x', 'dts_x')), ('audio_dtshd_ma', ('dtshd_ma',)), ('audio_dtshd_hra', ('dtshd_hra',)),
        ('audio_dts', ('dca', 'dts')), ('audio_truehd', ('truehd', 'mlp')), ('audio_ddplus', ('eac3',)),
        ('audio_dd', ('ac3',)), ('audio_flac', ('flac',)), ('audio_opus', ('opus',)),
        ('audio_mp3', ('mp3', 'mp3float')), ('audio_vorbis', ('vorbis',))) if ac in names), '')
    if not audio and (ac.startswith('aac') or ac.startswith('he_aac')):
        audio = 'audio_aac'
    if not audio and ac.startswith('pcm'):
        audio = 'audio_pcm'
    if audio:
        keys.append(audio)
    if ch == 8 and ac in ('truehd_atmos', 'dtshd_ma_x', 'dtshd_ma_x_imax'):
        keys.append('ch_714')
    elif ch == 6 and ac == 'eac3_ddp_atmos':
        keys.append('ch_514')
    elif ch in (1, 2, 3, 4, 5, 6, 7, 8, 10):
        keys.append('ch_%d' % ch)
    return keys


def _stamp(path, local):
    try:
        if local:
            info = os.stat(path)
            return '%d:%d' % (info.st_mtime_ns, info.st_size)
        info = xbmcvfs.Stat(path)
        return '%d:%d' % (info.st_mtime(), info.st_size())
    except Exception:
        return ''


def _image_class(path, local, stamp):
    """The skin's width class of an image file and the empty width beside
    it in that box (its size read once)."""
    hit = _SIZES.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    size = None
    if local:
        size = badge_shapes.image_size(path)
    else:
        handle = xbmcvfs.File(path)
        try:
            size = badge_shapes.image_size_of(bytes(handle.readBytes(65536)))
        finally:
            handle.close()
    shape = badge_shapes.width_class(size) if size else badge_shapes.FALLBACK
    found = (shape, badge_shapes.paddings(shape, size) if size else (0, 0))
    _SIZES[path] = (stamp, found)
    return found


def _forget_textures(paths):
    """Kodi keeps its own copy of each image it has drawn and shows that
    copy once more after the file changed: the copies of replaced badges
    are dropped, so the new image shows the next time. Kodi lists them by
    their wrapped address (image://special%3a%2f%2f...res_4k.png/)."""
    for path in paths:
        name = path.replace('\\', '/').rsplit('/', 1)[-1]
        query = {'jsonrpc': '2.0', 'id': 1, 'method': 'Textures.GetTextures',
                 'params': {'properties': ['url'],
                            'filter': {'field': 'url', 'operator': 'contains', 'value': name}}}
        try:
            found = json.loads(xbmc.executeJSONRPC(json.dumps(query))).get('result', {}).get('textures') or []
        except Exception:
            found = []
        for texture in found:
            url = unquote(str(texture.get('url') or '')).replace('\\', '/')
            if path in url or url.rstrip('/').endswith('/' + name):
                xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Textures.RemoveTexture',
                                                'params': {'textureid': texture.get('textureid')}}))


def folder_badges(keys):
    """The images for these badges: the folder's where it has one, else the skin's."""
    folder = folder_path()
    mine = _listing(folder)
    skin = _skin_badges()
    local = bool(folder.startswith('special://') or folder.startswith('/') or re.match(r'^[A-Za-z]:[\\/]', folder))
    seen_path = os.path.join(_profile(), 'badge_folder_seen.json')
    before = (_read_json(seen_path) or {}) if mine else {}
    seen = dict(before)
    changed, out = [], []
    for key in keys[:SLOTS]:
        if key in mine:
            path = folder + mine[key]
            real = xbmcvfs.translatePath(path) if local else path
            stamp = _stamp(real, local)
            if seen.get(path) and seen.get(path) != stamp:
                changed.append(path)
            seen[path] = stamp
            shape, pad = _image_class(real, local, stamp)
        elif skin:
            path = 'dexhub/badges/%s.png' % key
            real = os.path.join(skin, key + '.png')
            shape, pad = _image_class(real, True, _stamp(real, True))
        else:
            continue
        out.append({'image': path, 'w': shape, 'pad': pad, 'text': ''})
    if seen != before:
        try:
            with open(seen_path, 'w', encoding='utf-8') as handle:
                json.dump(seen, handle)
        except OSError:
            pass
    if changed:
        _forget_textures(changed)
    return out


# --------------------------------------------------------------- publish
def _write(win, prefix, badges):
    """The badges' properties: per slot its image and width class (or its
    word and colours) and 'x', the empty width between it and the next
    drawing, which the skin takes back from the gap; 'lead' the first's."""
    pads = [badge.get('pad') or (0, 0) for badge in badges]
    lead = badge_shapes.gap_class(pads[0][0]) if badges else ''
    if win.getProperty(prefix + '.lead') != lead:
        win.setProperty(prefix + '.lead', lead)
    for index in range(1, SLOTS + 1):
        badge = badges[index - 1] if index <= len(badges) else None
        values = {}
        if badge:
            if badge.get('image'):
                # 'fill' and 'border' fully transparent: skin.dexhub 3.18 and
                # older draw a plate under every image from them, and an empty
                # colour is white there (Kodi: no tint), the grey box behind it
                values = {'image': badge['image'], 'w': badge.get('w') or badge_shapes.FALLBACK,
                          'fill': '00ffffff', 'border': '00ffffff'}
            elif badge.get('text'):
                values = {'text': badge['text'], 'look': badge.get('look') or 'outline',
                          'fill': badge.get('fill') or '00000000', 'border': badge.get('border') or 'ffffffff',
                          'color': badge.get('color') or 'ffffffff'}
            if values and index < len(badges):
                values['x'] = badge_shapes.gap_class(pads[index - 1][1] + pads[index][0])
        for kind in _PROPS:
            prop = '%s.%d.%s' % (prefix, index, kind)
            value = values.get(kind, '')
            if win.getProperty(prop) != value:
                if value:
                    win.setProperty(prop, value)
                else:
                    win.clearProperty(prop)
    win.setProperty('%s.count' % prefix, str(len(badges)) if badges else '')


def current_badges():
    """The badges of what plays now, for the custom and folder styles."""
    current = style()
    if current not in ('custom', 'folder') or not xbmc.Player().isPlayingVideo():
        return []
    labels = {key: xbmc.getInfoLabel(key) or '' for key in _LABELS}
    if current == 'custom':
        return matched_badges(_cached_rules(_addon().getSetting('player_badges_json_url') or ''),
                              stream_blob(labels))
    return folder_badges(detect(labels, bool(xbmc.getCondVisibility('VideoPlayer.IsStereoscopic'))))


def publish():
    """Read labels/cache once per stream start or change, never poll/fetch."""
    win = xbmcgui.Window(10000)
    publish_style()
    _write(win, 'dhs.osd.badge', current_badges())


def preview():
    """Samples for the badge page: each kept preset's, the folder's and a
    pack from a link. The actual OSD badges are not touched."""
    win = xbmcgui.Window(10000)
    for key, _ar, _en, url in PRESETS:
        ready = pack_ready(url)
        _write(win, 'dhs.preview.%s' % key, matched_badges(_cached_rules(url), SAMPLE_BLOB) if ready else [])
        win.setProperty('dhs.preview.%s.ready' % key, 'true' if ready else '')
    _write(win, 'dhs.preview.folder', folder_badges(list(SAMPLE)))
    win.setProperty('dhs.preview.folderpath', folder_shown())
    url = _addon().getSetting('player_badges_json_url') or ''
    other = url if url and not preset_of(url) else ''
    _write(win, 'dhs.preview.url', matched_badges(_cached_rules(other), SAMPLE_BLOB) if other else [])
    publish_style()
    from . import ui_preferences
    ui_preferences.publish()
    return True


def skin_outdated():
    """skin.dexhub's version when it is older than 3.19 (it draws a pack's
    badges on plates and has no folder style), else ''."""
    if xbmc.getSkinDir() != 'skin.dexhub':
        return ''
    try:
        import xbmcaddon
        version = xbmcaddon.Addon('skin.dexhub').getAddonInfo('version') or ''
    except Exception:
        return ''
    parts = tuple(int(p) for p in re.findall(r'\d+', version)[:3])
    return version if parts and parts < (3, 19, 0) else ''


def warn_old_skin(always=False):
    """Once at start (and on the badge page): a pack or the folder chosen
    with a skin.dexhub older than 3.19, which Kodi kept when the 3.19 zip was
    installed before the add-on it asked for."""
    old = skin_outdated()
    if not old or (not always and style() not in ('custom', 'folder')):
        return False
    xbmc.log('[DexHub] skin.dexhub %s is older than 3.19: player badges stay on plates until it is installed' % old,
             xbmc.LOGWARNING)
    xbmcgui.Dialog().notification('Dex Hub', _tr('ثبّت سكين Dex Hub 3.19 (المثبت %s) عشان تظهر الرموز بدون خلفية',
                                                 'Install skin Dex Hub 3.19 (installed: %s) for badges without plates') % old,
                                  time=9000, sound=False)
    return True


# --------------------------------------------------------------- choices
def _ensure_extra_osd():
    if xbmc.getSkinDir() == 'skin.dexhub':
        xbmc.executebuiltin('Skin.SetBool(ExtraOSD)')     # skins before 3.18 still ask for it


def _chosen(label):
    xbmcgui.Dialog().notification('Dex Hub', _tr('رموز المشغّل: %s', 'Player badges: %s') % label,
                                  time=2000, sound=False)


def select_style(selected, quiet=False):
    if selected not in STYLES:
        return False
    if selected == 'folder':
        try:
            prepare_folder()
        except Exception as exc:
            xbmc.log('[DexHub] badge folder unavailable: %s' % exc, xbmc.LOGWARNING)
            xbmcgui.Dialog().notification('Dex Hub', _tr('تعذر تجهيز مجلد الرموز', 'Could not prepare the badge folder'),
                                          time=3000, sound=False)
            return False
    _set('player_badge_style', selected)
    if selected != 'off':
        _ensure_extra_osd()
    publish()
    if quiet or warn_old_skin():
        return True
    if selected == 'folder':
        _folder_notice()
    else:
        _chosen(style_label(selected))
    return True


def use_pack(url, quiet=False):
    """A pack chosen: read with its images (once), then shown."""
    url = str(url or '').strip()
    if not url:
        return False
    if not pack_ready(url):
        progress = xbmcgui.DialogProgress()
        progress.create('Dex Hub', _tr('تحميل حزمة الرموز مرة وحدة…', 'Loading the badge pack once…'))
        try:
            count, kept, images = load_pack(url, progress)
        except Exception as exc:
            progress.close()
            xbmc.log('[DexHub] player badge pack unavailable: %s' % exc, xbmc.LOGWARNING)
            if str(exc) != 'cancelled':
                xbmcgui.Dialog().notification('Dex Hub', _tr('تعذر تحميل الحزمة؛ المحفوظة باقية',
                                                             'Could not load pack; saved pack retained'),
                                              time=3000, sound=False)
            return False
        progress.close()
        xbmc.log('[DexHub] badge pack saved: %d badges, %d of %d images on the device' % (count, kept, images), xbmc.LOGINFO)
    else:
        _set('player_badges_json_url', url)
    _set('player_badge_style', 'custom')
    _ensure_extra_osd()
    publish()
    if not quiet and not warn_old_skin():
        _chosen(pack_name(url))
    return True


def use_preset(key):
    preset = next((p for p in PRESETS if p[0] == key), None)
    return use_pack(preset[3]) if preset else False


def _folder_notice():
    xbmcgui.Dialog().ok(_tr('رموز من مجلدك', 'Badges from your folder'), _tr(
        'المجلد: %s[CR]بدّل أي صورة فيه بصورة بنفس الاسم (الأسماء في README.txt) وتظهر في المشغّل. '
        'توصل له من مدير الملفات في كودي أو من الشبكة: Userdata ثم addon_data ثم plugin.video.dexhub ثم badges.',
        'Folder: %s[CR]Replace any image in it with one of the same name (listed in README.txt) and the player shows it. '
        'Reach it from Kodi\'s file manager or over the network: Userdata, addon_data, plugin.video.dexhub, badges.')
        % folder_shown())


def folder_menu(txt=None):
    """The folder's options: where it is, another folder, the skin's images back."""
    while True:
        options = [_tr('مكان المجلد وأسماء الصور', 'Where it is and the image names'),
                   _tr('اختيار مجلد آخر', 'Choose another folder'),
                   _tr('رجوع للمجلد الافتراضي', 'Back to the default folder'),
                   _tr('إرجاع صور السكين الأصلية للمجلد', 'Put the skin\'s images back in the folder'),
                   _tr('استخدام رموز المجلد الآن', 'Use the folder\'s badges now')]
        pick = xbmcgui.Dialog().select(_tr('رموز من مجلدك', 'Badges from your folder'), options)
        if pick < 0:
            return True
        try:
            if pick == 0:
                prepare_folder()
                xbmcgui.Dialog().textviewer(folder_shown(), readme())
            elif pick == 1:
                chosen = xbmcgui.Dialog().browse(0, _tr('مجلد الرموز', 'Badge folder'), 'files', '', False, False,
                                                 folder_path())
                if chosen and chosen != folder_path():
                    prepare_folder(chosen)
                    _set('player_badges_folder', chosen)
                    publish()
            elif pick == 2:
                _set('player_badges_folder', '')
                prepare_folder()
                publish()
            elif pick == 3:
                if xbmcgui.Dialog().yesno('Dex Hub', _tr('تُستبدل صور المجلد بصور السكين الأصلية. متأكد؟',
                                                        'The folder\'s images are replaced by the skin\'s. Sure?')):
                    prepare_folder(refill=True)
                    publish()
            elif pick == 4:
                select_style('folder')
                return True
        except Exception as exc:
            xbmc.log('[DexHub] badge folder: %s' % exc, xbmc.LOGWARNING)
            xbmcgui.Dialog().notification('Dex Hub', _tr('تعذر الوصول للمجلد', 'Could not reach the folder'),
                                          time=3000, sound=False)


def show(txt=None):
    """Other skins (and the add-on's settings): the styles in a list."""
    while True:
        labels = [_tr('مؤطرة (صور السكين)', 'Framed (the skin\'s images)')]
        labels += [_tr(ar, en) for _key, ar, en, _url in PRESETS]
        labels += [_tr('من مجلدي (تبديل يدوي)', 'From my folder (replace by hand)'),
                   _tr('بسيطة (كلمات)', 'Minimal (words)'), _tr('مدمجة (سطر)', 'Compact (one line)'),
                   _tr('حزمة برابط JSON', 'Pack from a JSON link'), _tr('بدون رموز', 'No badges')]
        pick = xbmcgui.Dialog().select(_tr('رموز المشغّل', 'Player badges'), labels)
        if pick < 0:
            return True
        if pick == 0:
            return select_style('colorful')
        if pick <= len(PRESETS):
            if use_pack(PRESETS[pick - 1][3]):
                return True
            continue
        rest = pick - len(PRESETS)
        if rest == 1:
            folder_menu()
            return True
        if rest == 2:
            return select_style('minimal')
        if rest == 3:
            return select_style('compact')
        if rest == 4:
            choose_pack()
            return True
        return select_style('off')


def choose_pack(txt=None):
    addon = _addon()
    current = addon.getSetting('player_badges_json_url') or ''
    url = xbmcgui.Dialog().input(_tr('رابط JSON لحزمة Badger', 'Badger pack JSON link'),
                                 defaultt='' if preset_of(current) else current)
    url = (url or '').strip()
    if not url:
        return True
    if pack_ready(url) and not preset_of(url):
        # a link chosen again: read anew (the pack may have changed)
        shutil.rmtree(_pack_dir(url), ignore_errors=True)
    if use_pack(url):
        preview()
    return True
