# -*- coding: utf-8 -*-
"""Small Kodi entry point.

The static Home and normal Settings front door are handled without importing
the large compatibility router. Feature code is loaded only after the user
opens a real destination.
"""

def run():
    try:
        return _run_inner()
    finally:
        # v5.10.15: the invocation is over, so every worker thread it started
        # must be told to exit. Without this Kodi's invoker sits in its
        # "wait for the script's threads" loop until it times out, then leaks
        # the whole generation of pools — see shutdown_lane_pools().
        try:
            from .runtime_cleanup import shutdown_loaded_pools
            shutdown_loaded_pools()
        except Exception:
            pass


def _unquote(text):
    """urllib.parse.unquote_plus without importing urllib.parse (v5.10.133:
    with what it brings along, a quarter of a row call's imports)."""
    text = text.replace('+', ' ')
    if '%' not in text:
        return text
    parts = text.split('%')
    out = bytearray(parts[0].encode('utf-8'))
    for part in parts[1:]:
        code = part[:2]
        if len(code) == 2 and all(c in '0123456789abcdefABCDEF' for c in code):
            out.append(int(code, 16))
            out += part[2:].encode('utf-8')
        else:
            out += ('%' + part).encode('utf-8')
    return out.decode('utf-8', 'replace')


def _query(text):
    """dict(parse_qsl(text, keep_blank_values=True)), for the skin's calls."""
    out = {}
    for pair in text.split('&'):
        if pair:
            key, _sep, value = pair.partition('=')
            out[_unquote(key)] = _unquote(value)
    return out


def _run_inner():
    import sys
    # v5.10.109: skin.dexhub's own routes. skin_row (a row of the skin's Home)
    # is answered from its cache before anything else is imported.
    if len(sys.argv) > 2 and 'action=skin_' in sys.argv[2]:
        try:
            _skin_params = _query(sys.argv[2].lstrip('?'))
        except Exception:
            _skin_params = {}
        if str(_skin_params.get('action') or '').startswith('skin_'):
            if _skin_params.get('action') != 'skin_row':
                try:
                    from .settings_cache import invalidate as _dh_invalidate
                    _dh_invalidate()
                except Exception:
                    pass
            from .skinui import serve as _skin_serve
            return _skin_serve.run(_skin_params)
    try:
        from .settings_cache import invalidate as _dh_invalidate
        _dh_invalidate()
    except Exception:
        pass

    # Player actions bypass all catalog/account routing. Once selected, errors
    # must not fall through and start the same playback a second time.
    import sys
    from urllib.parse import parse_qsl
    # v5.10.96: the Dex Hub Home windows (homeui) open before any router
    # import; the router loads later on a worker thread when a row needs it.
    try:
        _home_params = dict(parse_qsl(sys.argv[2].lstrip('?'))) if len(sys.argv) > 2 else {}
    except Exception:
        _home_params = {}
    if _home_params.get('action') in ('home_ui', 'home_folder', 'home_grid', 'home_layout', 'home_setup',
                                       'home_live', 'home_screensaver', 'home_servers', 'home_search',
                                       'home_details', 'home_vod_play'):
        from .homeui.app import run as _home_run
        return _home_run(_home_params)
    # v5.10.101: Kodi's subtitle dialog (Dex Hub is a subtitle service too).
    if _home_params.get('action') in ('search', 'manualsearch', 'dexsub_download'):
        from . import subtitle_service as _subs
        if _subs.wants(_home_params):
            return _subs.run(_home_params)
    # v5.10.84: a Home widget asking for a stable listing it already received
    # is answered from the recording before any router module is imported.
    try:
        from . import widget_cache
        if len(sys.argv) > 1 and str(sys.argv[1]) == '-1':
            # an action run for its effect (RunPlugin: a watched mark, a
            # removal, playback): continue and next up render fresh next time
            widget_cache.mark_changed()
        elif widget_cache.try_replay():
            return None
        else:
            widget_cache.begin()
    except Exception:
        pass
    from . import player_entry
    params = dict(parse_qsl(sys.argv[2].lstrip('?'))) if len(sys.argv) > 2 else {}
    if params.get('action') in player_entry.ACTIONS:
        return player_entry.run()

    # v5.4.32: zero-heavy-import front door. This intentionally runs before
    # i18n/plugin imports so Home/Settings stay instant on ARM/CoreELEC.
    try:
        from .routes import simple_entry as _simple
        result = _simple.dispatch()
        if result is not _simple._NOT_HANDLED:
            return result
    except Exception:
        # Never strand the user because of the fast path; the mature router
        # remains the compatibility fallback.
        #
        # v5.10.77: but never silently. A crash inside a Home/Settings screen
        # used to vanish without a line in kodi.log and then fall through to the
        # full router, which cannot explain an action it never owned — the user
        # saw "nothing happened" and there was nothing to diagnose it from.
        try:
            import traceback
            import xbmc
            xbmc.log('[DexHub] fast-path route failed, falling back to the full '
                     'router:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        except Exception:
            pass

    try:
        from .i18n import reset_language_cache
        reset_language_cache()
    except Exception:
        pass
    from .plugin import run as plugin_run
    return plugin_run()
