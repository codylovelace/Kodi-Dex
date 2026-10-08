# -*- coding: utf-8 -*-
"""Ultra-light front door for Dex Hub.

Home and the normal Settings pages are navigation-only.  Importing the legacy
plugin router for those pages used to pull in search, stream, metadata and
playback stacks before the user had asked for any of them.  This module keeps
those hot paths tiny and defers the large router until a real feature opens.
"""
import json
import os
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

_NOT_HANDLED = object()
try:
    from resources.lib.settings_cache import cached_addon as _cached_addon
    _ADDON = _cached_addon()
except Exception:
    _ADDON = xbmcaddon.Addon()
_ADDON_ID = _ADDON.getAddonInfo('id') or 'plugin.video.dexhub'
_BASE = 'plugin://%s/' % _ADDON_ID
_MEDIA = os.path.join(_ADDON.getAddonInfo('path'), 'resources', 'media')


def _params():
    try:
        raw = sys.argv[2] if len(sys.argv) > 2 else ''
        return dict(parse_qsl(str(raw or '').lstrip('?'), keep_blank_values=True))
    except Exception:
        return {}


def _handle():
    try:
        return int(sys.argv[1])
    except Exception:
        return -1


def _url(**query):
    return _BASE + '?' + urlencode(query)


def _media(name):
    return os.path.join(_MEDIA, name)


def _arabic():
    try:
        return (_ADDON.getSetting('ui_language') or 'English').strip().lower() == 'arabic'
    except Exception:
        return False


def _txt(ar, en):
    return ar if _arabic() else en


def _art(asset, fanart='fanart.jpg', tile='square'):
    icon = _media(asset)
    bg = _media(fanart)
    if tile == 'landscape':
        return {
            'icon': icon, 'thumb': icon, 'poster': icon,
            'landscape': icon, 'banner': icon, 'fanart': bg,
            'clearlogo': icon, 'logo': icon,
        }
    return {
        'icon': icon, 'thumb': icon, 'poster': icon,
        'landscape': icon, 'banner': icon, 'fanart': bg,
        'clearlogo': icon, 'logo': icon,
    }


def _capture_sink():
    """The Home's capture module while a Home page runs this route in-process.

    v5.10.140: the Dex Hub Home (homeui) and the skin's grids (skin_list) run
    listing routes with a capture sink instead of a Kodi directory. These
    light menus wrote straight to xbmcplugin, so a captured route (the Home's
    Favorites tab since 5.10.137) came back empty.
    """
    try:
        from resources.lib import capture as _capture
    except Exception:
        return None
    try:
        return _capture if _capture.active() else None
    except Exception:
        return None


def _add(label, action='', asset='settings.png', plot='', folder=True,
         query=None, tile='square', properties=None, art=None):
    sink = _capture_sink()
    handle = _handle()
    if handle < 0 and sink is None:
        return
    q = dict(query or {})
    if action:
        q['action'] = action
    path = _url(**q)
    item = xbmcgui.ListItem(label=label)
    try:
        # Kodi 20+: the info tag (setInfo is deprecated and logs a warning)
        tag = item.getVideoInfoTag()
        tag.setTitle(label)
        tag.setPlot(plot or '')
    except Exception:
        try:
            item.setInfo('video', {'title': label, 'plot': plot or ''})
        except Exception:
            pass
    own_art = dict((k, v) for k, v in dict(art or {}).items() if v)
    art = _art(asset, tile=tile)
    art.update(own_art)
    try:
        item.setArt(art)
    except Exception:
        pass
    props = {
        'fanart_image': art.get('fanart') or '',
        'dexhub.light_entry': '1',
        'dexhub.tile_shape': tile,
    }
    props.update(dict(properties or {}))
    try:
        item.setProperties(props)
    except Exception:
        pass
    if sink is not None:
        sink.record(path, bool(folder), label, '', {'title': label, 'plot': plot or ''},
                    art, {}, None, props, [], item)
        return
    xbmcplugin.addDirectoryItem(handle, path, item, isFolder=bool(folder))


def _end(content='files', cache=True):
    if _capture_sink() is not None:
        return True
    handle = _handle()
    if handle >= 0:
        try:
            xbmcplugin.setContent(handle, content)
        except Exception:
            pass
        xbmcplugin.endOfDirectory(handle, cacheToDisc=bool(cache))
    return True


def _notify(message):
    try:
        xbmcgui.Dialog().notification('Dex Hub', message, xbmcgui.NOTIFICATION_INFO, 2200)
    except Exception:
        pass


def _linked_json(setting_id):
    """Read only the tiny stored auth blob; never import a server client on Home."""
    try:
        value = json.loads(_ADDON.getSetting(setting_id) or '{}')
        linked = bool(value.get('token') and value.get('user_id') and value.get('url'))
        # Silo 5.10.2 moved from an account-only Jellyfin compatibility token
        # to the native account + household-profile contract. Do not label an
        # old blob as connected until a profile has actually been selected.
        if setting_id == 'silo_auth_json':
            linked = linked and bool(value.get('profile_id'))
        return linked
    except Exception:
        return False


def _welcome_once(launch=''):
    """v5.10.28: one-time welcome for the identity release.

    One cheap setting read on the fast path; the dialog module is imported
    only when the flag is unset, and everything is wrapped so it can never
    block or loop Home.

    v5.10.102: a first run gets the Accounts and sources screen instead (it
    welcomes too, and links Nuvio, Stremio or private sources), over this
    menu. When the Dex Hub Home opens next, it shows that screen itself.
    """
    if launch:
        return
    try:
        from ..homeui import setup
        if setup.should_show():
            # Wait for a real foreground root visit. Skin widget/background
            # requests must never bring up a pairing window.
            _open_home_when_listed(setup_only=True)
    except Exception:
        pass


def _is_root(path):
    """The add-on's root path, as Kodi's Container.FolderPath shows it."""
    path = str(path or '').strip().rstrip('?').rstrip('/')
    return path == _BASE.rstrip('/')


def _home_ui_wanted():
    """Open the Dex Hub Home window instead of the static menu?

    Only for a real launch of the add-on's root: never for a skin widget or a
    shortcut drawn on Kodi's Home screen, never for a background read of the
    root, and never when the user turned it off or reached the menu on
    purpose (action=home_classic).

    'now': Kodi's busy dialog shows that its media window is loading this
    listing, so the Home opens at once. 'watch' (v5.10.100): no busy dialog
    in time, which is how Kodi 22 often opens an add-on; the menu is drawn and
    the Home opens as soon as Kodi shows it as the page the user is on (a
    background read never becomes that page). '': not a launch.
    """
    if _handle() < 0:
        return ''
    if not _enabled('homeui_on_launch', 'true'):
        return ''
    try:
        # v5.10.109: skin.dexhub is the Dex Hub Home itself
        if xbmc.getSkinDir() == 'skin.dexhub':
            return ''
    except Exception:
        pass
    try:
        if xbmc.getCondVisibility('Window.IsActive(home)'):
            return ''
        if xbmc.getCondVisibility('Window.IsVisible(home) + !Window.IsActive(videos)'):
            return ''
    except Exception:
        pass
    if _foreground_listing():
        return 'now'
    try:
        if not xbmc.getCondVisibility('Window.IsActive(videos)'):
            return ''
        current = xbmc.getInfoLabel('Container.FolderPath') or ''
    except Exception:
        return ''
    if current.startswith(_BASE) and not _is_root(current):
        # a preview of '..' read while the user browses inside Dex Hub
        return ''
    return 'watch'


def _open_home_when_listed(timeout=1.5, setup_only=False):
    """Open the Dex Hub Home once Kodi shows the root menu as the current page.

    v5.10.100: runs after the menu has been handed to Kodi. A root read that
    was only a skin preview never becomes the Videos window's page, so it
    never opens the Home.
    """
    import threading
    import time

    def run():
        monitor = xbmc.Monitor()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if monitor.waitForAbort(0.1):
                return
            try:
                if xbmc.getCondVisibility('System.HasModalDialog'):
                    continue
                if not xbmc.getCondVisibility('Window.IsActive(videos)'):
                    return
                if _is_root(xbmc.getInfoLabel('Container.FolderPath')):
                    if setup_only:
                        from ..homeui import setup
                        if setup.should_show():
                            # Reserve before launching: concurrent root reads
                            # cannot stack multiple first-run dialogs.
                            xbmcgui.Window(10000).setProperty(setup.ASKED_PROP, '1')
                            xbmc.executebuiltin('RunPlugin(%s)' % _url(action='home_setup', first='1'))
                        return
                    xbmc.log('[DexHub] root menu shown: opening the Dex Hub Home', xbmc.LOGINFO)
                    # step back out of the menu first: Kodi reopens a media
                    # window on the last folder it showed, so the next visit
                    # to the add-ons would land on this menu and reopen the Home
                    xbmc.executebuiltin('Action(ParentDir)')
                    xbmc.executebuiltin('RunPlugin(%s)' % _url(action='home_ui'))
                    return
            except Exception:
                return
    thread = threading.Thread(target=run, name='DexHub-root-home')
    thread.daemon = True
    thread.start()


def _foreground_listing(timeout=0.6):
    """True when Kodi's own media window is loading this listing.

    v5.10.97: a media window shows Kodi's busy dialog 100 ms into every
    listing it loads; a background read of the same path never does. Arctic
    Fuse's combined views read the focused folder in the background to
    preview it, including the '..' item, whose path is this root: opening a
    series from the Dex Hub Home made that preview relaunch (or bring back)
    the Home window three seconds later, every time. The same preview on the
    add-on list reopened the Home right after the user had closed it.
    """
    import time
    deadline = time.monotonic() + timeout
    condition = 'Window.IsActive(busydialog) | Window.IsActive(busydialognocancel)'
    while True:
        try:
            if xbmc.getCondVisibility(condition):
                return True
        except Exception:
            return True
        if time.monotonic() >= deadline:
            return False
        xbmc.sleep(30)


def home(classic=False):
    """Static, zero-network Home.

    Do not read progress databases or metadata caches merely to draw the
    navigation anchors. The destination pages own their data and refresh.
    """
    launch = '' if classic else _home_ui_wanted()
    if launch == 'now':
        # v5.10.96: fail this directory quietly (Kodi stays where the user
        # launched from) and open the Dex Hub Home window in its own
        # invocation, so its lifetime is not tied to a directory request.
        try:
            xbmcplugin.endOfDirectory(_handle(), succeeded=False, updateListing=False,
                                      cacheToDisc=False)
        except Exception:
            pass
        xbmc.executebuiltin('RunPlugin(%s)' % _url(action='home_ui'))
        return True
    silo_linked = _linked_json('silo_auth_json')
    silo_label = 'Silo • %s' % _txt(
        'متصل ✓' if silo_linked else 'اضغط للربط',
        'Linked ✓' if silo_linked else 'Connect')
    _add(_txt('Dex Hub Home', 'Dex Hub Home'), 'home_ui', 'dexhub_infinity.png',
         _txt('الواجهة الكاملة: بانر متحرك، تريلر في الخلفية وكوليكشنات Nuvio.',
              'The full-screen home: animated banner, background trailers and Nuvio collections.'),
         folder=False, tile='landscape')
    rows = [
        ('Continue Watching', 'continue', 'root_continue.png', 'square'),
        ('Next Up', 'nextup', 'up-next.png', 'square'),
        ('Favorites', 'favorites', 'cat_watchlist.png', 'square'),
        ('Movies', 'nuvio_home_media', 'collection_movies.png', 'landscape'),
        ('TV Shows', 'nuvio_home_media', 'collection_tv.png', 'landscape'),
        ('Collections', 'collection_sets', 'cat_collection.png', 'landscape'),
        ('Plex', 'plex_menu', 'provider_plex.png', 'square'),
        ('Emby', 'emby_menu', 'provider_emby.png', 'square'),
        ('Jellyfin', 'jellyfin_menu', 'provider_jellyfin.png', 'square'),
        (silo_label, 'silo_menu', 'provider_silo.png', 'square'),
        ('Search', 'hub_search_menu', 'root_search_movie.png', 'square'),
        ('Settings', 'setup_center', 'settings.png', 'square'),
        ('Sources', 'providers', 'root_sources.png', 'square'),
    ]
    for label, action, asset, tile in rows:
        query = {}
        props = {}
        if label == 'Movies':
            query['media'] = 'movie'
            props['dexhub.nuvio_home_media'] = 'movie'
        elif label == 'TV Shows':
            query['media'] = 'series'
            props['dexhub.nuvio_home_media'] = 'series'
        _add(label, action, asset, folder=True, query=query, tile=tile,
             properties=props)
    # The account status is part of the label, so never let Kodi preserve an
    # older Home directory after linking or updating the add-on.
    result = _end('files', cache=False)
    if launch == 'watch':
        _open_home_when_listed()
    else:
        _welcome_once(launch)
    return result


def settings_center():
    """The same sections are reachable from any skin and the native settings."""
    sections = (
        ('المظهر والثيم', 'Appearance & Theme', 'simple_appearance', 'dexhub_infinity.png', True),
        ('الرئيسية والويدجت والتريلر', 'Home, Widgets & Trailers', 'simple_home_options', 'collection_movies.png', False),
        ('المشغّل والجودة', 'Player & Quality', 'setup_playback_quality', 'play.png', True),
        ('الحسابات والمصادر', 'Accounts & Sources', 'home_setup', 'sync_accounts.png', False),
        ('المفضلة واللستات', 'Favorites & Lists', 'simple_favorites', 'root_favorites.png', False),
        ('متابعة المشاهدة', 'Continue Watching & Next Up', 'simple_watching', 'root_continue.png', False),
        ('الميتاداتا والصور', 'Metadata & Artwork', 'simple_metadata', 'tmdb.png', True),
        ('IPTV والسيرفرات', 'IPTV & Servers', 'simple_sources_libraries', 'root_sources.png', True),
        ('الترجمة', 'Subtitles', 'setup_subtitles', 'settings.png', False),
        ('الأداء والصيانة', 'Performance & Maintenance', 'setup_performance', 'settings.png', True),
        ('الإعداد السريع', 'Quick Setup', 'simple_quick_setup', 'settings.png', False),
        ('الإعدادات الكاملة', 'All Settings', 'open_settings', 'settings.png', False),
    )
    for ar, en, action, icon, folder in sections:
        _add(_txt(ar, en), action, icon, folder=folder)
    return _end('files', cache=False)


def favorite_lists(source='', allow_direct=True):
    """Cached source navigation: do not import account clients or metadata.

    ``_NOT_HANDLED`` (with ``allow_direct``): only Dex Hub's own list applies,
    and the caller lists it instead of a menu with one row.
    """
    from resources.lib import favorites_store
    win = xbmcgui.Window(10000)
    if not win.getProperty('dexhub.fav_mirror_done') and not win.getProperty('dexhub.fav_mirror_syncing'):
        win.setProperty('dexhub.fav_mirror_syncing', '1')
        def refresh():
            try:
                favorites_store.refresh_external_mirror()
            finally:
                win.setProperty('dexhub.fav_mirror_done', '1')
                win.clearProperty('dexhub.fav_mirror_syncing')
                path = xbmc.getInfoLabel('Container.FolderPath') or ''
                query = dict(parse_qsl(urlsplit(path).query))
                if 'plugin.video.dexhub' in path and query.get('action') in ('favorites', 'favorite_source_menu') and not query.get('source'):
                    xbmc.executebuiltin('Container.Refresh')
        from resources.lib.runtime_tasks import submit_optional
        if not submit_optional(refresh, key='favorites-external-mirror'):
            win.clearProperty('dexhub.fav_mirror_syncing')
    counts = favorites_store.source_counts()
    if source == 'trakt':
        _add(_txt('قائمة المشاهدة', 'Watchlist'), 'favorites', 'trakt.png',
             query={'source': 'trakt'})
        _add(_txt('المفضلة في Trakt', 'Trakt Favorites'), 'trakt_favorites', 'trakt.png')
        _add(_txt('لستاتي', 'My Lists'), 'trakt_my_lists', 'trakt.png')
    elif source == 'simkl':
        _add('Plan to Watch', 'favorites', 'sync_accounts.png', query={'source': 'simkl'})
        _add(_txt('كل لستاتي في Simkl', 'All Simkl Lists'), 'simkl_lists_menu', 'sync_accounts.png')
    elif source == 'mdblist':
        _add(_txt('قائمة المشاهدة', 'Watchlist'), 'favorites', 'sync_accounts.png', query={'source': 'mdblist'})
        _add('MDBList', 'mdblist_menu', 'sync_accounts.png')
    else:
        shown = _favorite_sources(counts)
        if allow_direct and shown == ['local'] and counts.get('local'):
            # v5.10.140: only Dex Hub's own list applies (no service linked,
            # nothing mirrored) and it has titles: open it at once instead of
            # a one-line menu. The full router lists it (plugin.favorites,
            # source 'local'); an empty list keeps the menu and its Settings.
            return _NOT_HANDLED
        names = {
            'local': ('مفضلتي', 'My Favorites', 'root_favorites.png'),
            'nuvio': ('Nuvio', 'Nuvio', 'sync_accounts.png'),
            'trakt': ('Trakt', 'Trakt', 'trakt.png'),
            'simkl': ('Simkl', 'Simkl', 'sync_accounts.png'),
            'mdblist': ('MDBList', 'MDBList', 'sync_accounts.png'),
            'stremio': ('Stremio', 'Stremio', 'sync_accounts.png'),
        }
        try:
            covers = favorites_store.source_covers()
        except Exception:
            covers = {}
        for key in shown:
            ar, en, icon = names[key]
            action = 'favorite_source_menu' if key in ('trakt', 'simkl', 'mdblist') else 'favorites'
            poster, backdrop = covers.get(key) or ('', '')
            # the newest favourite's poster is the card's cover (the Home's
            # grid shows no add-on icon as a poster)
            cover = {'poster': poster, 'thumb': poster, 'fanart': backdrop,
                     'landscape': backdrop} if poster else None
            _add('%s · %d' % (_txt(ar, en), counts.get(key, 0)), action, icon,
                 query={'source': key}, properties={'dexhub.favorite.source': key}, art=cover)
        _add(_txt('الإعدادات', 'Settings'), 'simple_favorites', 'settings.png', folder=False)
    return _end('files', cache=False)


def _profile_file(name):
    """Is this file in Dex Hub's profile folder (an account's token)?"""
    try:
        import xbmcvfs
        folder = xbmcvfs.translatePath(_ADDON.getAddonInfo('profile'))
        return os.path.isfile(os.path.join(folder, name))
    except Exception:
        return False


def _favorite_sources(counts):
    """The favourite sources worth a row, in order (v5.10.140).

    Dex Hub's own list always; a service only when it holds favourites or is
    linked (its token file, a key), so an unused service is not an empty row.
    Reads files and settings only: no account client is imported here.
    """
    counts = dict(counts or {})
    linked = {
        'nuvio': _profile_file('nuvio_token.json'),
        'trakt': _enabled('enable_trakt', 'true') and _profile_file('trakt_token.json'),
        'simkl': _enabled('enable_simkl', 'true') and _profile_file('simkl_token.json'),
        'mdblist': bool((_ADDON.getSetting('mdblist_api_key') or '').strip()),
        'stremio': False,
    }
    out = ['local']
    for key in ('nuvio', 'trakt', 'simkl', 'mdblist', 'stremio'):
        if counts.get(key) or linked.get(key):
            out.append(key)
    return out


def favorites_dialog():
    _close_native_settings_dialog()
    while True:
        separate = (_ADDON.getSetting('favorites_layout') or 'separate') == 'separate'
        pick = _select_dialog(_txt('المفضلة واللستات', 'Favorites & Lists'), [
            _row(_txt('طريقة العرض', 'Layout'), _txt('قوائم حسب المصدر', 'Lists by source') if separate else _txt('مجمّعة', 'Merged')),
            _row(_txt('مزامنة Simkl: Plan to Watch', 'Sync Simkl: Plan to Watch'), _txt('مفعّل', 'On') if _enabled('watchlist_merge_simkl', 'true') else _txt('مطفأ', 'Off')),
            _row(_txt('مزامنة MDBList Watchlist', 'Sync MDBList Watchlist'), _txt('مفعّل', 'On') if _enabled('watchlist_merge_mdblist', 'true') else _txt('مطفأ', 'Off')),
            _row(_txt('متابعة المشاهدة والحلقة التالية', 'Continue Watching & Next Up')),
            _row(_txt('إعدادات Trakt', 'Trakt settings')),
            _row(_txt('فتح اللستات', 'Open Lists')),
        ])
        if pick < 0:
            return True
        if pick == 0:
            _ADDON.setSetting('favorites_layout', 'merged' if separate else 'separate')
        elif pick in (1, 2):
            key = ('watchlist_merge_simkl', 'watchlist_merge_mdblist')[pick - 1]
            _ADDON.setSetting(key, 'false' if _enabled(key, 'true') else 'true')
        elif pick == 3:
            watching_dialog()
        elif pick == 4:
            trakt_dialog()
        else:
            xbmc.executebuiltin('ActivateWindow(Videos,%s,return)' % _url(action='favorites'))
            return True
        _invalidate_settings()


def home_options_dialog():
    """Shared Home preferences; editing never reloads the skin or calls HTTP."""
    _close_native_settings_dialog()
    options = (
        ('homeui_trailers', 'true', 'التريلر التلقائي', 'Automatic trailers', None),
        ('homeui_trailer_delay', '3', 'انتظار التريلر', 'Trailer delay', ('1', '3', '5', '8', '12')),
        ('homeui_trailer_sound', 'true', 'صوت التريلر', 'Trailer sound', None),
        ('homeui_trailer_quality', '720p', 'جودة التريلر', 'Trailer quality', ('480p', '720p', '1080p')),
        ('homeui_immersive', 'true', 'التريلر بملء الشاشة', 'Fullscreen trailer', None),
        ('homeui_carousel', 'true', 'تدوير البانر', 'Banner carousel', None),
        ('homeui_carousel_interval', '10', 'مدة البانر', 'Banner interval', ('5', '10', '15', '20', '30')),
        ('homeui_kenburns', 'true', 'تحريك الخلفيات', 'Animated backgrounds', None),
        ('homeui_focus_gif', 'true', 'الصور المتحركة', 'Animated images', None),
        ('homeui_plot_scroll', 'true', 'تحريك الحبكة', 'Scrolling plot', None),
        ('homeui_live_preview', 'true', 'معاينة القنوات', 'Channel previews', None),
        ('homeui_light_mode', 'true', 'الوضع الخفيف', 'Light mode', None),
        ('homeui_show_continue', 'true', 'صف متابعة المشاهدة', 'Continue Watching row', None),
        ('homeui_show_nextup', 'false', 'صف الحلقة التالية', 'Next Up row', None),
        ('homeui_size', 'Large', 'حجم الواجهة', 'UI size', ('Normal', 'Large', 'Extra large')),
        ('homeui_title_page', 'Dex Hub', 'صفحة العمل', 'Title page', ('Dex Hub', 'TMDb Helper', 'Classic')),
    )
    cursor = 0
    while True:
        rows = [_row(_txt('ترتيب الصفوف والويدجت', 'Arrange rows & widgets')), _row('Nuvio Home')]
        for key, default, ar, en, choices in options:
            value = _ADDON.getSetting(key) or default
            shown = value if choices else (_txt('مفعّل', 'On') if value == 'true' else _txt('مطفأ', 'Off'))
            rows.append(_row(_txt(ar, en), shown))
        cursor = _select_dialog(_txt('الرئيسية والويدجت والتريلر', 'Home, Widgets & Trailers'), rows, preselect=cursor)
        if cursor < 0:
            return True
        if cursor in (0, 1):
            action = 'home_layout' if cursor == 0 else 'simple_nuvio_collections'
            if cursor == 0:
                xbmc.executebuiltin('RunPlugin(%s)' % _url(action=action))
            else:
                xbmc.executebuiltin('ActivateWindow(Videos,%s,return)' % _url(action=action))
            return True
        key, default, ar, en, choices = options[cursor - 2]
        current = _ADDON.getSetting(key) or default
        if choices:
            pick = xbmcgui.Dialog().select(_txt(ar, en), list(choices), preselect=choices.index(current) if current in choices else 0)
            if pick < 0:
                continue
            value = choices[pick]
        else:
            value = 'false' if current == 'true' else 'true'
        _ADDON.setSetting(key, value)
        _invalidate_settings()


def nuvio_collections():
    _add('Nuvio', 'nuvio_sync_menu', 'nuvio.png' if os.path.exists(_media('nuvio.png')) else 'sync_accounts.png',
         _txt('اربط الحساب مرة واحدة. Movies وTV Shows يعكسان هوم Nuvio تلقائيًا.',
              'Connect once. Movies and TV Shows automatically mirror Nuvio Home.'))
    _add(_txt('Fallback / Self-Hosted', 'Fallback / Self-Hosted'), 'selfhost_settings_menu', 'root_sources.png',
         _txt('اختياري فقط إذا لم تستخدم Nuvio أو كان الحساب غير متاح.',
              'Optional fallback only when Nuvio is not used or unavailable.'))
    return _end('files', cache=False)


def _has_auth(setting_id):
    """A server account is kept (its token is stored)."""
    try:
        value = json.loads(_ADDON.getSetting(setting_id) or '{}')
    except Exception:
        return False
    if isinstance(value, dict):
        if any(value.get(k) for k in ('token', 'access_token', 'auth_token', 'AccessToken')):
            return True
        servers = value.get('servers')
        return bool(servers)
    return bool(value)


def sources_libraries():
    """v5.10.117: the Sources page says what each source is and its state
    (linked or not, how many add-ons, the posters' source)."""
    linked = _txt('متصل ✓', 'Linked ✓')
    unlinked = _txt('اضغط للربط', 'Connect')
    try:
        from resources.lib.dexhub import store as _store
        count = len(_store.list_providers() or [])
    except Exception:
        count = 0
    try:
        from resources.lib.dexhub import nuvio_stremio_sync as _sync
        nuvio = bool(_sync.Nuvio.is_linked())
    except Exception:
        nuvio = False
    _add(_txt('إضافات Stremio وNuvio • %d', 'Stremio and Nuvio add-ons • %d') % count, 'providers',
         'root_sources.png',
         _txt('الإضافات اللي تجيب الكتالوجات والمصادر والميتاداتا: ترتيبها وتعطيلها وحذفها.',
              'The add-ons that bring catalogs, streams and metadata: order, disable or remove them.'))
    _add(_txt('إضافة مصدر جديد', 'Add a source'), 'add_provider', 'root_sources.png',
         _txt('رابط إضافة Stremio أو Nuvio (manifest.json)، أو من الجوال بكود QR.',
              'A Stremio or Nuvio add-on link (manifest.json), or from your phone with a QR code.'),
         folder=False)
    _add('Nuvio • %s' % (linked if nuvio else unlinked), 'nuvio_sync_menu', 'root_accounts.png',
         _txt('حسابك في Nuvio: إضافاته وكوليكشنه وتقدم المشاهدة.',
              'Your Nuvio account: its add-ons, collections and watch progress.'))
    for name, action, asset, setting in (('Plex', 'plex_menu', 'provider_plex.png', 'plex_auth_json'),
                                         ('Emby', 'emby_menu', 'provider_emby.png', 'emby_auth_json'),
                                         ('Jellyfin', 'jellyfin_menu', 'provider_jellyfin.png',
                                          'jellyfin_auth_json')):
        _add('%s • %s' % (name, linked if _has_auth(setting) else unlinked), action, asset,
             _txt('سيرفرك: مكتباته في الرئيسية ونسخه ضمن مصادر التشغيل.',
                  'Your server: its libraries on the Home and its copies among the streams.'))
    _add('Silo • %s' % (linked if _linked_json('silo_auth_json') else unlinked), 'silo_menu',
         'provider_silo.png',
         _txt('ربط Silo وإظهاره كمصدر أصلي داخل نتائج التشغيل.',
              'Connect Silo and show it as a native playback source.'))
    _add(_txt('البث المباشر (IPTV)', 'Live TV (IPTV)'), 'home_live', 'root_sources.png',
         _txt('قوائم M3U وحسابات Xtream: من صفحة البث المباشر، إعداد البث.',
              'M3U playlists and Xtream accounts: from the Live TV page, Live TV setup.'),
         folder=False)
    try:
        from resources.lib import meta_source as _meta_source
        current = _meta_source.source_display_name(_meta_source._default_source_id_from_setting() or 'auto')
    except Exception:
        current = ''
    current = {'Automatic': _txt('تلقائي', 'Automatic'),
               'Addon metadata': _txt('ميتاداتا الإضافة', 'Add-on metadata')}.get(current, current)
    _add(_txt('مصدر الميتاداتا والبوسترات • %s', 'Metadata and posters source • %s') % (current or 'Auto'),
         'meta_source_picker', 'tmdb.png',
         _txt('من أين تأتي البوسترات والوصف: الإضافة نفسها، TMDb Helper، أو إضافة ميتاداتا من حسابك.',
              'Where posters and plots come from: the add-on itself, TMDb Helper or a metadata add-on.'),
         folder=False)
    has_key = bool((_ADDON.getSetting('tmdb_api_key') or '').strip())
    _add(_txt('مفتاح TMDb • %s', 'TMDb key • %s') % (_txt('موجود ✓', 'set ✓') if has_key else
                                                    _txt('غير موجود', 'not set')),
         'simple_tmdb_api_key', 'tmdb.png',
         _txt('مجاني من موقع TMDb: يجلب البوسترات للعناوين اللي ما لها معرّف IMDb.',
              'Free from the TMDb site: posters for titles without an IMDb id.'),
         folder=False)
    try:
        from resources.lib import poster_doctor as _pd
        issues = [i for i in _pd.load().values() if not i.get('dismissed')]
    except Exception:
        issues = []
    _add(_txt('كتالوجات بدون بوسترات • %d', 'Catalogs without posters • %d') % len(issues), 'poster_review',
         'tmdb.png',
         _txt('الكتالوجات اللي وصلت عناوينها بدون بوسترات: السبب والحل.',
              'The catalogs whose titles came without posters: the cause and the fix.'),
         folder=False)
    _add('Trakt', 'simple_trakt', 'trakt.png',
         _txt('حالة الربط وكل خيارات Trakt الأساسية.',
              'Connection status and all essential Trakt options.'), folder=False)
    _add(_txt('حسابات أخرى', 'Other Accounts'), 'sync_accounts_menu', 'root_accounts.png',
         _txt('Stremio وSimkl وMDBList.', 'Stremio, Simkl and MDBList.'))
    return _end('files', cache=False)


def playback_quality():
    _add(_txt('المصادر وطريقة التشغيل', 'Sources & Playback'),
         'simple_source_behavior', 'root_sources.png',
         _txt('ضغط الكتالوج، اختيار المصدر، النتائج المكررة وسلوك TMDb Helper.',
              'Catalog clicks, source routing, duplicate results and TMDb Helper behavior.'),
         folder=False)
    _add(_txt('الجودة والفلاتر', 'Quality & Filters'), 'setup_quality',
         'cat_4k.png', folder=False)
    _add(_txt('الحلقات وأزرار التخطي', 'Episodes & Skip Buttons'),
         'simple_episode_controls', 'up-next.png',
         _txt('تشغيل التالي، نفس المصدر، وTIDB إذا كان مثبتًا.',
              'Next Episode, same-source playback and TIDB when installed.'),
         folder=False)
    try:
        from resources.lib import tmdbh_player as _tmdbh_player
        _tmdbh_available = bool(_tmdbh_player.has_tmdbhelper())
        _tmdbh_registered = bool(
            _tmdbh_available and _tmdbh_player.player_installed())
    except Exception:
        _tmdbh_available = False
        _tmdbh_registered = False
    if _tmdbh_registered:
        _tmdbh_label = _txt(
            'تحديث مشغّل TMDb Helper • مسجّل ✓',
            'Refresh TMDb Helper Player • Registered ✓')
        _tmdbh_plot = _txt(
            'أعد تسجيل ملف Dex Hub المضمّن واحذف النسخ القديمة المكررة.',
            'Refresh the bundled Dex Hub player and remove stale duplicate copies.')
    elif _tmdbh_available:
        _tmdbh_label = _txt(
            'تثبيت مشغّل TMDb Helper • غير مسجّل',
            'Install TMDb Helper Player • Not registered')
        _tmdbh_plot = _txt(
            'سجّل Dex Hub كمشغّل داخل TMDb Helper.',
            'Register Dex Hub as a player inside TMDb Helper.')
    else:
        _tmdbh_label = _txt(
            'تثبيت مشغّل TMDb Helper • الإضافة غير مثبتة',
            'Install TMDb Helper Player • Add-on not installed')
        _tmdbh_plot = _txt(
            'ثبّت TMDb Helper أولاً، ثم اضغط هنا لتسجيل Dex Hub كمشغّل.',
            'Install TMDb Helper first, then use this button to register Dex Hub as a player.')
    _add(_tmdbh_label, 'simple_tmdbh_install', 'tmdb.png',
         _tmdbh_plot, folder=False)
    return _end('files', cache=False)


def tmdbh_install_action():
    """Install/refresh the bundled player without loading the full router."""
    try:
        if xbmc.getCondVisibility('Window.IsVisible(addonsettings)'):
            xbmc.executebuiltin('Dialog.Close(addonsettings)')
            xbmc.sleep(120)
    except Exception:
        pass
    try:
        from resources.lib import tmdbh_player
        if not tmdbh_player.has_tmdbhelper():
            xbmcgui.Dialog().ok(
                'Dex Hub • TMDb Helper',
                _txt('TMDb Helper غير مثبّت. ثبّته أولاً ثم أعد الضغط على زر تثبيت المشغّل.',
                     'TMDb Helper is not installed. Install it first, then press Install Player again.'))
            return True
        was_installed = bool(tmdbh_player.player_installed())
        if not tmdbh_player.install(silent=True):
            xbmcgui.Dialog().ok(
                'Dex Hub • TMDb Helper',
                _txt('تعذّر تثبيت ملف المشغّل. راجع سجل Kodi.',
                     'Could not install the player file. Check the Kodi log.'))
            return True
        xbmcgui.Dialog().ok(
            'Dex Hub • TMDb Helper',
            _txt(
                '[B]%s ✓[/B]\n\nDex Hub الآن مسجّل داخل TMDb Helper. ستجده عند الضغط على Play ضمن قائمة المشغّلات.\n\nلجعله افتراضيًا: TMDb Helper ← الإعدادات ← Players.' %
                ('تم تحديث المشغّل' if was_installed else 'تم تثبيت المشغّل'),
                '[B]%s ✓[/B]\n\nDex Hub is now registered inside TMDb Helper and appears in the player list when you press Play.\n\nTo make it the default: TMDb Helper → Settings → Players.' %
                ('Player refreshed' if was_installed else 'Player installed')))
        try:
            xbmc.executebuiltin('Container.Refresh')
        except Exception:
            pass
    except Exception as exc:
        xbmcgui.Dialog().ok(
            'Dex Hub • TMDb Helper',
            _txt('فشل تثبيت المشغّل: %s' % exc,
                 'Player installation failed: %s' % exc))
    return True


def _enabled(key, default='false'):
    try:
        value = _ADDON.getSetting(key) or default
    except Exception:
        value = default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _close_native_settings_dialog():
    """Close Kodi's cached settings editor before writing from a dialog."""
    try:
        if xbmc.getCondVisibility('Window.IsVisible(addonsettings)'):
            xbmc.executebuiltin('Dialog.Close(addonsettings)')
            xbmc.sleep(180)
    except Exception:
        pass


_watching_cursor = [0]


def _multiselect_dialog(title, rows, preselect=None):
    """multiselect that shows each service's own logo beside its name."""
    preselect = list(preselect or [])
    try:
        items = []
        for row in rows:
            label, value, icon = (list(row) + ['', ''])[:3] if isinstance(
                row, (tuple, list)) else (str(row), '', '')
            item = xbmcgui.ListItem(label, value)
            if icon:
                path = icon if os.path.isabs(icon) else _media(icon)
                item.setArt({'icon': path, 'thumb': path})
            items.append(item)
        return xbmcgui.Dialog().multiselect(title, items, preselect=preselect,
                                            useDetails=True)
    except Exception:
        plain = [(r[0] if isinstance(r, (tuple, list)) else str(r)) for r in rows]
        return xbmcgui.Dialog().multiselect(title, plain, preselect=preselect)


def _row(label, value='', icon=''):
    """One dialog row: name, its current value, and the service's own logo.

    Returned as a plain tuple so callers stay readable; _select_dialog turns it
    into a ListItem only when Kodi can render the detailed layout.
    """
    return (str(label), str(value or ''), str(icon or ''))


def _select_dialog(title, labels, preselect=0):
    """Select dialog that shows real logos when any row carries one.

    v5.10.24: these menus were bare text lists — a Trakt screen with nothing
    identifying Trakt on it, and a value glued onto the label with markup
    ("Scrobble:  [B]On[/B]"). Rows built with _row() now render through
    useDetails, which puts the service's own artwork beside the setting and the
    value on its own second line. Plain strings still work unchanged, so every
    existing caller is unaffected.
    """
    rows = list(labels)
    detailed = [r for r in rows if isinstance(r, (tuple, list))]
    if detailed:
        try:
            items = []
            for row in rows:
                if isinstance(row, (tuple, list)):
                    label, value, icon = (list(row) + ['', ''])[:3]
                else:
                    label, value, icon = str(row), '', ''
                item = xbmcgui.ListItem(label, value)
                if icon:
                    path = icon if os.path.isabs(icon) else _media(icon)
                    item.setArt({'icon': path, 'thumb': path})
                items.append(item)
            return xbmcgui.Dialog().select(title, items, preselect=preselect,
                                           useDetails=True)
        except Exception:
            # Any skin or Kodi build that cannot draw the detailed layout falls
            # back to text so the menu is never unreachable.
            rows = ['%s:  [B]%s[/B]' % (r[0], r[1]) if isinstance(r, (tuple, list)) and r[1]
                    else (r[0] if isinstance(r, (tuple, list)) else r)
                    for r in rows]
    try:
        return xbmcgui.Dialog().select(title, [str(r) for r in rows], preselect=preselect)
    except TypeError:
        return xbmcgui.Dialog().select(title, [str(r) for r in rows])


def _invalidate_settings():
    try:
        from resources.lib import settings_cache
        settings_cache.invalidate()
    except Exception:
        pass


def _library_direct_mode():
    try:
        from resources.lib.library_mode import library_direct
        return library_direct(_ADDON)
    except Exception:
        return (_ADDON.getSetting('library_playback_mode') or 'dexhub') == 'native'


def _toggle_setting(key, default='false'):
    _ADDON.setSetting(key, 'false' if _enabled(key, default) else 'true')
    _invalidate_settings()


def _sync_watching_now():
    """Refresh every selected progress account only when the user asks."""
    updated = []
    errors = []
    try:
        from resources.lib.dexhub import nuvio_stremio_sync as _cloud
        targets = [name for name in _cloud.enabled_targets()
                   if _enabled('%s_sync_progress' % name, 'true')]
        if targets:
            result = _cloud.run_sync(direction='pull', targets=targets,
                                     force_full=True,
                                     sections={'addons': False,
                                               'collections': False,
                                               'progress': True,
                                               'library': False})
            if result and result.get('ok'):
                updated.extend(targets)
            else:
                errors.append(_txt('مزامنة الحساب السحابي', 'cloud account sync'))
    except Exception:
        errors.append(_txt('مزامنة الحساب السحابي', 'cloud account sync'))
    if _enabled('trakt_sync_progress', 'true'):
        try:
            from resources.lib import trakt as _trakt
            if _trakt.enabled() and _trakt.authorized():
                _trakt.import_progress(limit=100)
                updated.append('Trakt')
        except Exception:
            errors.append('Trakt')
    if _enabled('simkl_service_sync', 'true'):
        try:
            from resources.lib import simkl as _simkl
            if _simkl.enabled() and _simkl.authorized():
                _simkl.invalidate_cache('/sync/activities')
                _simkl.import_watched(force=True)
                _simkl.sync_continue_watching(limit=60)
                updated.append('Simkl')
        except Exception:
            errors.append('Simkl')
    try:
        win = xbmcgui.Window(10000)
        for key in ('dexhub.nextup_cache', 'dexhub.nextup_cache_ts'):
            win.clearProperty(key)
        from resources.lib import nextup_logic
        nextup_logic._delete_file_cache()
    except Exception:
        pass
    if updated:
        _notify(_txt('تم تحديث متابعة المشاهدة: ', 'Continue Watching updated: ') +
                ', '.join(updated))
    elif not errors:
        xbmcgui.Dialog().ok(
            'Dex Hub',
            _txt('لا يوجد حساب تقدم مرتبط. التقدم المحلي وPlex وEmby يُحفظ تلقائيًا.',
                 'No progress account is linked. Local, Plex and Emby progress is saved automatically.'))
    if errors:
        xbmcgui.Dialog().ok(
            'Dex Hub',
            _txt('تعذر تحديث: ', 'Could not update: ') + ', '.join(errors))
    return True


def _run_plugin_action(action):
    """Leave a simple dialog and hand a one-shot action to the full router."""
    try:
        xbmc.executebuiltin('RunPlugin(%s)' % _url(action=action))
    except Exception:
        _notify(_txt('تعذر تشغيل الإجراء', 'Could not run the action'))
    return True


def trakt_dialog():
    """Complete Trakt controls without exposing API credentials.

    The linked state comes from the local token file. Network work starts only
    after an explicit Link or Sync selection.

    v5.10.24: rows carry the Trakt logo and put the value on its own line
    instead of gluing it onto the label. The cursor also stays where it was
    after a toggle — every switch used to bounce the selection back to the top,
    which made turning two things off a hunt each time.
    """
    _close_native_settings_dialog()
    cursor = 1
    while True:
        try:
            from resources.lib import trakt as _trakt
            connected = bool(_trakt.authorized())
        except Exception:
            connected = False
        enabled = _enabled('enable_trakt', 'true')
        logo = 'trakt.png'
        rows = [
            _row(_txt('الحالة', 'Status'),
                 _txt('متصل ✓', 'Connected ✓') if connected else
                 _txt('غير مرتبط', 'Not linked'), logo),
            _row(_txt('مزامنة Trakt الآن', 'Sync Trakt now') if connected else
                 _txt('ربط Trakt عبر PIN', 'Link Trakt with PIN'),
                 '', 'sync_accounts.png' if connected else logo),
            _row(_txt('تشغيل Trakt', 'Trakt enabled'), _onoff(enabled), logo),
        ]
        # The switches below only do anything while Trakt itself is on, so say
        # so rather than showing four live-looking toggles that do nothing.
        off = '' if enabled else _txt('  (يتطلب تشغيل Trakt)', '  (needs Trakt on)')
        rows += [
            _row(_txt('متابعة المشاهدة والحلقة التالية', 'Continue Watching & Next Up'),
                 _onoff(_enabled('trakt_sync_progress', 'true')) + off, 'root_continue.png'),
            _row('Scrobble', _onoff(_enabled('trakt_scrobble', 'true')) + off, logo),
            _row(_txt('قائمة المشاهدة داخل المفضلة', 'Watchlist in Favorites'),
                 _onoff(_enabled('trakt_sync_watchlist', 'true')) + off, 'watchlist.png'),
            _row(_txt('بوسترات Trakt', 'Trakt artwork'),
                 _onoff(_enabled('trakt_fetch_art', 'true')) + off, logo),
        ]
        if connected:
            rows.append(_row(_txt('تسجيل خروج Trakt', 'Disconnect Trakt'), '', logo))
        choice = _select_dialog('Trakt', rows, preselect=cursor)
        if choice < 0:
            return True
        cursor = choice
        if choice == 0:
            xbmcgui.Dialog().ok(
                'Trakt',
                _txt('Trakt مرتبط وجاهز للمزامنة.' if connected else
                     'اربط Trakt عبر PIN لتظهر متابعة المشاهدة والحلقة التالية وتتم مزامنة قائمتك.',
                     'Trakt is linked and ready to sync.' if connected else
                     'Link Trakt with a PIN to enable Continue Watching, Next Up and watchlist sync.'))
        elif choice == 1:
            return _run_plugin_action('trakt_import' if connected else 'trakt_auth')
        elif choice == 2:
            _toggle_setting('enable_trakt', 'true')
        elif choice == 3:
            _toggle_setting('trakt_sync_progress', 'true')
        elif choice == 4:
            _toggle_setting('trakt_scrobble', 'true')
        elif choice == 5:
            _toggle_setting('trakt_sync_watchlist', 'true')
        elif choice == 6:
            _toggle_setting('trakt_fetch_art', 'true')
        elif choice == 7 and connected:
            if xbmcgui.Dialog().yesno(
                    'Trakt', _txt('هل تريد فصل حساب Trakt؟',
                                  'Disconnect the Trakt account?')):
                return _run_plugin_action('trakt_logout')


def watching_dialog():
    """One lightweight editor for Continue Watching and Next Up.

    Local playback plus Plex/Emby rows are always included. The selectable
    services below only control optional account imports; no provider is
    contacted while this dialog is being drawn.
    """
    _close_native_settings_dialog()
    while True:
        art_wide = str(_ADDON.getSetting('continue_art_style') or '0').strip().lower() in (
            '1', 'landscape', 'banner')
        try:
            minutes = int(float(_ADDON.getSetting('cloud_sync_interval_min') or '30'))
        except Exception:
            minutes = 30
        if minutes <= 15:
            cadence = _txt('سريعة • كل 15 دقيقة', 'Fast • every 15 minutes')
        elif minutes >= 60:
            cadence = _txt('موفرة • كل 60 دقيقة', 'Saver • every 60 minutes')
        else:
            cadence = _txt('خفيفة • كل 30 دقيقة', 'Light • every 30 minutes')
        source_count = sum(1 for key, default in (
            ('nuvio_sync_progress', 'true'),
            ('stremio_sync_progress', 'true'),
            ('trakt_sync_progress', 'true'),
            ('simkl_service_sync', 'true'),
        ) if _enabled(key, default))
        rows = [
            _row(_txt('شكل البطاقات', 'Card artwork'),
                 _txt('عريض', 'Wide') if art_wide else _txt('بوستر', 'Poster'),
                 'root_continue.png'),
            _row(_txt('التحديث التلقائي', 'Automatic refresh'), cadence, 'sync_accounts.png'),
            _row(_txt('حسابات التقدم', 'Progress accounts'), '%d' % source_count,
                 'sync_accounts.png'),
            _row(_txt('مزامنة الآن', 'Sync now'), '', 'sync_accounts.png'),
            _row(_txt('الترتيب: آخر مشاهدة أولاً', 'Order: latest activity first'),
                 _txt('المحلي وPlex وEmby دائمًا مفعلة',
                      'Local, Plex and Emby are always included'), 'root_continue.png'),
        ]
        choice = _select_dialog(
            _txt('متابعة المشاهدة والحلقة التالية', 'Continue Watching & Next Up'),
            rows, preselect=_watching_cursor[0])
        if choice < 0:
            return True
        _watching_cursor[0] = choice
        if choice == 0:
            pick = xbmcgui.Dialog().select(
                _txt('شكل البطاقات', 'Card artwork'),
                [_txt('بوستر (موصى به)', 'Poster (recommended)'),
                 _txt('عريض', 'Wide')],
                preselect=1 if art_wide else 0)
            if pick >= 0:
                _ADDON.setSetting('continue_art_style', str(pick))
        elif choice == 1:
            values = (30, 15, 60)
            labels = (
                _txt('خفيفة • كل 30 دقيقة (موصى به)', 'Light • every 30 minutes (recommended)'),
                _txt('سريعة • كل 15 دقيقة', 'Fast • every 15 minutes'),
                _txt('موفرة • كل 60 دقيقة', 'Saver • every 60 minutes'),
            )
            current = 1 if minutes <= 15 else (2 if minutes >= 60 else 0)
            pick = xbmcgui.Dialog().select(
                _txt('التحديث التلقائي', 'Automatic refresh'), list(labels), preselect=current)
            if pick >= 0:
                value = str(values[pick])
                _ADDON.setSetting('cloud_sync_interval_min', value)
                _ADDON.setSetting('trakt_service_sync_interval', value)
                # Continuous sync adds wakeups and is deliberately kept off
                # for the simple/light profiles.
                _ADDON.setSetting('cloud_sync_continuous', 'false')
        elif choice == 2:
            options = (
                ('nuvio_sync_progress', 'Nuvio', 'true', 'nuvio.png'),
                ('stremio_sync_progress', 'Stremio', 'true', 'stremio.png'),
                ('trakt_sync_progress', 'Trakt', 'true', 'trakt.png'),
                ('simkl_service_sync', 'Simkl', 'true', 'simkl.png'),
            )
            selected = [i for i, (key, _label, default, _icon) in enumerate(options)
                        if _enabled(key, default)]
            picked = _multiselect_dialog(
                _txt('حسابات التقدم (المحلي وPlex وEmby دائمًا)',
                     'Progress accounts (Local, Plex and Emby are always included)'),
                [(label, '', icon) for _key, label, _default, icon in options],
                preselect=selected)
            if picked is not None:
                picked = set(picked)
                for i, (key, _label, _default, _icon) in enumerate(options):
                    _ADDON.setSetting(key, 'true' if i in picked else 'false')
                # Selecting a linked cloud account's progress must not leave
                # its master sync switch in the old off state.
                if 0 in picked:
                    _ADDON.setSetting('nuvio_sync_enabled', 'true')
                if 1 in picked:
                    _ADDON.setSetting('stremio_sync_enabled', 'true')
        elif choice == 3:
            _sync_watching_now()
        else:
            xbmcgui.Dialog().ok(
                _txt('طريقة الدمج والترتيب', 'Merge and order'),
                _txt('يجمع Dex Hub التقدم المحلي وPlex وEmby والحسابات المختارة في قائمة واحدة، يزيل النسخة المطابقة لنفس العمل والحلقة، ثم يعرض آخر نشاط أولاً. الحلقة التالية تستخدم Trakt إن كان مفعلاً ثم تكمل من سجل المشاهدة المحلي.',
                     'Dex Hub combines local, Plex, Emby and selected account progress in one list, merges an exact matching title/episode, then shows latest activity first. Next Up uses Trakt when enabled and completes the list from local playback history.'))
        _invalidate_settings()


def appearance():
    _add(_txt('الثيم', 'Theme'), 'theme_select', 'dexhub_infinity.png', folder=False)
    _add(_txt('مظهر المشغّل: الشعار والنبذة والبيانات', 'Player appearance: logo, plot & details'), 'player_appearance', 'play.png', folder=False)
    _add(_txt('رموز المشغّل', 'Player badges'), 'player_badges', 'badges/dolby_vision.png', folder=False)
    _add(_txt('شارات مصادر التشغيل', 'Source picker badges'), 'badges_url', 'badges/dolby_vision.png', folder=False)
    _add(_txt('الرئيسية والتريلر والحركة', 'Home, trailers & motion'), 'simple_home_options', 'dexhub_infinity.png', folder=False)
    _add(_txt('الميتاداتا والصور', 'Metadata & Artwork'), 'simple_metadata', 'tmdb.png')
    return _end('files', cache=False)


def metadata_settings():
    _add('BetterPosters (BTTR)', 'betterposters_settings_dialog', 'tmdb.png', folder=False)
    _add(_txt('Fanart.tv وClearLogo', 'Fanart.tv & ClearLogo'), 'simple_fanarttv', 'dexhub_infinity.png', folder=False)
    try:
        from resources.lib.live_settings import live_setting
        _has_tmdb_key = bool((live_setting('tmdb_api_key', default='') or '').strip())
    except Exception:
        _has_tmdb_key = bool((_ADDON.getSetting('tmdb_api_key') or '').strip())
    _tmdb_state = _txt('محفوظ ✓', 'Saved ✓') if _has_tmdb_key else _txt('غير مضاف', 'Not set')
    _add(_txt('مفتاح TMDb API', 'TMDb API Key'), 'simple_tmdb_api_key', 'tmdb.png',
         _txt('الحالة: %s\nمطلوب لفتح مصادر TMDb داخل Collections المستوردة من Nuvio.' % _tmdb_state,
              'Status: %s\nRequired for TMDb-backed sources inside Collections imported from Nuvio.' % _tmdb_state), folder=False)
    try:
        from resources.lib.meta_mode import status_label as _meta_status
        _meta_state = _meta_status()
    except Exception:
        _meta_state = _txt('تلقائي (ذكي)', 'Auto (smart)')
    _add(_txt('طريقة عرض البيانات والبوسترات • %s', 'Metadata & poster appearance • %s') % _meta_state, 'meta_source_picker', 'tmdb.png',
         _txt('مثل Nuvio: نفس بيانات وبوسترات المصدر. أو TMDb Helper لبيانات موحّدة، أو AIOMetadata. الاختيار يطبّق على الإضافة والسكين.',
              'Like Nuvio: keep source metadata and posters. Or use TMDb Helper for unified metadata, or AIOMetadata. Applies to the add-on and skin.'),
         folder=False)
    _add(_txt('مسح كاش الميتاداتا وإعادة التحميل', 'Clear metadata cache and reload'), 'meta_cache_reload', 'root_refresh.png',
         _txt('يمسح كاش البيانات والصور المحفوظ محلياً (HTTP وSQLite وTMDb Helper) ويعيد رسم الصفحة الحالية.',
              'Drops the locally cached metadata (HTTP, SQLite, TMDb Helper bundles) and redraws the current page.'),
         folder=False)
    _add(_txt('تخصيص الميتاداتا المتقدم', 'Advanced Metadata Overrides'), 'meta_sources_menu', 'settings.png',
         _txt('اختياري: غيّر المصدر لمزود محدد فقط.', 'Optional: override metadata for a specific provider only.'))
    return _end('files', cache=False)


def maintenance():
    _add(_txt('وضع السرعة (بحث وتشغيل)', 'Speed profile (search and playback)'), 'setup_stability', 'settings.png',
         _txt('يطبق أسرع إعدادات البحث والمصادر دفعة وحدة: فتح النتائج مع أول مصدر، مهلات أقصر، كاش كتالوجات أطول، بدون إثراء إضافي. ما يلمس مصدر الميتاداتا.',
              'Applies the fastest search and source settings in one go: open results with the first source, shorter timeouts, longer catalog cache, no extra enrichment. Does not touch the metadata source.'),
         folder=False)
    _add(_txt('مسح الكاش', 'Clear Cache'), 'clear_cache', 'settings.png', folder=False)
    _add(_txt('التشخيص', 'Diagnostics'), 'setup_diagnostics', 'settings.png', folder=False)
    _add(_txt('نسخ احتياطي / استعادة', 'Backup / Restore'), 'config_backup_menu', 'settings.png')
    return _end('files', cache=False)


def quick_setup():
    try:
        if xbmc.getCondVisibility('Window.IsVisible(addonsettings)'):
            xbmc.executebuiltin('Dialog.Close(addonsettings)')
            xbmc.sleep(180)
    except Exception:
        pass
    if not xbmcgui.Dialog().yesno(
        'Dex Hub',
        _txt('تطبيق الإعداد البسيط والسريع؟\n\nلن تتغير حساباتك أو الكوليكشن أو إعدادات البادجز.',
             'Apply the simple and fast profile?\n\nAccounts, Collections and badge choices will not be changed.'),
    ):
        return True
    values = {
        'lightweight_mode': 'true',
        'search_style': '1',
        'quality_profile': 'Balanced (recommended)',
        'playback_open_mode': 'Choose from sources',
        'source_resolution_mode': 'DexHub picker',
        'show_playback_waiter': 'false',
        'pre_cache_next_episode': 'false',
        'streams_full_parallel_scan': 'false',
        'streams_quick_to_results': 'true',
        'kodi22_minimal_item': 'false',
        'deep_meta_enrich': 'false',
        'fanarttv_enrich': 'false',
        'verbose_logging': 'false',
        'cloud_sync_continuous': 'false',
        'large_section_limit': '24',
        'default_meta_source': 'Auto (smart)',
        'subtitle_timeout': '15',
    }
    for key, value in values.items():
        try:
            _ADDON.setSetting(key, value)
        except Exception:
            pass
    _notify(_txt('تم تطبيق الإعداد السريع ✓', 'Fast profile applied ✓'))
    return True


def source_behavior_dialog():
    """Expose useful source behavior without leaking implementation knobs."""
    _close_native_settings_dialog()
    while True:
        catalog_values = ('Dex Hub', 'TMDb Helper', 'Ask every time')
        catalog_labels = (
            'Dex Hub', 'TMDb Helper',
            _txt('اسأل كل مرة', 'Ask every time'),
        )
        catalog = _ADDON.getSetting('catalog_click_mode') or catalog_values[0]

        resolution_values = ('DexHub picker', 'Same source', 'TMDb Helper')
        resolution_labels = (
            _txt('مختار Dex Hub', 'Dex Hub picker'),
            _txt('المصدر الأصلي أولاً', 'Originating source first'),
            'TMDb Helper',
        )
        resolution = _ADDON.getSetting('source_resolution_mode') or resolution_values[0]

        playback_values = ('Choose from sources', 'Best quality automatically')
        playback_labels = (
            _txt('اختر من النتائج', 'Choose from results'),
            _txt('أفضل نتيجة تلقائيًا', 'Best result automatically'),
        )
        playback = _ADDON.getSetting('playback_open_mode') or playback_values[0]

        dedup_values = ('Off', 'Single best', 'One per addon')
        dedup_labels = (
            _txt('اعرض كل النتائج الصالحة', 'Keep every valid result'),
            _txt('نسخة واحدة فقط', 'Single best copy'),
            _txt('نسخة من كل إضافة', 'One copy per addon'),
        )
        dedup = _ADDON.getSetting('source_dedup_mode') or dedup_values[0]

        if _enabled('tmdbh_auto_play_first'):
            tmdbh_mode = 2
        elif _enabled('tmdbh_poster_picker'):
            tmdbh_mode = 1
        else:
            tmdbh_mode = 0
        tmdbh_labels = (
            _txt('صفحة النتائج الكاملة', 'Full results page'),
            _txt('اختيار سريع من المصادر', 'Quick source picker'),
            _txt('أفضل مصدر تلقائيًا', 'Best source automatically'),
        )

        rows = []
        actions = []

        def add_row(label, value, action):
            rows.append('%s:  [B]%s[/B]' % (label, value))
            actions.append(action)

        add_row(_txt('عند الضغط على العمل', 'When opening a title'),
                catalog_labels[catalog_values.index(catalog)]
                if catalog in catalog_values else catalog_labels[0], 'catalog')
        add_row(_txt('حل المصدر داخل Dex Hub', 'Source routing inside Dex Hub'),
                resolution_labels[resolution_values.index(resolution)]
                if resolution in resolution_values else resolution_labels[0], 'resolution')
        add_row(_txt('فتح نتائج المصادر', 'Source results'),
                playback_labels[playback_values.index(playback)]
                if playback in playback_values else playback_labels[0], 'playback')
        add_row(_txt('النتائج المتشابهة', 'Similar results'),
                dedup_labels[dedup_values.index(dedup)]
                if dedup in dedup_values else dedup_labels[0], 'dedup')
        add_row(_txt('تذكر آخر مصدر', 'Remember last source'),
                _txt('مفعّل', 'On') if _enabled('remember_last_source')
                else _txt('متوقف', 'Off'), 'remember')
        add_row(_txt('تشغيل سريع من مصدر العنصر نفسه', 'Quick play from the item source'),
                _txt('مفعّل', 'On') if _enabled('quick_same_source_play')
                else _txt('متوقف', 'Off'), 'quick_same')
        add_row(_txt('متابعة المكتبات: تشغيل مباشر من المصدر', 'Library Continue Watching: direct from source'),
                _txt('متوقف', 'Off') if _ADDON.getSetting('continue_native_direct') != 'true'
                else _txt('مفعّل', 'On'), 'continue_native')
        add_row(_txt('تشغيل عناصر المكتبات', 'Library playback'),
                'Dex Hub' if not _library_direct_mode()
                else _txt('مصدر المكتبة', 'Library source'), 'library_mode')
        add_row(_txt('استدعاء Dex Hub من TMDb Helper', 'Dex Hub from TMDb Helper'),
                tmdbh_labels[tmdbh_mode], 'tmdbh_mode')

        try:
            from resources.lib import tmdbh_player
            registered = bool(tmdbh_player.player_installed())
        except Exception:
            registered = False
        if registered:
            rows.append(_txt('إزالة تسجيل مشغّل Dex Hub من TMDb Helper',
                             'Remove Dex Hub player from TMDb Helper'))
            actions.append('tmdbh_remove')

        choice = _select_dialog(
            _txt('المصادر وطريقة التشغيل', 'Sources & Playback'), rows)
        if choice < 0:
            return True
        action = actions[choice]
        if action == 'catalog':
            pre = catalog_values.index(catalog) if catalog in catalog_values else 0
            picked = _select_dialog(
                _txt('عند الضغط على فيلم أو مسلسل', 'When opening a movie or show'),
                catalog_labels, pre)
            if picked >= 0:
                _ADDON.setSetting('catalog_click_mode', catalog_values[picked])
        elif action == 'resolution':
            pre = resolution_values.index(resolution) if resolution in resolution_values else 0
            picked = _select_dialog(
                _txt('كيف يحدد Dex Hub المصدر؟', 'How should Dex Hub resolve the source?'),
                resolution_labels, pre)
            if picked >= 0:
                _ADDON.setSetting('source_resolution_mode', resolution_values[picked])
        elif action == 'playback':
            pre = playback_values.index(playback) if playback in playback_values else 0
            picked = _select_dialog(
                _txt('فتح نتائج المصادر', 'Source results'), playback_labels, pre)
            if picked == 1 and not xbmcgui.Dialog().yesno(
                    'Dex Hub',
                    _txt('سيبدأ أفضل مصدر مباشرة. متابعة؟',
                         'The best source will start immediately. Continue?')):
                picked = -1
            if picked >= 0:
                _ADDON.setSetting('playback_open_mode', playback_values[picked])
        elif action == 'dedup':
            pre = dedup_values.index(dedup) if dedup in dedup_values else 0
            picked = _select_dialog(
                _txt('النتائج المتشابهة', 'Similar results'), dedup_labels, pre)
            if picked >= 0:
                _ADDON.setSetting('source_dedup_mode', dedup_values[picked])
        elif action == 'remember':
            _toggle_setting('remember_last_source')
        elif action == 'quick_same':
            _toggle_setting('quick_same_source_play')
        elif action == 'continue_native':
            _ADDON.setSetting('continue_native_direct', 'false' if _ADDON.getSetting('continue_native_direct') == 'true' else 'true')
        elif action == 'library_mode':
            selected = _select_dialog(_txt('تشغيل عناصر المكتبات', 'Library playback'),
                [_txt('من مصدر المكتبة مباشرة', 'Direct from library source'),
                 _txt('بحث المصادر عبر Dex Hub', 'Search sources through Dex Hub')])
            if selected >= 0:
                _ADDON.setSetting('library_playback_mode', ('native', 'dexhub')[selected])
        elif action == 'tmdbh_mode':
            picked = _select_dialog(
                _txt('عند استدعاء Dex Hub من TMDb Helper',
                     'When TMDb Helper calls Dex Hub'), tmdbh_labels, tmdbh_mode)
            if picked >= 0:
                _ADDON.setSetting('tmdbh_auto_play_first', 'true' if picked == 2 else 'false')
                _ADDON.setSetting('tmdbh_poster_picker', 'true' if picked == 1 else 'false')
                try:
                    from resources.lib.context_play import publish as _publish_play_mode
                    _publish_play_mode(_ADDON)
                except Exception:
                    pass
        elif action == 'tmdbh_remove':
            if xbmcgui.Dialog().yesno(
                    'Dex Hub • TMDb Helper',
                    _txt('إزالة Dex Hub من قائمة مشغّلات TMDb Helper؟',
                         'Remove Dex Hub from the TMDb Helper player list?')):
                try:
                    if tmdbh_player.uninstall(silent=True):
                        _notify(_txt('تمت إزالة تسجيل المشغّل', 'Player registration removed'))
                    else:
                        _notify(_txt('المشغّل غير مسجّل', 'Player was not registered'))
                except Exception:
                    _notify(_txt('تعذرت إزالة تسجيل المشغّل', 'Could not remove player registration'))
        _invalidate_settings()


def episode_controls_dialog():
    """One page for Next Episode plus optional TIDB-owned skip controls."""
    _close_native_settings_dialog()
    while True:
        try:
            tidb_installed = bool(
                xbmc.getCondVisibility('System.HasAddon(plugin.video.tidb)') and
                xbmcaddon.Addon('plugin.video.tidb').getAddonInfo('id'))
        except Exception:
            tidb_installed = False

        rows = []
        actions = []

        def add_toggle(label, key, default='false', action=None):
            rows.append('%s:  [B]%s[/B]' % (
                label, _txt('مفعّل', 'On') if _enabled(key, default)
                else _txt('متوقف', 'Off')))
            actions.append(action or key)

        add_toggle(_txt('تشغيل التالي بعد نهاية الحلقة',
                        'Play next after an episode ends'),
                   'auto_next_episode', 'true')
        if _enabled('auto_next_episode', 'true'):
            try:
                countdown = max(5, int(float(
                    _ADDON.getSetting('auto_next_prompt_seconds') or '20')))
            except Exception:
                countdown = 20
            rows.append(_txt('مهلة التأكيد: [B]%d ثانية[/B]' % countdown,
                             'Confirmation countdown: [B]%d seconds[/B]' % countdown))
            actions.append('countdown')
        add_toggle(_txt('زر الحلقة التالية يستخدم نفس المصدر',
                        'Next Episode button uses the same source'),
                   'next_episode_same_source', 'true')

        if tidb_installed:
            add_toggle(_txt('فرض أزرار Intro وRecap وCredits اليدوية',
                            'Force manual Intro, Recap and Credits buttons'),
                       'introdb_manual_buttons', 'true')
            rows.append(_txt('فتح إعدادات TIDB الكاملة',
                             'Open full TIDB settings'))
            actions.append('tidb_settings')

        choice = _select_dialog(
            _txt('الحلقات وأزرار التخطي', 'Episodes & Skip Buttons'), rows)
        if choice < 0:
            return True
        action = actions[choice]
        if action == 'countdown':
            values = (10, 20, 30, 45, 60)
            labels = [_txt('%d ثانية' % value, '%d seconds' % value)
                      for value in values]
            pre = min(range(len(values)), key=lambda i: abs(values[i] - countdown))
            picked = _select_dialog(
                _txt('مهلة تشغيل التالي', 'Next Episode countdown'), labels, pre)
            if picked >= 0:
                _ADDON.setSetting('auto_next_prompt_seconds', str(values[picked]))
        elif action == 'tidb_settings':
            try:
                xbmcaddon.Addon('plugin.video.tidb').openSettings()
            except Exception:
                _notify(_txt('تعذر فتح إعدادات TIDB', 'Could not open TIDB settings'))
        else:
            default = 'true' if action in (
                'auto_next_episode', 'next_episode_same_source',
                'introdb_manual_buttons') else 'false'
            _toggle_setting(action, default)
        _invalidate_settings()


def playback_dialog():
    labels = [
        _txt('اختيار المصدر في كل مرة (موصى به)', 'Choose a source each time (recommended)'),
        _txt('استخدام نفس المصدر', 'Use the same source'),
        _txt('الاختيار عبر TMDb Helper', 'Choose through TMDb Helper'),
        _txt('أفضل جودة تلقائيًا', 'Best quality automatically'),
    ]
    idx = xbmcgui.Dialog().select(_txt('طريقة التشغيل', 'Playback Mode'), labels)
    if idx < 0:
        return True
    if idx == 3 and not xbmcgui.Dialog().yesno('Dex Hub', _txt('سيبدأ أفضل مصدر مباشرة. متابعة؟', 'The best source will start directly. Continue?')):
        return True
    resolution = ('DexHub picker', 'Same source', 'TMDb Helper', 'DexHub picker')[idx]
    auto = idx == 3
    for k, v in {
        'source_resolution_mode': resolution,
        'playback_open_mode': 'Best quality automatically' if auto else 'Choose from sources',
        'remember_last_source': 'false',
    }.items():
        _ADDON.setSetting(k, v)
    _notify(_txt('تم الحفظ', 'Saved'))
    return True


def quality_dialog():
    _close_native_settings_dialog()
    vals = ('Balanced (recommended)', 'Best', 'Data saver', 'Custom')
    labels = (_txt('متوازن (موصى به)', 'Balanced (recommended)'),
              _txt('أفضل جودة', 'Best quality'),
              _txt('توفير البيانات', 'Data saver'),
              _txt('مخصص', 'Custom'))
    idx = xbmcgui.Dialog().select(_txt('الجودة', 'Quality'), list(labels))
    if idx < 0:
        return True
    _ADDON.setSetting('quality_profile', vals[idx])
    # A stale troubleshooting bypass used to silently override every profile.
    # Choosing a profile must always make that profile authoritative again.
    _ADDON.setSetting('bypass_all_filters', 'false')
    if idx == 3:
        minimums = ('No filter', '480p+', '720p+', '1080p+', '4K only')
        minimum_labels = (
            _txt('بدون حد أدنى', 'No minimum'), '480p+', '720p+', '1080p+',
            _txt('4K فقط', '4K only'))
        current = _ADDON.getSetting('min_quality') or 'No filter'
        pre = minimums.index(current) if current in minimums else 0
        try:
            picked = xbmcgui.Dialog().select(
                _txt('الحد الأدنى للجودة', 'Minimum quality'),
                list(minimum_labels), preselect=pre)
        except TypeError:
            picked = xbmcgui.Dialog().select(
                _txt('الحد الأدنى للجودة', 'Minimum quality'),
                list(minimum_labels))
        if picked >= 0:
            _ADDON.setSetting('min_quality', minimums[picked])
        for key, ar, en, default in (
            ('hide_cam_ts', 'إخفاء CAM / TS', 'Hide CAM / TS', True),
            ('prefer_debrid', 'تفضيل المصادر المميزة / Debrid',
             'Prefer premium / Debrid sources', True),
            ('prefer_hdr', 'تفضيل HDR', 'Prefer HDR', True),
            ('prefer_atmos', 'تفضيل Atmos', 'Prefer Atmos', True),
        ):
            old = str(_ADDON.getSetting(key) or ('true' if default else 'false')).lower() in ('1', 'true', 'yes', 'on')
            choice = xbmcgui.Dialog().select(
                _txt(ar, en),
                [_txt('مفعّل', 'On'), _txt('متوقف', 'Off')],
                preselect=0 if old else 1)
            if choice >= 0:
                _ADDON.setSetting(key, 'true' if choice == 0 else 'false')

    codec_rows = [
        ('exclude_av1', 'AV1'), ('exclude_dv', 'Dolby Vision'),
        ('exclude_hevc', 'HEVC / H.265'),
    ]
    preselected = []
    for pos, (key, _label) in enumerate(codec_rows):
        if str(_ADDON.getSetting(key) or 'false').lower() in ('1', 'true', 'yes', 'on'):
            preselected.append(pos)
    selected = xbmcgui.Dialog().multiselect(
        _txt('استبعاد ترميزات غير مدعومة (اختياري)',
             'Exclude unsupported formats (optional)'),
        [label for _key, label in codec_rows], preselect=preselected)
    if selected is not None:
        selected = set(selected)
        for pos, (key, _label) in enumerate(codec_rows):
            _ADDON.setSetting(key, 'true' if pos in selected else 'false')
    _invalidate_settings()
    _notify(_txt('تم حفظ إعداد الجودة', 'Quality settings saved'))
    return True


def subtitles_dialog():
    _close_native_settings_dialog()
    while True:
        mode_values = ('Play only', 'Play with subtitles')
        mode_labels = (
            _txt('عند الطلب (موصى به)', 'On demand (recommended)'),
            _txt('ابحث وحمّل مع التشغيل', 'Search and load with playback'),
        )
        mode = _ADDON.getSetting('subtitle_search_mode') or mode_values[0]
        try:
            timeout = int(float(_ADDON.getSetting('subtitle_timeout') or '15'))
        except Exception:
            timeout = 15
        langs = (_ADDON.getSetting('preferred_subtitle_langs') or 'ar,en').strip()
        rows = [
            '%s:  [B]%s[/B]' % (
                _txt('الوضع', 'Mode'),
                mode_labels[mode_values.index(mode)]
                if mode in mode_values else mode_labels[0]),
            _txt('اللغات المفضلة: [B]%s[/B]' % (langs or 'ar,en'),
                 'Preferred languages: [B]%s[/B]' % (langs or 'ar,en')),
            _txt('مدة البحث: [B]%d ثانية[/B]' % timeout,
                 'Search time: [B]%d seconds[/B]' % timeout),
            '%s:  [B]%s[/B]' % (
                _txt('إضافات ترجمة Stremio', 'Stremio subtitle addons'),
                _txt('مفعّلة', 'On') if _enabled('enable_stremio_subtitle_broker', 'true')
                else _txt('متوقفة', 'Off')),
            '%s:  [B]%s[/B]' % (
                _txt('منع ترجمة AI التلقائية', 'Prevent automatic AI subtitles'),
                _txt('مفعّل', 'On') if _enabled('never_auto_ai_subtitles', 'true')
                else _txt('متوقف', 'Off')),
        ]
        choice = _select_dialog(_txt('الترجمة', 'Subtitles'), rows)
        if choice < 0:
            return True
        if choice == 0:
            pre = mode_values.index(mode) if mode in mode_values else 0
            picked = _select_dialog(_txt('وضع الترجمة', 'Subtitle mode'), mode_labels, pre)
            if picked >= 0:
                _ADDON.setSetting('subtitle_search_mode', mode_values[picked])
        elif choice == 1:
            presets = ('ar,en', 'ar', 'en,ar')
            labels = (
                _txt('العربية ثم الإنجليزية', 'Arabic, then English'),
                _txt('العربية فقط', 'Arabic only'),
                _txt('الإنجليزية ثم العربية', 'English, then Arabic'),
                _txt('مخصص…', 'Custom…'),
            )
            pre = presets.index(langs) if langs in presets else 3
            picked = _select_dialog(
                _txt('اللغات المفضلة', 'Preferred languages'), labels, pre)
            if 0 <= picked < len(presets):
                _ADDON.setSetting('preferred_subtitle_langs', presets[picked])
            elif picked == 3:
                keyboard = xbmc.Keyboard(
                    langs or 'ar,en',
                    _txt('رموز اللغات مفصولة بفاصلة',
                         'Comma-separated language codes'))
                keyboard.doModal()
                if keyboard.isConfirmed():
                    value = ','.join(
                        part.strip().lower() for part in
                        (keyboard.getText() or '').split(',') if part.strip())
                    if value:
                        _ADDON.setSetting('preferred_subtitle_langs', value)
        elif choice == 2:
            values = (10, 15, 20)
            labels = tuple(_txt('%d ثوانٍ' % value, '%d seconds' % value)
                           for value in values)
            pre = min(range(len(values)), key=lambda i: abs(values[i] - timeout))
            picked = _select_dialog(_txt('مدة البحث', 'Search time'), labels, pre)
            if picked >= 0:
                _ADDON.setSetting('subtitle_timeout', str(values[picked]))
        elif choice == 3:
            _toggle_setting('enable_stremio_subtitle_broker', 'true')
        elif choice == 4:
            _toggle_setting('never_auto_ai_subtitles', 'true')
        _invalidate_settings()


def fanarttv_dialog():
    """Small optional editor for network artwork enrichment."""
    _close_native_settings_dialog()
    while True:
        enabled = _enabled('fanarttv_enrich')
        key = (_ADDON.getSetting('fanarttv_api_key') or '').strip()
        rows = [
            '%s:  [B]%s[/B]' % (
                _txt('إثراء ClearLogo وBanner', 'ClearLogo and Banner enrichment'),
                _txt('مفعّل', 'On') if enabled else _txt('متوقف', 'Off')),
            '%s:  [B]%s[/B]' % (
                _txt('مفتاح Fanart.tv الشخصي', 'Personal Fanart.tv key'),
                _txt('محفوظ ✓', 'Saved ✓') if key else
                _txt('المفتاح العام المدمج', 'Bundled public key')),
            _txt('معلومة: الطلب يتم فقط عند فقدان الصورة، ثم يُحفظ في الكاش.',
                 'Info: a request runs only when artwork is missing, then it is cached.'),
        ]
        choice = _select_dialog(
            _txt('Fanart.tv وClearLogo', 'Fanart.tv & ClearLogo'), rows)
        if choice < 0:
            return True
        if choice == 0:
            if not enabled and not xbmcgui.Dialog().yesno(
                    'Dex Hub • Fanart.tv',
                    _txt('التفعيل قد يضيف طلبًا واحدًا عند فقدان الصور ثم يستخدم الكاش. متابعة؟',
                         'Enabling may add one request when artwork is missing, then uses cache. Continue?')):
                continue
            _toggle_setting('fanarttv_enrich')
        elif choice == 1:
            keyboard = xbmc.Keyboard(
                key, _txt('مفتاح Fanart.tv API (اختياري)',
                          'Fanart.tv API key (optional)'))
            from .. import kb_private
            with kb_private.private():      # v5.10.110: no keyboard suggestions for a key
                keyboard.doModal()
            if keyboard.isConfirmed():
                _ADDON.setSetting('fanarttv_api_key',
                                  (keyboard.getText() or '').strip())
        else:
            xbmcgui.Dialog().ok(
                _txt('Fanart.tv وClearLogo', 'Fanart.tv & ClearLogo'),
                _txt('هذا الخيار اختياري. يبقى متوقفًا للأجهزة الضعيفة، وعند تفعيله يجلب فقط الصور غير الموجودة ويحفظ النتيجة محليًا.',
                     'This is optional. Keep it off on slower devices; when enabled it fetches only missing artwork and stores the result locally.'))
        _invalidate_settings()


def tmdb_api_key_dialog():
    """Save the TMDb key through a fresh Kodi Addon handle, then verify it live.

    v5.4.38: the regular Kodi settings page now launches this route instead of
    editing tmdb_api_key inline. Kodi keeps an in-memory settings tree while
    addonsettings is open; a RunPlugin write made behind that window can be
    overwritten when the window later closes. Close that stale editor first,
    then write through a fresh Addon handle and verify from profile storage.
    """
    try:
        if xbmc.getCondVisibility('Window.IsVisible(addonsettings)'):
            xbmc.executebuiltin('Dialog.Close(addonsettings)')
            xbmc.sleep(180)
    except Exception:
        pass
    try:
        from resources.lib.live_settings import live_setting
        current = (live_setting('tmdb_api_key', default='') or '').strip()
    except Exception:
        current = (_ADDON.getSetting('tmdb_api_key') or '').strip()
    if not current:
        from ..tmdb_direct import DEFAULT_API_KEY
        current = DEFAULT_API_KEY
    kb = xbmc.Keyboard(current, _txt('مفتاح TMDb API', 'TMDb API Key'))
    from .. import kb_private
    with kb_private.private():      # v5.10.110: no keyboard suggestions for a key
        kb.doModal()
    if not kb.isConfirmed():
        return True
    value = (kb.getText() or '').strip()
    try:
        # A new instance is essential here; do not write through the possibly
        # stale module-level handle.
        fresh = xbmcaddon.Addon(_ADDON_ID)
        fresh.setSetting('tmdb_api_key', value)
        try:
            from resources.lib import settings_cache
            settings_cache.invalidate()
        except Exception:
            pass
        try:
            from resources.lib import live_settings
            live_settings._SETTINGS_FILE_MEMO['sig'] = None
            live_settings._SETTINGS_FILE_MEMO['values'] = {}
            saved = (live_settings.live_setting('tmdb_api_key', default='') or '').strip()
        except Exception:
            saved = (xbmcaddon.Addon(_ADDON_ID).getSetting('tmdb_api_key') or '').strip()
        if saved == value:
            _notify(_txt('تم حفظ مفتاح TMDb ✓', 'TMDb API key saved ✓') if value else
                    _txt('تم حذف مفتاح TMDb', 'TMDb API key removed'))
        else:
            xbmcgui.Dialog().ok('Dex Hub', _txt('تعذر تأكيد حفظ مفتاح TMDb. أعد المحاولة.',
                                                'Could not verify the TMDb API key save. Please try again.'))
    except Exception as exc:
        xbmcgui.Dialog().ok('Dex Hub', _txt('فشل حفظ مفتاح TMDb: %s' % exc,
                                            'Failed to save TMDb API key: %s' % exc))
    return True


def metadata_auto():
    # v5.10.68: one writer for the metadata source (meta_mode) so the legacy
    # labelenum and the global picker can never disagree.
    try:
        from resources.lib import meta_mode as _meta_mode
        _meta_mode.apply_source('auto')
    except Exception:
        try:
            _ADDON.setSetting('default_meta_source', 'Auto (smart)')
            _ADDON.setSetting('global_meta_source_id', '')
            _ADDON.setSetting('deep_meta_enrich', 'false')
        except Exception:
            pass
        _notify(_txt('الميتاداتا: تلقائي ✓', 'Metadata: Auto ✓'))
    return True


SPEED_PROFILE = {
    # lightness (unchanged from the old lightweight profile)
    'lightweight_mode': 'true', 'search_style': '1',
    'show_playback_waiter': 'false', 'pre_cache_next_episode': 'false',
    'streams_full_parallel_scan': 'false', 'deep_meta_enrich': 'false',
    'fanarttv_enrich': 'false', 'verbose_logging': 'false',
    'cloud_sync_continuous': 'false', 'large_section_limit': '24',
    # v5.10.69: search and source-scan speed
    'streams_quick_to_results': 'true',     # open the picker with the first source
    'streams_quick_open_seconds': '0.3',    # shortest post-first-result grace
    'streams_result_goal': '1',             # 4 unique rows are enough to open
    'streams_enough_wait_seconds': '3',     # foreground wait before partial results
    'search_ceiling_seconds': '4',          # search never waits longer than this
    'timeout': '12',                        # provider lifetime; UI/search caps stay separate
    'subtitle_timeout': '10',               # subtitle discovery budget
    'catalog_cache_ttl': '1800',            # catalog pages stay cached 30 min
    'http_gzip': 'true', 'parallel_workers': '4',
}


def _close_native_settings():
    # Kodi re-saves its in-memory settings tree when addonsettings closes,
    # reverting anything written behind it (see tmdb_api_key_dialog).
    try:
        if xbmc.getCondVisibility('Window.IsVisible(addonsettings)'):
            xbmc.executebuiltin('Dialog.Close(addonsettings)')
            xbmc.sleep(180)
    except Exception:
        pass


def stability_dialog():
    # v5.10.69: the lightweight profile is now the speed profile, reachable
    # from Maintenance. It deliberately does NOT touch the metadata source:
    # that is the unified picker's job (meta_mode), and the old
    # `default_meta_source` reset here contradicted it.
    _close_native_settings()
    if not xbmcgui.Dialog().yesno('Dex Hub', _txt('تطبيق وضع السرعة (بحث وتشغيل)؟', 'Apply the speed profile (search and playback)?')):
        return True
    addon = xbmcaddon.Addon()
    for k, v in SPEED_PROFILE.items():
        addon.setSetting(k, v)
    _invalidate_settings()
    _notify(_txt('تم تطبيق وضع السرعة ✓', 'Speed profile applied ✓'))
    return True


def diagnostics_dialog():
    def enabled(key, default='false'):
        try:
            value = _ADDON.getSetting(key) or default
        except Exception:
            value = default
        return str(value).strip().lower() in ('1', 'true', 'yes', 'on')

    try:
        tidb = bool(xbmc.getCondVisibility('System.HasAddon(plugin.video.tidb)'))
    except Exception:
        tidb = False
    lines = [
        _txt('الإصدار: %s', 'Version: %s') % (_ADDON.getAddonInfo('version') or '?'),
        _txt('الوضع الخفيف: %s', 'Lightweight mode: %s') % _onoff(enabled('lightweight_mode')),
        'BetterPosters: %s' % _onoff(enabled('betterposters_enabled', 'true')),
        'TIDB: %s' % (_txt('مثبت', 'Installed') if tidb else _txt('اختياري وغير مثبت', 'Optional, not installed')),
        _txt('نفس المصدر للحلقة التالية: %s', 'Next episode same source: %s') % _onoff(enabled('next_episode_same_source', 'true')),
        _txt('السجل المطوّل: %s', 'Verbose logging: %s') % _onoff(enabled('verbose_logging')),
    ]
    xbmcgui.Dialog().ok('Dex Hub', '\n'.join(lines))
    return True


def _onoff(value):
    return _txt('مفعّل', 'On') if value else _txt('متوقف', 'Off')


def open_native_settings():
    try:
        _ADDON.openSettings()
    except Exception:
        xbmc.executebuiltin('Addon.OpenSettings(%s)' % _ADDON_ID)
    return True


def open_page_from_native_settings(target):
    """Reliably leave Add-on Settings before opening a plugin directory."""
    allowed = {
        'nuvio_sync_menu', 'sync_accounts_menu', 'media_libraries_menu',
        'meta_sources_menu', 'config_backup_menu', 'collection_settings_hub',
        'selfhost_settings_menu', 'setup_connections', 'providers', 'home_live',
        'setup_center', 'simple_appearance', 'simple_metadata', 'favorites',
    }
    if target not in allowed:
        _notify(_txt('وجهة إعدادات غير صالحة', 'Invalid settings destination'))
        return True
    try:
        xbmc.executebuiltin('Dialog.Close(addonsettings)')
        xbmc.sleep(120)
        if target == 'home_live':
            # Dex Hub's own page, not a listing (v5.10.117)
            xbmc.executebuiltin('RunPlugin(%s)' % _url(action=target))
            return True
        xbmc.executebuiltin('ActivateWindow(Videos,%s,return)' % _url(action=target))
    except Exception:
        _notify(_txt('تعذر فتح القسم', 'Could not open this section'))
    return True


def dispatch():
    params = _params()
    action = str(params.get('action') or '').strip()
    if not action or action == 'home':
        return home()
    if action == 'home_classic':
        return home(classic=True)
    if action == 'setup_center':
        return settings_center()
    if action == 'simple_nuvio_collections':
        return nuvio_collections()
    if action in ('simple_sources_libraries', 'setup_connections'):
        return sources_libraries()
    if action == 'setup_playback_quality':
        return playback_quality()
    if action in ('simple_source_behavior', 'setup_playback'):
        return source_behavior_dialog()
    if action == 'simple_episode_controls':
        return episode_controls_dialog()
    if action in ('simple_tmdbh_install', 'tmdbh_install'):
        return tmdbh_install_action()
    if action == 'simple_watching':
        return watching_dialog()
    if action == 'simple_trakt':
        return trakt_dialog()
    if action == 'simple_appearance':
        return appearance()
    if action == 'simple_metadata':
        return metadata_settings()
    if action == 'simple_metadata_language':
        _close_native_settings_dialog()
        values = ('Auto', 'Arabic', 'English')
        current = _ADDON.getSetting('homeui_metadata_language') or 'Auto'
        pick = xbmcgui.Dialog().select(_txt('لغة الميتاداتا', 'Metadata language'),
            [_txt('تتبع مزود الميتاداتا', 'Follow metadata provider'), _txt('العربية', 'Arabic'), _txt('الإنجليزية', 'English')],
            preselect=values.index(current) if current in values else 0)
        if pick >= 0:
            _ADDON.setSetting('homeui_metadata_language', values[pick])
            _invalidate_settings()
        return True

    if action == 'simple_home_options':
        return home_options_dialog()
    if action == 'simple_favorites':
        return favorites_dialog()
    if action == 'favorite_source_menu':
        return favorite_lists(str(params.get('source') or ''), allow_direct=False)
    if action == 'favorites' and not params.get('source') and params.get('view') != 'merged' and (_ADDON.getSetting('favorites_layout') or 'separate') == 'separate':
        # _NOT_HANDLED (only Dex Hub's own list applies): the full router lists it
        return favorite_lists()
    if action == 'ui_options_publish':
        from resources.lib import ui_preferences, player_badges
        ui_preferences.publish()
        player_badges.publish_style()
        return True
    if action == 'ui_option':
        from resources.lib import ui_preferences
        return ui_preferences.edit(str(params.get('key') or ''), _txt)
    if action == 'player_appearance':
        from resources.lib import ui_preferences
        return ui_preferences.player_dialog(_txt)
    if action == 'player_badge_select':
        from resources.lib import player_badges
        return player_badges.select_style(str(params.get('style') or ''))
    if action == 'player_badge_preview':
        from resources.lib import player_badges
        return player_badges.preview()
    if action == 'player_badge_pack':
        from resources.lib import player_badges
        return player_badges.choose_pack(_txt)
    if action == 'player_badge_preset':
        # v5.10.141: a pack by name (Elite, Gold, Minimalist), its images kept
        from resources.lib import player_badges
        done = player_badges.use_preset(str(params.get('preset') or ''))
        player_badges.preview()
        return done
    if action == 'player_badge_folder':
        from resources.lib import player_badges
        done = player_badges.folder_menu(_txt)
        player_badges.preview()
        return done
    if action in ('player_badges', 'player_badges_publish'):
        from resources.lib import player_badges
        if action == 'player_badges_publish':
            return player_badges.publish()
        _close_native_settings_dialog()
        if xbmc.getSkinDir() == 'skin.dexhub':
            player_badges.preview()
            xbmc.executebuiltin('ActivateWindow(1194)')
            player_badges.warn_old_skin(always=True)
            return True
        return player_badges.show(_txt)

    if action in ('betterposters_settings', 'betterposters_settings_dialog'):
        from resources.lib.routes import betterposters_settings as _bp_settings
        return _bp_settings.show(_ADDON, _txt)
    if action == 'simple_open_page':
        return open_page_from_native_settings(str(params.get('target') or ''))
    if action == 'setup_performance':
        return maintenance()
    if action == 'simple_quick_setup':
        return quick_setup()
    if action == 'setup_quality':
        return quality_dialog()
    if action == 'setup_subtitles':
        return subtitles_dialog()
    if action == 'simple_tmdb_api_key':
        return tmdb_api_key_dialog()
    if action == 'simple_fanarttv':
        return fanarttv_dialog()
    if action == 'meta_cache_reload':
        from resources.lib import meta_mode as _meta_mode
        _meta_mode.clear_metadata_caches()
        _invalidate_settings()
        return True
    if action in ('meta_source_picker', 'meta_pick_global'):
        from resources.lib import meta_mode as _meta_mode
        _meta_mode.pick()
        _invalidate_settings()
        return True
    if action == 'simple_meta_auto':
        from resources.lib import meta_mode as _meta_mode
        _meta_mode.apply_source('auto')
        _invalidate_settings()
        return True
    if action == 'aiometadata_fast_mode':
        from resources.lib import meta_mode as _meta_mode
        _meta_mode.apply_aiometadata()
        _invalidate_settings()
        return True
    if action == 'setup_stability':
        return stability_dialog()
    if action == 'setup_diagnostics':
        return diagnostics_dialog()
    if action == 'open_settings':
        return open_native_settings()
    return _NOT_HANDLED
