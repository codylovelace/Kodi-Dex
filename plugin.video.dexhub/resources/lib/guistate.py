# -*- coding: utf-8 -*-
"""Which window or dialog is up, without xbmc.getCondVisibility (v5.10.140).

xbmc.getCondVisibility takes Kodi's frame-move guard: Kodi's GUI thread
opens a slot for the call in its frame and sleeps there 2 to 80 ms (the
slot grows while calls keep coming, 80 ms when no video plays). The service
asked it about forty times a second while the Home was idle (the busy
dialog, the Home, the collection page, the title page), so every page lost
frames; a preview start polled the busy dialog every 30 ms for as long as
the stream took to open. xbmcgui.getCurrentWindowId and
getCurrentWindowDialogId only hold the graphics lock for a moment.
"""
import xbmcgui

HOME = 10000
VIDEOS = 10025
BUSY = (10138, 10160)           # busydialog, busydialognocancel
VIDEO_INFO = 12003
FULLSCREEN_VIDEO = 12005
_INVALID = 9999
_MASK = 0xffff


def window_id(wid):
    """Kodi's id of a window named by number: a skin's custom window 1190
    is 11190 (ids up to 9999 are offsets from the Home's 10000)."""
    wid = int(wid)
    return wid + HOME if wid <= _INVALID else wid


def active():
    """The active window (dialogs are not windows)."""
    try:
        return xbmcgui.getCurrentWindowId() & _MASK
    except Exception:
        return 0


def top_dialog():
    """The topmost modal dialog, or 9999 when none is open."""
    try:
        return xbmcgui.getCurrentWindowDialogId() & _MASK
    except Exception:
        return _INVALID


def is_active(wid):
    return active() == window_id(wid)


def busy_dialog():
    """Kodi's busy spinner is up (with or without its cancel button)."""
    return top_dialog() in BUSY


def dialog_up():
    """A modal dialog is open (the busy spinner included)."""
    return top_dialog() not in (_INVALID, 0)


def video_info_up():
    """The title page (DialogVideoInfo) is the dialog on top."""
    return top_dialog() == VIDEO_INFO
