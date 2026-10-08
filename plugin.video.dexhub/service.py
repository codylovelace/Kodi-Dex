# -*- coding: utf-8 -*-
import json
import os
import sys
import threading
import time
import xbmc

addon_path = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(addon_path, 'resources', 'lib')
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

import xbmcgui


# Trakt/Simkl and Nuvio/Stremio touch the same local playback state.  They
# must never run concurrently: apart from extra pressure, overlapping pulls
# can race over progress rows.  A non-blocking lock lets the later cycle defer
# cleanly instead of creating another waiting worker.
_SYNC_CYCLE_LOCK = threading.Lock()
# --- dexhub-402-patch ---
try:
    from resources.lib.i18n import tr as tr
except Exception:
    try:
        from i18n import tr as tr
    except Exception:
        def tr(_s):
            return _s



def _purge_http_cache():
    """Delete cached HTTP responses older than twice their max TTL. Keeps the
    cache dir bounded on long-running installs.

    Also purges:
      * special://temp/dexhub_subs/  → subtitle files older than 24h
      * addon_data/subtitle_cache/    → switched subtitle copies older than 7d
      * meta_cache.db expired rows
      * fanarttv_cache.db expired rows
      * special://temp/dexhub_trick/ → seek previews unused for 24h, or
        the oldest beyond 400 MB (Kodi-Dex)
    """
    import os as _os, time as _t
    try:
        from resources.lib.dexhub.client import HTTP_CACHE_DIR, catalog_ttl, meta_ttl
        max_ttl = max(catalog_ttl(), meta_ttl(), 3600) * 2
        if _os.path.isdir(HTTP_CACHE_DIR):
            now = _t.time()
            removed = 0
            for name in _os.listdir(HTTP_CACHE_DIR):
                path = _os.path.join(HTTP_CACHE_DIR, name)
                try:
                    if _os.path.isfile(path) and (now - _os.path.getmtime(path)) > max_ttl:
                        _os.remove(path)
                        removed += 1
                except Exception:
                    continue
            if removed:
                xbmc.log('[DexHub] purged %d stale http-cache files' % removed, xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] http-cache purge failed: %s' % exc, xbmc.LOGWARNING)

    # Kodi-Dex: seek preview copies (special://temp/dexhub_trick/)
    try:
        from resources.lib import seekthumbs
        removed = seekthumbs.purge()
        if removed:
            xbmc.log('[DexHub] purged %d seek preview folders' % removed, xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] seek preview purge failed: %s' % exc, xbmc.LOGWARNING)

    # Subtitle files dir — wasn't being touched in earlier versions.
    try:
        import xbmcvfs
        subs_dir = xbmcvfs.translatePath('special://temp/dexhub_subs/')
        if _os.path.isdir(subs_dir):
            now = _t.time()
            cutoff = now - 86400  # 24h
            removed = 0
            for root, dirs, files in _os.walk(subs_dir):
                for fn in files:
                    fp = _os.path.join(root, fn)
                    try:
                        if _os.path.getmtime(fp) < cutoff:
                            _os.remove(fp)
                            removed += 1
                    except Exception:
                        continue
                # Drop empty subdirs left behind
                try:
                    if root != subs_dir and not _os.listdir(root):
                        _os.rmdir(root)
                except Exception:
                    pass
            if removed:
                xbmc.log('[DexHub] purged %d stale subtitle files' % removed, xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] subs purge failed: %s' % exc, xbmc.LOGWARNING)

    # Stable copies used only to preserve external subtitles across source
    # switches. Keep them for a week, then remove them so the cache stays
    # bounded even on devices that are rarely restarted.
    try:
        import xbmcvfs
        switch_subs_dir = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/subtitle_cache/')
        if _os.path.isdir(switch_subs_dir):
            cutoff = _t.time() - (7 * 86400)
            removed = 0
            for fn in _os.listdir(switch_subs_dir):
                fp = _os.path.join(switch_subs_dir, fn)
                try:
                    if _os.path.isfile(fp) and _os.path.getmtime(fp) < cutoff:
                        _os.remove(fp)
                        removed += 1
                except Exception:
                    continue
            if removed:
                xbmc.log('[DexHub] purged %d stale switched-subtitle files' % removed, xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] switched-subs purge failed: %s' % exc, xbmc.LOGWARNING)

    # SQLite caches — drop expired rows
    try:
        from resources.lib import meta_cache as _mc
        n = _mc.purge_expired()
        if n:
            xbmc.log('[DexHub] purged %d expired meta_cache rows' % n, xbmc.LOGINFO)
    except Exception:
        pass
    try:
        from resources.lib import fanarttv as _ft
        n = _ft.purge_expired()
        if n:
            xbmc.log('[DexHub] purged %d expired fanarttv rows' % n, xbmc.LOGINFO)
    except Exception:
        pass


_WIN = []


def _win():
    # v5.10.140: one Home window object for the service (each new one takes
    # Kodi's GUI lock to be built; see skinui.common.home)
    try:
        if not _WIN:
            _WIN.append(xbmcgui.Window(10000))
        return _WIN[0]
    except Exception:
        return None


def _interactive_busy(max_age=180.0):
    """True while playback or a source/search interaction has priority."""
    try:
        if xbmc.Player().isPlayingVideo():
            return True
    except Exception:
        pass
    try:
        win = _win()
        raw = win.getProperty('dexhub.interactive_busy') if win else ''
        return bool(raw and (time.time() - float(raw)) < float(max_age))
    except Exception:
        return False



def _setting(key, default=''):
    try:
        import xbmcaddon
        return xbmcaddon.Addon().getSetting(key) or default
    except Exception:
        return default



def _primary_player_mode():
    raw = str(_setting('catalog_click_mode', 'TMDb Helper') or 'TMDb Helper').strip().lower()
    compact = raw.replace(' ', '').replace('_', '').replace('-', '')
    if compact in ('tmdbhelper', 'helper', '1') or 'tmdb' in compact:
        return 'tmdbhelper'
    if compact in ('ask', 'askeverytime', '2') or raw in ('اسأل كل مرة', 'السؤال كل مرة'):
        return 'ask'
    return 'dexhub'



# v5.10.120: settings Dex Hub writes for itself (account and server caches,
# sync times, one time defaults). Each write wakes onSettingsChanged; a box's
# log had the skin's 40 rows worked out again (5 s each time) after every
# Plex server cache write and every account sync, a few times a minute.
_VOLATILE_SETTINGS = frozenset((
    'welcome_seen', 'wizard_completed', 'metadata_generation', 'last_account_sync_at',
    'plex_client_identifier', 'plex_servers_cache_json', 'ui_lang_autodetected',
    'dexhub_defaults_rev', 'dexhub_v510_defaults_applied', 'dexhub_v520_defaults_applied',
    'dexhub_v5432_simple_applied', 'dexhub_v51090_timeout_applied', 'dexhub_v51088_speed_applied',
    'perf_defaults_39242', 'subtitle_defaults_39243', 'trailer_sound_default_104',
    'switch_source_keymap_data',
    # v5.10.131: a theme (kept in step with skin.dexhub's colour theme)
    'theme_preset',
))


def _volatile_setting(key):
    return key in _VOLATILE_SETTINGS or key.endswith('_auth_json') or key.endswith('_token')


def _settings_snapshot():
    """{id: value} of the add-on's saved settings (its settings.xml)."""
    import re
    try:
        import xbmcvfs
        path = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/settings.xml')
        with open(path, 'r', encoding='utf-8') as handle:
            text = handle.read()
    except Exception:
        return {}
    out = {}
    for match in re.finditer(r'<setting id="([^"]+)"[^>]*?(?:/>|>(.*?)</setting>)', text, re.S):
        out[match.group(1)] = match.group(2) or ''
    return out


def _publish_badge_props():
    """v4.7.5: bridge the badge settings to window properties.

    Reused plugin interpreters cannot see settings saved by the main Kodi
    process, so they read the badges URL they started with. This service
    runs in a process Kodi notifies (onSettingsChanged) and republishes
    the live values; window properties are global and always fresh."""
    win = _win()
    if not win:
        return
    try:
        url = (_setting('elite_badges_json_url', '') or '').strip()
        win.setProperty('dexhub.badges.url', url or '-')
        # v5.4.3: this fallback said 'false' while the setting itself
        # defaults to 'true'. A profile that had never written the key
        # got 'false' PUBLISHED to the window property — which the
        # badge code trusts ahead of the setting — so ticking the box
        # in settings changed nothing until Kodi restarted.
        enabled = (_setting('elite_badges_enabled', 'true') or 'true').strip().lower()
        win.setProperty('dexhub.badges.enabled', enabled)
        from resources.lib import player_badges
        player_badges.publish_style()
        from resources.lib import ui_preferences
        ui_preferences.publish()
    except Exception as exc:
        xbmc.log('[DexHub] badge props publish failed: %s' % exc, xbmc.LOGWARNING)


def _publish_core_props(last_sync=''):
    win = _win()
    if not win:
        return
    # Keep Kodi startup light: optional integrations are imported only when
    # this small status snapshot is actually published.
    try:
        from resources.lib import tmdbh_player
        tmdbh_available = '1' if tmdbh_player.has_tmdbhelper() else '0'
        tmdbh_installed = '1' if tmdbh_player.player_installed() else '0'
    except Exception:
        tmdbh_available = '0'
        tmdbh_installed = '0'
    tmdbh_primary = '1' if _primary_player_mode() == 'tmdbhelper' else '0'
    # v5.10.28: answer from settings instead of importing the Trakt module on
    # the service's main thread at boot; the module is only loaded once the
    # first background sync tick actually needs it.
    try:
        trakt_enabled = '1' if (_setting('enable_trakt', 'true') or 'true').strip().lower() == 'true' else '0'
        trakt_connected = '0'
        if trakt_enabled == '1':
            import json as _json
            import xbmcaddon as _xbmcaddon
            import xbmcvfs as _xbmcvfs
            _token_path = os.path.join(
                _xbmcvfs.translatePath(_xbmcaddon.Addon().getAddonInfo('profile')), 'trakt_token.json')
            if os.path.exists(_token_path):
                with open(_token_path, 'r', encoding='utf-8') as _fh:
                    _tok = _json.load(_fh) or {}
                trakt_connected = '1' if _tok.get('access_token') else '0'
    except Exception:
        trakt_enabled = '0'
        trakt_connected = '0'
    payload = {
        'dexhub.core.ready': '1',
        'dexhub.core.tmdbh.available': tmdbh_available,
        'dexhub.core.tmdbh.player_installed': tmdbh_installed,
        'dexhub.core.tmdbh.primary': tmdbh_primary,
        'dexhub.core.trakt.enabled': trakt_enabled,
        'dexhub.core.trakt.connected': trakt_connected,
        'dexhub.core.trakt.last_sync': str(last_sync or ''),
        'dexhub.core.formatter.enabled': '1' if ((_setting('enable_source_formatter', 'true') or 'true').lower() == 'true') else '0',
    }
    for k, v in payload.items():
        try:
            win.setProperty(k, v)
        except Exception:
            pass



def _invalidate_ui_caches():
    win = _win()
    if not win:
        return
    for key in ('dexhub.nextup_cache', 'dexhub.nextup_cache_ts', 'dexhub.fav_mirror_done'):
        try:
            win.clearProperty(key)
        except Exception:
            pass



def _sync_trakt_state(reason='manual'):
    # Trakt is optional and fairly heavy; do not import it during service
    # bootstrap on installations where it is never used.
    try:
        from resources.lib import trakt
    except Exception:
        _publish_core_props('')
        return False
    if not trakt.enabled():
        _publish_core_props('')
        return False
    try:
        if not trakt.authorized():
            _publish_core_props('')
            return False
    except Exception:
        _publish_core_props('')
        return False

    did_work = False
    try:
        if trakt.sync_enabled():
            trakt.import_progress(limit=100)
            did_work = True
    except Exception as exc:
        xbmc.log('[DexHub] trakt progress sync failed (%s): %s' % (reason, exc), xbmc.LOGWARNING)

    try:
        # v4.2.0: one merged snapshot (Trakt + Simkl + MDBList). Writing a
        # trakt-only snapshot here used to erase the other services' rows on
        # every service cycle.
        from resources.lib import favorites_store
        # v4.4.1: the merged mirror hits up to three external APIs; refreshing
        # it every service cycle is wasteful. 30-minute throttle via window
        # property (resets on Kodi restart) keeps sections fresh and light.
        import time as _t
        win = _win()
        last = 0.0
        try:
            last = float(win.getProperty('dexhub.mirror.last') or 0) if win else 0.0
        except Exception:
            last = 0.0
        if _t.time() - last >= 30 * 60:
            include_trakt = (_setting('trakt_sync_watchlist', 'true') or 'true').lower() == 'true'
            favorites_store.refresh_external_mirror(include_trakt=include_trakt)
            if win:
                win.setProperty('dexhub.mirror.last', str(int(_t.time())))
            did_work = True
    except Exception as exc:
        xbmc.log('[DexHub] watchlist mirror sync failed (%s): %s' % (reason, exc), xbmc.LOGWARNING)

    if did_work:
        try:
            trakt.invalidate_cache('next_up_v1')
            trakt.invalidate_cache('/sync/playback/')
        except Exception:
            pass
        _invalidate_ui_caches()
        _publish_core_props(str(int(__import__('time').time())))
    else:
        _publish_core_props('')
    return did_work



def _sync_simkl_state(reason='manual'):
    """Apply activity-gated history before rebuilding next-episode suggestions.

    The persistent baseline survives Kodi restarts. Unchanged cycles do not
    download full histories or repeat the local watched import.
    """
    if (_setting('simkl_service_sync', 'true') or 'true').lower() != 'true':
        return False
    try:
        from resources.lib import simkl
    except Exception:
        return False
    try:
        if not (simkl.enabled() and simkl.authorized()):
            return False
    except Exception:
        return False
    did_work = False
    try:
        movies, episodes = simkl.import_watched()
        did_work = bool(movies or episodes)
    except Exception as exc:
        xbmc.log('[DexHub] simkl watched import failed (%s): %s' % (reason, exc), xbmc.LOGWARNING)
    try:
        did_work = bool(simkl.sync_continue_watching(limit=60)) or did_work
    except Exception as exc:
        xbmc.log('[DexHub] simkl continue sync failed (%s): %s' % (reason, exc), xbmc.LOGWARNING)
    if did_work:
        _invalidate_ui_caches()
    return did_work


def _sync_interval_ms():
    try:
        minutes = int(_setting('trakt_service_sync_interval', '30') or '30')
    except Exception:
        minutes = 30
    # Keep the full set exposed by the simplified account screen.  Earlier
    # code silently collapsed the 120/240 minute choices back to 60 minutes.
    minutes = max(10, min(240, minutes))
    return minutes * 60 * 1000



def _background_sync_loop(monitor):
    # Do not race Kodi/TMDb Helper/database migrations during boot. Previous
    # builds launched this at 4s and a second Trakt startup sync at 5s, doing
    # the same account import twice while the home screen was still opening.
    if monitor.waitForAbort(60):
        return
    _last_health = time.time()
    _last_purge = time.time()
    while not monitor.abortRequested():
        deferred = _interactive_busy()
        acquired = False
        if not deferred:
            acquired = _SYNC_CYCLE_LOCK.acquire(False)
            deferred = not acquired
        if acquired:
            try:
                try:
                    _sync_trakt_state(reason='service')
                except Exception as exc:
                    xbmc.log('[DexHub] trakt background sync failed: %s' % exc, xbmc.LOGWARNING)
                try:
                    _sync_simkl_state(reason='service')
                except Exception as exc:
                    xbmc.log('[DexHub] simkl background sync failed: %s' % exc, xbmc.LOGWARNING)
            finally:
                _SYNC_CYCLE_LOCK.release()

        # Health check Plex/Emby endpoints every 5 minutes
        import time as _t
        now = _t.time()
        if not deferred and now - _last_health >= 300:
            try:
                from resources.lib import health_monitor
                checked = health_monitor.run_check_cycle()
                if checked:
                    xbmc.log('[DexHub] health checks: %d endpoints' % checked, xbmc.LOGDEBUG)
            except Exception as exc:
                xbmc.log('[DexHub] health check failed: %s' % exc, xbmc.LOGWARNING)
            _last_health = now

        # Cache cleanup every hour
        if not deferred and now - _last_purge >= 3600:
            try:
                _purge_http_cache()
            except Exception:
                pass
            _last_purge = now

        if not deferred:
            _publish_core_props(_win().getProperty('dexhub.core.trakt.last_sync') if _win() else '')
        # If the user is browsing/playing or the cloud cycle owns the lock,
        # retry gently in one minute. A completed cycle follows the normal
        # account interval (30 minutes by default).
        wait_seconds = 60.0 if deferred else (_sync_interval_ms() / 1000.0)
        if monitor.waitForAbort(wait_seconds):
            break


def _autodetect_language_first_run():
    """If the user hasn't picked a UI language yet, infer one from Kodi's
    locale on the very first start. Saves new users from seeing English when
    their Kodi UI is already Arabic (or vice versa). Runs once and writes a
    sentinel setting so subsequent starts don't override the user's choice.
    """
    try:
        import xbmcaddon
        addon = xbmcaddon.Addon()
        if (addon.getSetting('ui_lang_autodetected') or '').strip() == 'true':
            return
        # Read Kodi's UI language. xbmc.getLanguage gives English name; the
        # ISO 639-1 form is the most reliable signal.
        kodi_lang = (xbmc.getLanguage(xbmc.ISO_639_1) or '').strip().lower()
        if kodi_lang.startswith('ar'):
            addon.setSetting('ui_language', 'Arabic')
            addon.setSetting('preferred_subtitle_langs', 'ar,en')
        else:
            addon.setSetting('ui_language', 'English')
        addon.setSetting('ui_lang_autodetected', 'true')
        xbmc.log('[DexHub] auto-detected UI language from Kodi locale=%s' % kodi_lang,
                 xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] language auto-detect failed: %s' % exc, xbmc.LOGWARNING)


def _migrate_performance_defaults():
    """Move untouched legacy timeouts to the current fast defaults.

    Explicit user choices are preserved.  Kodi stores defaults as literal
    values, so the old 35/12 pair is a reliable signal that the user did not
    customise them.  A sentinel makes this a one-time operation.
    """
    try:
        import xbmcaddon
        addon = xbmcaddon.Addon()
        if (addon.getSetting('perf_defaults_39242') or '').strip() == '1':
            return
        ceiling = (addon.getSetting('search_ceiling_seconds') or '').strip()
        lookup = (addon.getSetting('server_lookup_seconds') or '').strip()
        if ceiling in ('', '35', '35.0'):
            addon.setSetting('search_ceiling_seconds', '4')
        if lookup in ('', '12', '12.0'):
            addon.setSetting('server_lookup_seconds', '5')
        addon.setSetting('perf_defaults_39242', '1')
        xbmc.log('[DexHub] migrated untouched search defaults to 4s/5s', xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] performance-default migration failed: %s' % exc,
                 xbmc.LOGDEBUG)


def _migrate_silo_always_on():
    """v5.10.17: a linked Silo profile is always a playback source."""
    try:
        addon = xbmcaddon.Addon()
        raw = addon.getSetting('silo_auth_json') or '{}'
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = {}
        if data.get('url') and data.get('token') and data.get('profile_id'):
            addon.setSetting('silo_in_sources', 'true')
    except Exception:
        pass


def _migrate_subtitle_broker_defaults():
    """Enable Stremio subtitle discovery once for existing installations.

    v3.9.243 separates subtitle discovery from auto-showing subtitles.  The
    broker may search all installed Stremio subtitle addons while Play only
    remains selected, so enabling discovery no longer forces a subtitle on.
    Users can still turn the broker off afterwards.
    """
    try:
        import xbmcaddon
        addon = xbmcaddon.Addon()
        if (addon.getSetting('subtitle_defaults_39243') or '').strip() == '1':
            return
        addon.setSetting('enable_stremio_subtitle_broker', 'true')
        addon.setSetting('subtitle_defaults_39243', '1')
        xbmc.log('[DexHub] enabled parallel Stremio subtitle discovery', xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] subtitle-default migration failed: %s' % exc, xbmc.LOGDEBUG)


def _migrate_v510_light_defaults():
    """One-time production profile requested for speed and stability.

    The settings remain readable for backward compatibility, but costly
    experimental features are disabled and no longer exposed in the normal
    settings UI.  Account links, provider lists, quality choices and user data
    are untouched.
    """
    try:
        import xbmcaddon
        addon = xbmcaddon.Addon()
        if (addon.getSetting('dexhub_v510_defaults_applied') or '').strip().lower() == 'true':
            return
        try:
            revision = int((addon.getSetting('dexhub_defaults_rev') or '0').strip())
        except Exception:
            revision = 0
        if revision >= 510:
            addon.setSetting('dexhub_v510_defaults_applied', 'true')
            return
        # v5.4.1: this migration force-wrote every value below into existing
        # installs — including two the user had deliberately turned ON:
        # image badges (after several sessions spent getting community badge
        # sets working) and continuous sync. Silently reversing a choice the
        # user made is the same fault the clean_catalog_view migration had.
        # Performance defaults still apply to installs that never touched
        # them; anything the user has expressed an opinion on is left alone.
        values = {
            'lightweight_mode': 'true',
            'pre_cache_next_episode': 'false',
            'deep_meta_enrich': 'false',
            'fanarttv_enrich': 'false',
            'streams_full_parallel_scan': 'false',
            'show_playback_waiter': 'false',
            'safe_playback_handoff': 'true',
            'kodi22_minimal_item': 'true',
            'tmdbh_auto_play_first': 'false',
            'parallel_workers': '4',
            'trakt_service_sync_interval': '30',
            'http_gzip': 'true',
        }
        # Features the user opts into keep whatever they already are.
        for key, value in values.items():
            addon.setSetting(key, value)
        addon.setSetting('dexhub_v510_defaults_applied', 'true')
        xbmc.log('[DexHub] applied v5.1 light production defaults', xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] v5.1 defaults migration failed: %s' % exc,
                 xbmc.LOGDEBUG)


def _migrate_v520_search_defaults():
    """Move untouched v5.1 timing values to the v5.2 source/subtitle policy."""
    try:
        import xbmcaddon
        addon = xbmcaddon.Addon()
        if (addon.getSetting('dexhub_v520_defaults_applied') or '').strip().lower() == 'true':
            return
        try:
            revision = int((addon.getSetting('dexhub_defaults_rev') or '0').strip())
        except Exception:
            revision = 0
        if revision >= 520:
            addon.setSetting('dexhub_v520_defaults_applied', 'true')
            return
        subtitle = (addon.getSetting('subtitle_timeout') or '').strip()
        # Every old supported value is outside the new safe range. Move it to
        # the requested midpoint; values already in 10..20 are user choices.
        try:
            subtitle_value = int(float(subtitle)) if subtitle else 0
        except Exception:
            subtitle_value = 0
        if subtitle_value < 10 or subtitle_value > 20:
            addon.setSetting('subtitle_timeout', '15')
        quick = (addon.getSetting('streams_quick_open_seconds') or '').strip()
        patient = (addon.getSetting('streams_enough_wait_seconds') or '').strip()
        if quick in ('', '0.8', '0.80'):
            addon.setSetting('streams_quick_open_seconds', '0.4')
        if patient in ('', '5', '5.0'):
            addon.setSetting('streams_enough_wait_seconds', '8')
        # This is a permanent safe default (see apply_clean_defaults_once).
        addon.setSetting('clean_catalog_view', 'false')
        addon.setSetting('dexhub_v520_defaults_applied', 'true')
        xbmc.log('[DexHub] applied v5.2 source/subtitle timing defaults', xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] v5.2 defaults migration failed: %s' % exc,
                 xbmc.LOGDEBUG)


def _migrate_v5432_simple_defaults():
    """One-time production baseline focused on UI responsiveness.

    Only performance/complexity knobs are touched. Accounts, active
    Collection, providers, badge preset, playback choice and subtitle mode
    remain the user's.
    """
    try:
        import xbmcaddon
        addon = xbmcaddon.Addon()
        if (addon.getSetting('dexhub_v5432_simple_applied') or '').strip().lower() == 'true':
            return
        values = {
            'lightweight_mode': 'true',
            'search_style': '1',
            'show_playback_waiter': 'false',
            'pre_cache_next_episode': 'false',
            'streams_full_parallel_scan': 'false',
            'streams_quick_to_results': 'true',
            'kodi22_minimal_item': 'true',
            'deep_meta_enrich': 'false',
            'fanarttv_enrich': 'false',
            'verbose_logging': 'false',
            'cloud_sync_continuous': 'false',
            'large_section_limit': '24',
            'default_meta_source': 'Auto (smart)',
        }
        for key, value in values.items():
            addon.setSetting(key, value)
        addon.setSetting('dexhub_v5432_simple_applied', 'true')
        xbmc.log('[DexHub] applied v5.4.32 simple/responsive baseline', xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] v5.4.32 baseline migration failed: %s' % exc, xbmc.LOGDEBUG)


def _migrate_v51088_speed_defaults():
    """Align untouched hidden timing defaults with the current Speed profile.

    Older upgrades can still carry the historical 12s search ceiling / 8s
    source foreground wait even though the current quick setup uses 4s / 3s.
    These settings are hidden in the normal UI, so migrate only exact values
    shipped by earlier releases and preserve anything else as a user choice.
    """
    try:
        import xbmcaddon
        addon = xbmcaddon.Addon()
        if (addon.getSetting('dexhub_v51088_speed_applied') or '').strip().lower() == 'true':
            return

        known = {
            'search_ceiling_seconds': ({'', '6', '6.0', '12', '12.0'}, '4'),
            'timeout': ({'', '10', '10.0'}, '8'),
            'server_lookup_seconds': ({'', '7', '7.0'}, '5'),
            'streams_quick_open_seconds': ({'', '0.4', '0.40'}, '0.3'),
            'streams_enough_wait_seconds': ({'', '5', '5.0', '8', '8.0'}, '3'),
            'catalog_cache_ttl': ({'', '600', '600.0'}, '1800'),
        }
        changed = []
        for key, (legacy_values, new_value) in known.items():
            current = (addon.getSetting(key) or '').strip()
            if current in legacy_values:
                addon.setSetting(key, new_value)
                changed.append('%s=%s' % (key, new_value))
        addon.setSetting('dexhub_v51088_speed_applied', 'true')
        if changed:
            xbmc.log('[DexHub] v5.10.88 speed migration: %s' % ', '.join(changed), xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] v5.10.88 speed migration failed: %s' % exc, xbmc.LOGDEBUG)


def _migrate_v51090_timeout_default():
    """Restore the provider request lifetime without slowing UI budgets.

    v5.10.88 migrated untouched installs to an 8s generic timeout. Logs from
    real providers showed useful stream responses beyond that point, while
    the picker/search foreground budgets already keep navigation responsive.
    Only the exact hidden 8s value is changed; any other user value survives.
    """
    try:
        import xbmcaddon
        addon = xbmcaddon.Addon()
        if (addon.getSetting('dexhub_v51090_timeout_applied') or '').strip().lower() == 'true':
            return
        current = (addon.getSetting('timeout') or '').strip()
        if current in ('', '8', '8.0'):
            addon.setSetting('timeout', '12')
            xbmc.log('[DexHub] v5.10.90 provider timeout migration: timeout=12', xbmc.LOGINFO)
        addon.setSetting('dexhub_v51090_timeout_applied', 'true')
    except Exception as exc:
        xbmc.log('[DexHub] v5.10.90 timeout migration failed: %s' % exc, xbmc.LOGDEBUG)


def _migrate_v510104_trailer_sound():
    """v5.10.104: Home trailers are heard by default, once for every install.

    Kodi keeps an add-on setting's stored value when the add-on's default
    changes, so the old muted default would stay. An untouched value cannot
    be told from one set to off on purpose (both equal the old default, and
    the first save under the new default drops Kodi's default="true" mark),
    so every install is switched on once; the setting still turns it off.
    """
    try:
        import xbmcaddon
        addon = xbmcaddon.Addon()
        if (addon.getSetting('trailer_sound_default_104') or '').strip() == '1':
            return
        if (addon.getSetting('homeui_trailer_sound') or '').strip().lower() != 'true':
            addon.setSetting('homeui_trailer_sound', 'true')
            xbmc.log('[DexHub] v5.10.104: Home trailers now play with sound', xbmc.LOGINFO)
        addon.setSetting('trailer_sound_default_104', '1')
    except Exception as exc:
        xbmc.log('[DexHub] v5.10.104 trailer sound migration failed: %s' % exc, xbmc.LOGDEBUG)


def _stop_threads(budget=2.0):
    """v5.10.130: Kodi lets the service go only once every thread of its
    interpreter has ended (daemon threads too): "waiting on thread", then
    "script didn't stop in 5 seconds - let's kill it", and in the test
    harness Kodi hung after that kill instead of quitting. The threads that
    never ended were idle pool workers (the rows' network lanes), which wait
    on their queue for ever. Every executor is told to stop (an idle worker
    wakes and leaves, a busy one leaves after its task) and the threads still
    alive get a short while to finish: the loops leave on Kodi's abort."""
    import threading
    import time
    started = time.monotonic()
    try:
        from resources.lib.runtime_cleanup import shutdown_loaded_pools
        shutdown_loaded_pools()
    except Exception:
        pass
    try:
        # every other executor of this interpreter (continue watching,
        # subtitles, sync): what Python does at its own exit, without the
        # join that would wait for a slow server
        import concurrent.futures.thread as _cft
        _cft._shutdown = True
        lock = getattr(_cft, '_global_shutdown_lock', None)
        if lock is not None:
            with lock:
                queues = list(_cft._threads_queues.items())
        else:
            queues = list(_cft._threads_queues.items())
        for _thread, work_queue in queues:
            try:
                work_queue.put(None)
            except Exception:
                pass
    except Exception:
        pass
    deadline = started + budget
    me = threading.current_thread()
    # Kodi's own threads: the process main thread (Python names it
    # MainThread in every interpreter) and the foreign threads Python only
    # knows as dummies never end here, so waiting on them only costs time
    skip = {me, threading.main_thread()}
    dummy = getattr(threading, '_DummyThread', None)
    left_alive = []
    for thread in threading.enumerate():
        if thread in skip or (dummy is not None and isinstance(thread, dummy)):
            continue
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                thread.join(remaining)
            except Exception:
                pass
        if thread.is_alive():
            left_alive.append(thread.name)
    try:
        xbmc.log('[DexHub] service stopped in %.0f ms%s' % (
            (time.monotonic() - started) * 1000,
            ('; still running: %s' % ', '.join(left_alive[:8])) if left_alive else ''), xbmc.LOGINFO)
    except Exception:
        pass


if __name__ == '__main__':
    try:
        import xbmcaddon
        _service_addon = xbmcaddon.Addon()
        xbmc.log('[DexHub] companion service started: version=%s skin=%s' % (
            _service_addon.getAddonInfo('version') or '?',
            xbmc.getSkinDir() or '?'), xbmc.LOGINFO)
    except Exception:
        xbmc.log('[DexHub] companion service started', xbmc.LOGINFO)
    # v3.9.71: log Kodi version on startup so platform-specific issues
    # (e.g. deprecated API native crashes on Kodi 22 alpha) are easy to
    # correlate with bug reports.
    try:
        xbmc.log('[DexHub] platform: Kodi %s, Python %s' % (xbmc.getInfoLabel('System.BuildVersion') or '?', sys.version.split()[0]),
                 xbmc.LOGINFO)
    except Exception:
        pass

    # v5.10.140: first, before any other write rewrites the settings file,
    # list choices saved as their shown label become their value again.
    try:
        from resources.lib import settings_migrate as _settings_migrate
        _settings_migrate.run()
    except Exception:
        pass
    _autodetect_language_first_run()
    _migrate_performance_defaults()
    _migrate_silo_always_on()
    _migrate_subtitle_broker_defaults()
    _migrate_v510_light_defaults()
    _migrate_v520_search_defaults()
    _migrate_v5432_simple_defaults()
    _migrate_v51088_speed_defaults()
    _migrate_v51090_timeout_default()
    _migrate_v510104_trailer_sound()

    # Publish the skin-aware theme palette early so every DexHub dialog
    # (sources, loading, wait, select) inherits the active skin's accent
    # the moment it opens, regardless of open order.
    try:
        from resources.lib import skin_theme
        skin_theme.publish_theme(log=lambda m: xbmc.log('[DexHub] %s' % m, xbmc.LOGINFO))
    except Exception as exc:
        xbmc.log('[DexHub] skin_theme publish failed: %s' % exc, xbmc.LOGWARNING)

    # v5.10.96: a Dex Hub Home trailer that was muted when Kodi stopped (crash,
    # power loss) must not leave the whole system muted.
    try:
        import xbmcaddon as _xbmcaddon
        import xbmcvfs as _xbmcvfs
        from resources.lib.homeui.director import restore_mute_marker
        restore_mute_marker(_xbmcvfs.translatePath(
            _xbmcaddon.Addon().getAddonInfo('profile')))
    except Exception:
        pass
    # v5.10.103: likewise Kodi's "switch to full screen" setting for TV
    # channels, held at "never" while a Live TV preview starts.
    try:
        import xbmcaddon as _xbmcaddon
        import xbmcvfs as _xbmcvfs
        from resources.lib.homeui.live import restore_marker as _live_restore
        _live_restore(_xbmcvfs.translatePath(_xbmcaddon.Addon().getAddonInfo('profile')))
    except Exception:
        pass

    # v5.10.98: open the Dex Hub Home when Kodi starts (setting homeui_autostart).
    # Only at Kodi's own start: a service restart (add-on update) in the same
    # session never opens it in the middle of whatever the user is doing.
    try:
        from resources.lib.homeui import autostart as _autostart
        _first_start = _autostart.first_run_this_session()
        if _first_start:
            # a player keymap the Home left behind when Kodi stopped with it open
            from resources.lib.homeui import keymap as _home_keymap
            _home_keymap.reset()
        if _first_start and (_setting('homeui_autostart', 'false') or 'false').strip().lower() in (
                'true', '1', 'yes', 'on'):
            import threading as _thr_auto
            _thr_auto.Thread(target=_autostart.launch_when_ready,
                             name='DexHub-homeui-autostart', daemon=True).start()
    except Exception as exc:
        xbmc.log('[DexHub] Home autostart not started: %s' % exc, xbmc.LOGWARNING)

    _publish_core_props('')
    _publish_badge_props()
    # v5.10.142: a pack's or the folder's badges need skin.dexhub 3.19 (an
    # older skin draws them on plates); said once, after the Home is up
    try:
        import threading as _thr_badges
        from resources.lib import player_badges as _player_badges
        _badge_check = _thr_badges.Timer(25.0, _player_badges.warn_old_skin)
        _badge_check.daemon = True
        _badge_check.start()
    except Exception:
        pass
    # v5.10.78: tell the context menu which play mode is the default, so it
    # can offer the opposite one.
    try:
        from resources.lib.context_play import publish as _publish_play_mode
        _publish_play_mode()
    except Exception:
        pass

    # v3.9.24: launch the local poster proxy. Inspired by Plexio's
    # /proxy/{token} pattern, this gives us a single-URL handle to every
    # poster image that transparently falls back from decorated → clean
    # if the upstream decoration service is slow/dead. Critically it
    # works on skins that don't honour Kodi's poster→thumb fallback
    # chain (Estuary, Confluence, much of the community-skin field).
    _lightweight = (_setting('lightweight_mode', 'true') or 'true').strip().lower() in ('true', '1', 'yes', 'on')
    if not _lightweight:
        try:
            from resources.lib import poster_proxy as _poster_proxy
            _poster_proxy.start()
        except Exception as exc:
            xbmc.log('[DexHub] poster-proxy not started: %s' % exc, xbmc.LOGWARNING)
    else:
        xbmc.log('[DexHub] Lightweight Mode — poster proxy skipped', xbmc.LOGINFO)

    # v3.9.27: launch the library-index sync scheduler. Runs the first
    # sync 30s after Kodi boot, then every `index_sync_interval_hours`.
    # Activated only when the user has enabled hybrid/fast mode — in
    # 'live' mode the sync still runs to keep the index warm in case
    # the user toggles modes later, but we skip the first-boot sync to
    # avoid wasting bandwidth on someone who isn't using the feature.
    #
    # v3.9.37: Lightweight Mode disables the index scheduler entirely.
    # The user opted out of aggregated buckets, so there is nothing to
    # index — running the scheduler would only waste CPU and bandwidth.
    if _lightweight:
        xbmc.log('[DexHub] Lightweight Mode enabled — index scheduler skipped', xbmc.LOGINFO)
    else:
        try:
            from resources.lib import index_render as _idx_render
            from resources.lib.dexhub import sync_engine as _sync_eng
            _idx_db = _idx_render.get_db()

            def _pinned_provider():
                # Late-import plugin (heavy module) only when actually needed.
                try:
                    from resources.lib import plugin as _plg
                    return _plg._hub_catalog_entries(bucket=None) or []
                except Exception as exc:
                    xbmc.log('[DexHub] sync pinned-provider failed: %s' % exc,
                             xbmc.LOGWARNING)
                    return []

            # v5.4.13: background sync is deliberately silent. Kodi notifications
            # interrupt playback and stack up on TV boxes; diagnostics belong in
            # kodi.log while explicit user-triggered actions still show feedback.
            def _on_sync_progress(stage, info):
                try:
                    if stage == 'start':
                        xbmc.log('[DexHub] library sync started (%s catalogs)' % (info.get('total') or 0), xbmc.LOGDEBUG)
                    elif stage == 'done':
                        xbmc.log('[DexHub] library sync done: %s ok in %.1fs' % (info.get('ok') or 0, float(info.get('duration') or 0)), xbmc.LOGDEBUG)
                except Exception:
                    pass

            _idx_engine = _sync_eng.SyncEngine(
                _idx_db,
                pinned_entries_provider=_pinned_provider,
                on_progress=_on_sync_progress,
            )

            def _interval_hours():
                try:
                    raw = _setting('index_sync_interval_hours', '4') or '4'
                    return int(float(raw))
                except Exception:
                    return 4

            import threading as _thr
            _thr.Thread(
                target=_sync_eng.run_scheduler,
                args=(_idx_engine, xbmc.Monitor()),
                kwargs={'get_interval_hours': _interval_hours},
                name='DexHub-index-scheduler', daemon=True,
            ).start()
            xbmc.log('[DexHub] library index scheduler started', xbmc.LOGINFO)
        except Exception as exc:
            xbmc.log('[DexHub] index scheduler not started: %s' % exc, xbmc.LOGWARNING)

    # Playback monitor is the largest service dependency; load it only after
    # lightweight startup work and optional schedulers are ready.
    from resources.lib.companion import CompanionPlayer, ProgressLoop
    player = CompanionPlayer()

    class _DexHubMonitor(xbmc.Monitor):
        """v4.7.3: closes the badges feedback loop at the SETTINGS DIALOG.
        'I changed badges.json and nothing changed' had no on-change hook —
        the new rules were only ever attempted on the next picker open, and
        a failure looked identical to success. Now the URL change triggers
        an immediate background fetch whose success/failure toast fires
        right away (the once-guards reset with the memo)."""

        def __init__(self):
            super().__init__()
            self._elite_url = _setting('elite_badges_json_url', '')
            self._settings_snap = _settings_snapshot()

        def _changed_settings(self):
            """The settings the user changed since the last call (the ones
            Dex Hub keeps for itself left out); None when unknown."""
            snap = _settings_snapshot()
            if not snap:
                return None
            old, self._settings_snap = self._settings_snap, snap
            if not old:
                return None
            keys = set(old) | set(snap)
            return sorted(k for k in keys if old.get(k) != snap.get(k) and not _volatile_setting(k))

        def onNotification(self, sender, method, data):
            # Kodi-Dex: skin.dexhub's seek timer (Timers.xml) announces a seek
            # with NotifyAll; everything else is ignored after one compare.
            if sender == 'plugin.video.dexhub' and 'dexhub_seek_' in method:
                try:
                    from resources.lib import seekthumbs
                    seekthumbs.on_notification(sender, method, data)
                except Exception as exc:
                    xbmc.log('[DexHub] seekthumb: notification failed: %s' % exc, xbmc.LOGWARNING)

        def onSettingsChanged(self):
            try:
                from resources.lib import settings_cache as _sc
                _sc.invalidate()
            except Exception:
                pass
            _publish_badge_props()
            try:
                from resources.lib.context_play import publish as _publish_play_mode
                _publish_play_mode()
            except Exception:
                pass
            changed = self._changed_settings()
            if changed == []:
                return          # only Dex Hub's own bookkeeping was written
            if changed and set(changed).intersection({'metadata_badges', 'player_show_ratings', 'player_show_studio'}):
                try:
                    from resources.lib import player_presentation
                    player_presentation.refresh_current()
                except Exception:
                    pass
            from resources.lib.ui_preferences import DISPLAY_ONLY
            if changed and set(changed).issubset(DISPLAY_ONLY):
                return          # display-only choices do not rebuild Home/widgets
            if changed:
                xbmc.log('[DexHub] settings changed: %s' % ', '.join(changed[:8]), xbmc.LOGDEBUG)
            try:
                from resources.lib import widget_cache as _widget_cache
                _widget_cache.clear()
            except Exception:
                pass
            try:
                if xbmc.getSkinDir() == 'skin.dexhub':
                    from resources.lib.skinui import common as _skin_common
                    _skin_common.ask_republish('settings')
            except Exception:
                pass
            new_url = (_setting('elite_badges_json_url', '') or '').strip()
            if new_url == self._elite_url:
                return
            self._elite_url = new_url
            xbmc.log('[DexHub] elite badges URL changed — reloading rules', xbmc.LOGINFO)

            def _job():
                try:
                    from resources.lib import source_browser as _sb
                    _sb._ELITE_BADGE_RULE_CACHE.clear()
                    _sb._elite_badge_rules()
                except Exception as exc:
                    xbmc.log('[DexHub] elite reload after settings change failed: %s'
                             % exc, xbmc.LOGWARNING)
            import threading as _thr
            _thr.Thread(target=_job, name='DexHubEliteReload', daemon=True).start()

    monitor = _DexHubMonitor()

    SYNC_DIRTY_PROP = 'dexhub.sync_dirty'

    def _cloud_sync_loop(mon):
        """Native Nuvio/Stremio sync.

        v5.1 adaptive mode: local writes are debounced, cloud pulls use a
        minimum 30-minute cadence, and all network/database work is deferred
        while playback or an interactive source/search screen is active.
        Pulled account rows use a bulk transaction and do not raise the dirty
        flag, eliminating the old sync feedback loop.
        """
        if mon.waitForAbort(30):
            return
        from resources.lib.dexhub import nuvio_stremio_sync as sync
        win = _win()
        last_pull = time.time()  # never force a full pull during Kodi boot
        dirty_since = None
        next_retry = 0.0

        while not mon.abortRequested():
            try:
                interval = int(float(_setting('cloud_sync_interval_min', '30') or '30'))
            except Exception:
                interval = 30

            if interval <= 0:                     # user switched sync off
                if mon.waitForAbort(60):
                    break
                continue

            dirty = False
            try:
                dirty = bool(win and win.getProperty(SYNC_DIRTY_PROP))
            except Exception:
                dirty = False
            now = time.time()
            if dirty and dirty_since is None:
                dirty_since = now
            elif not dirty:
                dirty_since = None
            # v5.4.1: continuous mode is honoured again. 5.4.0 kept the
            # sturdier loop (cycle lock, retry backoff, no sync while the
            # user is interacting) but hardcoded a 45s debounce and a 30
            # minute floor on pulls, so the cloud_sync_continuous setting
            # did nothing at all. Continuous debounces 8s and pulls on the
            # user's interval; periodic keeps the conservative numbers.
            continuous = (_setting('cloud_sync_continuous', 'false') or 'false'
                          ).strip().lower() in ('true', '1', 'yes', 'on')
            debounce = 8.0 if continuous else 45.0
            pull_floor = max(120, interval * 60) if continuous else max(1800, interval * 60)
            dirty_due = bool(dirty and dirty_since is not None and
                             (now - dirty_since) >= debounce)
            pull_due = (now - last_pull) >= pull_floor
            # v5.10.120: a profile switch whose data Nuvio did not give
            # (homeui/profile.pull) is read in full at the next chance
            want_full = False
            try:
                want_full = bool(win and win.getProperty('dexhub.sync.want_full'))
            except Exception:
                want_full = False
            pull_due = pull_due or want_full

            if ((dirty_due or pull_due) and now >= next_retry and
                    not _interactive_busy() and sync.enabled_targets()):
                acquired = _SYNC_CYCLE_LOCK.acquire(False)
                if not acquired:
                    if mon.waitForAbort(30):
                        break
                    continue
                try:
                    if win:
                        win.clearProperty(SYNC_DIRTY_PROP)
                except Exception:
                    pass
                light = dirty_due and not pull_due
                try:
                    # Each service resolves its own direction and sections.
                    # v5.10.120: a local change (progress, the library) is
                    # sent on its own; the add-ons, collections and Home
                    # settings wait for the scheduled pull. A full sync after
                    # every playback asked Nuvio a dozen things each time,
                    # and Nuvio answered 429 (sync paused 10 minutes, the
                    # profile list unreadable meanwhile).
                    if light:
                        result = sync.run_sync(only=('progress', 'library'))
                    else:
                        result = sync.run_sync()
                    if result and result.get('ok'):
                        if not light:
                            last_pull = time.time()
                            if want_full and win:
                                win.clearProperty('dexhub.sync.want_full')
                        dirty_since = None
                        next_retry = 0.0
                    else:
                        next_retry = time.time() + 300.0
                        if dirty and win:
                            win.setProperty(SYNC_DIRTY_PROP, '1')
                except Exception as exc:
                    next_retry = time.time() + 300.0
                    if dirty and win:
                        try:
                            win.setProperty(SYNC_DIRTY_PROP, '1')
                        except Exception:
                            pass
                    xbmc.log('[DexHub] cloud sync failed: %s' % exc, xbmc.LOGDEBUG)
                finally:
                    _SYNC_CYCLE_LOCK.release()

            # Continuous mode has to wake often enough for its 8s debounce
            # to be real; periodic keeps the cheap 30s tick.
            if mon.waitForAbort(12 if continuous else 30):
                break


    def _tmdbh_player_maintenance():
        # Player-file maintenance is not part of Kodi startup.  Delay it until
        # the user has had time to enter the skin and skip the pass entirely
        # while Dex Hub is actively browsing/searching.
        try:
            if monitor.waitForAbort(75):
                return
            if _interactive_busy(max_age=120.0):
                return
            from resources.lib import tmdbh_player
            tmdbh_player.ensure_installed_once()
        except Exception as exc:
            xbmc.log('[DexHub] tmdbh player maintenance skipped: %s' % exc, xbmc.LOGDEBUG)


    def _bytecode_warm_job():
        # v5.4.7: pre-compile the addon tree to __pycache__ so the user's
        # first click after install/update pays warm-import cost only
        # (~0.5s ARM32) instead of full byte-compilation (~4-6s ARM32).
        # 120s boot delay keeps us out of Kodi's startup and first-browse CPU window;
        # after the first pass this is a stat-only sweep in milliseconds.
        try:
            if monitor.waitForAbort(120):
                return
            if _interactive_busy(max_age=240.0):
                return
            from bytecode_warm import warm
            addon_root = os.path.dirname(os.path.abspath(__file__))
            checked, compiled = warm(addon_root, monitor=monitor)
            if compiled:
                xbmc.log('[DexHub] bytecode warm: compiled %d/%d files'
                         % (compiled, checked), xbmc.LOGDEBUG)
        except Exception as exc:
            xbmc.log('[DexHub] bytecode warm skipped: %s' % exc, xbmc.LOGDEBUG)

    # v5.10.109: skin.dexhub's rows (published and kept fresh here while that
    # skin is in use; the thread waits quietly under any other skin)
    def _skin_worker():
        try:
            from resources.lib.skinui import worker as _skin_worker_mod
            _skin_worker_mod.run(monitor)
        except Exception as exc:
            xbmc.log('[DexHub] skin worker not started: %s' % exc, xbmc.LOGWARNING)

    try:
        import threading as _thr
        _thr.Thread(target=_skin_worker, name='DexHub-skin', daemon=True).start()
    except Exception as exc:
        xbmc.log('[DexHub] skin worker thread failed: %s' % exc, xbmc.LOGWARNING)

    # v5.10.117: subtitle AutoSync (DexSubtitles' engine), only while
    # DexSubtitles itself is not enabled: never two services on one subtitle
    def _subsync_worker():
        try:
            from resources.lib.subsync import runner as _subsync_runner
            _subsync_runner.run(monitor)
        except Exception as exc:
            xbmc.log('[DexHub] subtitle AutoSync not started: %s' % exc, xbmc.LOGWARNING)

    try:
        import threading as _thr
        _thr.Thread(target=_subsync_worker, name='DexHub-subsync', daemon=True).start()
    except Exception as exc:
        xbmc.log('[DexHub] subtitle AutoSync thread failed: %s' % exc, xbmc.LOGWARNING)

    try:
        import threading as _thr
        _thr.Thread(target=_background_sync_loop, args=(monitor,), name='DexHubCoreLoop', daemon=True).start()
        _thr.Thread(target=_cloud_sync_loop, args=(monitor,), name='DexHubCloudLoop', daemon=True).start()
        _thr.Thread(target=_tmdbh_player_maintenance, name='DexHubTMDbHMaintenance', daemon=True).start()
        _thr.Thread(target=_bytecode_warm_job, name='DexHubBytecodeWarm', daemon=True).start()
    except Exception as exc:
        xbmc.log('[DexHub] could not spawn core sync thread: %s' % exc, xbmc.LOGWARNING)
    try:
        ProgressLoop(player).run()
    finally:
        # Clean shutdown of the poster proxy thread when Kodi tears the
        # service down. Errors here are non-fatal — daemon threads die
        # with the process anyway, but explicit cleanup is hygienic.
        try:
            from resources.lib import poster_proxy as _poster_proxy
            _poster_proxy.stop()
        except Exception:
            pass
        _stop_threads()
