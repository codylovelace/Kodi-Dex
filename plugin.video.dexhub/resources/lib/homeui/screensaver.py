# -*- coding: utf-8 -*-
"""The Dex Hub screensaver (v5.10.103).

Dex Hub is a Kodi screensaver too: addon.xml has a second extension point
(xbmc.ui.screensaver, library screensaver.py), so Dex Hub appears in Kodi's
list of screensavers (idea from the Nuvio Hub community build). It shows the
artwork of the titles on the user's own Home as a slow slideshow, each with
its logo and the row it came from, and the clock.

The pictures come from the rows the Home saved for its next start
(home_*.json in the Home's profile folder), so the screensaver asks no
server for anything; Kodi's image cache already holds most of them.

Layout contract with resources/skins/Default/1080i/dexhub_screensaver.xml:
  ss.slot            'a' or 'b': the slide on screen (b lies above a and fades)
  ss.<slot>.fanart   the picture of each slide
  ss.<slot>.logo / title / meta / row   its texts
  ss.<slot>.zoom     '1' while its slow zoom runs ('' resets it, hidden)
  ss.<slot>.kb       '1' or '2': which way that zoom drifts
  ss.brand           the Dex Hub backdrop when the Home has saved nothing yet
"""
import json
import os
import random
import threading

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = 'plugin.video.dexhub'
SLIDE = 12.0            # seconds a picture stays
FADE = 1.8              # the crossfade (the skin's own fade time, plus a margin)
MAX_SLIDES = 80
_TYPES = {'movie': 'فيلم', 'series': 'مسلسل', 'episode': 'حلقة'}


def _addon():
    return xbmcaddon.Addon(ADDON_ID)


def _profile():
    return os.path.join(xbmcvfs.translatePath(_addon().getAddonInfo('profile')), 'homeui')


def _tr(text):
    try:
        from ..i18n import tr
        return tr(text)
    except Exception:
        return text


def slides():
    """The Home's saved titles that have a backdrop, shuffled."""
    seen, out = set(), []
    for page in ('all', 'movie', 'series'):
        try:
            with open(os.path.join(_profile(), 'home_%s.json' % page), 'r', encoding='utf-8') as handle:
                data = json.load(handle) or {}
        except Exception:
            continue
        for row in data.get('rows') or []:
            if not isinstance(row, dict):
                continue
            for tile in row.get('tiles') or []:
                if not isinstance(tile, dict) or tile.get('kind') != 'work':
                    continue
                fanart = str(tile.get('fanart') or '')
                if not fanart or fanart == tile.get('poster'):
                    continue
                key = tile.get('imdb_id') or tile.get('tmdb_id') or fanart
                if key in seen:
                    continue
                seen.add(key)
                media = tile.get('media_type') or ''
                meta = []
                if tile.get('year'):
                    meta.append(str(tile.get('year')))
                if media in _TYPES:
                    meta.append(_tr(_TYPES[media]))
                genres = [str(g) for g in (tile.get('genres') or []) if g][:2]
                if genres:
                    meta.append(' / '.join(genres))
                logo = str(tile.get('clearlogo') or '')
                if logo.lower().split('?', 1)[0].endswith('.svg'):
                    logo = ''
                out.append({
                    'fanart': fanart, 'logo': logo,
                    'title': str(tile.get('show') or tile.get('title') or ''),
                    'meta': '   •   '.join(meta), 'row': str(row.get('title') or ''),
                })
    random.shuffle(out)
    return out[:MAX_SLIDES]


class Saver(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(Saver, self).__init__(*args)
        self.slides = list(kwargs.get('slides') or [])
        self.brand = kwargs.get('brand') or ''
        self.done = threading.Event()
        self.slot = ''
        self.index = -1
        self._thread = None

    def onInit(self):
        self.setProperty('ss.brand', self.brand)
        if not self.slides:
            self.setProperty('ss.empty', '1')
            return
        self._show('a', self._next_slide())
        if len(self.slides) > 1:
            self._thread = threading.Thread(target=self._loop, name='DexHub-screensaver')
            self._thread.daemon = True
            self._thread.start()

    def _next_slide(self):
        self.index = (self.index + 1) % len(self.slides)
        return self.slides[self.index]

    def _fill(self, slot, slide):
        for key in ('fanart', 'logo', 'title', 'meta', 'row'):
            self.setProperty('ss.%s.%s' % (slot, key), slide.get(key) or '')
        self.setProperty('ss.%s.long' % slot, '1' if len(slide.get('title') or '') > 24 else '')
        self.setProperty('ss.%s.kb' % slot, random.choice(('1', '2')))

    def _show(self, slot, slide):
        """Put ``slide`` in ``slot`` and bring that slot up."""
        self._fill(slot, slide)
        self.setProperty('ss.%s.zoom' % slot, '1')
        self.setProperty('ss.slot', slot)
        self.slot = slot

    def _loop(self):
        monitor = xbmc.Monitor()
        while not self.done.is_set():
            # the next picture goes into the hidden slot a few seconds early,
            # so it has loaded by the time it fades in
            if self.done.wait(SLIDE - 4.0) or monitor.abortRequested():
                break
            other = 'b' if self.slot == 'a' else 'a'
            old = self.slot
            self.setProperty('ss.%s.zoom' % other, '')
            self._fill(other, self._next_slide())
            if self.done.wait(4.0) or monitor.abortRequested():
                break
            self.setProperty('ss.%s.zoom' % other, '1')
            self.setProperty('ss.slot', other)
            self.slot = other
            if self.done.wait(FADE):
                break
            # the picture now covered starts its next zoom from the beginning
            self.setProperty('ss.%s.zoom' % old, '')

    def onAction(self, action):
        try:
            if action.getId() == 203:    # Kodi reading its keymaps again
                return
        except Exception:
            pass
        self.stop()

    def onClick(self, control_id):
        self.stop()

    def stop(self):
        if self.done.is_set():
            return
        self.done.set()
        try:
            self.close()
        except Exception:
            pass


class _Monitor(xbmc.Monitor):
    def __init__(self, window):
        super(_Monitor, self).__init__()
        self.window = window

    def onScreensaverDeactivated(self):
        self.window.stop()

    def onAbortRequested(self):
        self.window.stop()


def run():
    """Kodi starts the screensaver (screensaver.py), or the user previews it."""
    addon = _addon()
    path = addon.getAddonInfo('path')
    brand = os.path.join(path, 'resources', 'media', 'fanart.jpg')
    try:
        found = slides()
    except Exception:
        found = []
    window = Saver('dexhub_screensaver.xml', path, 'Default', '1080i', slides=found, brand=brand)
    monitor = _Monitor(window)
    try:
        window.doModal()
    finally:
        window.stop()
        del monitor
        del window


# ---------------------------------------------------------------- setup
_PREVIOUS = 'screensaver_previous'


def _rpc(method, params=None):
    try:
        raw = xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                                              'params': params or {}}))
        return (json.loads(raw) or {}).get('result')
    except Exception:
        return None


def current_mode():
    found = _rpc('Settings.GetSettingValue', {'setting': 'screensaver.mode'}) or {}
    return str(found.get('value') or '') if isinstance(found, dict) else ''


def enable():
    """Make Dex Hub Kodi's screensaver; the previous choice is kept for off()."""
    mode = current_mode()
    if mode and mode != ADDON_ID:
        try:
            with open(os.path.join(_profile(), _PREVIOUS), 'w') as handle:
                handle.write(mode)
        except Exception:
            pass
    return _rpc('Settings.SetSettingValue', {'setting': 'screensaver.mode', 'value': ADDON_ID}) is not None


def disable():
    previous = ''
    try:
        with open(os.path.join(_profile(), _PREVIOUS), 'r') as handle:
            previous = handle.read().strip()
    except Exception:
        pass
    if not previous or previous == ADDON_ID:
        previous = 'screensaver.xbmc.builtin.dim'
    return _rpc('Settings.SetSettingValue', {'setting': 'screensaver.mode', 'value': previous}) is not None


def preview():
    """Show it now, in this invocation (any key ends it)."""
    run()


def setup_menu(tr=None):
    """The screensaver options (Home menu, Dex Hub settings)."""
    tr = tr or _tr
    from .options import choose
    on = current_mode() == ADDON_ID
    options = []
    if on:
        options.append(('off', tr('إيقاف شاشة توقف Dex Hub')))
    else:
        options.append(('on', tr('استخدام شاشة توقف Dex Hub')))
    options.append(('preview', tr('معاينة الآن')))
    subtitle = tr('شاشة التوقف مفعّلة') if on else tr('صور أعمال صفحتك الرئيسية مع الساعة')
    choice = choose(tr('شاشة التوقف'), [label for _key, label in options], subtitle=subtitle)
    if choice < 0:
        return
    key = options[choice][0]
    message = ''
    if key == 'on':
        message = tr('شاشة توقف Dex Hub مفعّلة') if enable() else tr('تعذر تغيير شاشة التوقف')
    elif key == 'off':
        message = tr('رجعت شاشة التوقف السابقة') if disable() else tr('تعذر تغيير شاشة التوقف')
    elif key == 'preview':
        preview()
    if message:
        try:
            xbmcgui.Dialog().notification('Dex Hub', message, xbmcgui.NOTIFICATION_INFO, 2500, sound=False)
        except Exception:
            pass
