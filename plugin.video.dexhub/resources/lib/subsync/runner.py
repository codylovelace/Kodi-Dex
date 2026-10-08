# -*- coding: utf-8 -*-
"""AutoSync in Dex Hub's service, only while DexSubtitles is not enabled (v5.10.117).

DexSubtitles' own service runs the same queue and subtitle watcher: with
both add-ons enabled that is the one that runs, and Dex Hub waits; turning
DexSubtitles off (or removing it) hands the work to Dex Hub within seconds.
Nothing is imported while it is not Dex Hub's turn or AutoSync is off.
"""
import xbmc

OTHER = 'service.subtitles.dexworld'


def other_enabled():
    """DexSubtitles is installed and enabled: its service syncs."""
    try:
        return bool(xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % OTHER))
    except Exception:
        return False


def enabled():
    try:
        import xbmcaddon
        value = (xbmcaddon.Addon('plugin.video.dexhub').getSetting('autosync_enabled') or 'true').lower()
        return value not in ('false', '0', 'no', 'off')
    except Exception:
        return True


def run(monitor):
    player = None
    watcher = None
    autosync = None
    mine = None
    checked = 0.0
    import time
    while not monitor.abortRequested():
        now = time.time()
        if mine is None or now - checked > 10.0:
            checked = now
            turn = enabled() and not other_enabled()
            if turn != mine:
                mine = turn
                xbmc.log('[DexHub] subtitle AutoSync: %s' % (
                    'runs in Dex Hub' if mine else
                    ('left to DexSubtitles' if other_enabled() else 'off')), xbmc.LOGINFO)
        if not mine:
            if monitor.waitForAbort(10):
                break
            continue
        try:
            if autosync is None:
                from . import autosync as _autosync
                from . import dexwatch as _dexwatch
                autosync = _autosync
                player = xbmc.Player()
                watcher = _dexwatch.Watcher(autosync, autosync._setting_bool, autosync.TEMP_DIR)
            autosync.process_queue_once(player)
            watcher.tick(player)
            autosync.flush_notice()
        except Exception as exc:
            xbmc.log('[DexHub] subtitle AutoSync: %s' % exc, xbmc.LOGWARNING)
        if monitor.waitForAbort(1):
            break
