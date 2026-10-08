# -*- coding: utf-8 -*-
"""Shared inline skin preferences; small property updates, no skin reload."""
import xbmc
import xbmcgui

# key: (default, choices, Arabic label, English label)
OPTIONS = {
    'iptv_epg_enabled': ('true', None, 'دليل البرامج EPG', 'Programme guide EPG'),
    'iptv_show_live': ('true', None, 'قسم القنوات IPTV', 'IPTV channels section'),
    'iptv_show_vod': ('true', None, 'قسم VOD', 'VOD section'),
    'iptv_show_movies': ('true', None, 'أفلام VOD', 'VOD movies'),
    'iptv_show_series': ('true', None, 'مسلسلات VOD', 'VOD series'),
    'player_show_clearlogo': ('true', None, 'شعار العمل في المشغّل', 'Work logo in player'),
    'player_logo_position': ('above_info', ('above_info', 'top_left', 'top_right'), 'مكان شعار العمل', 'Work logo position'),
    'player_show_plot': ('true', None, 'النبذة في المشغّل', 'Plot in player'),
    'player_show_metadata': ('true', None, 'بيانات العمل في المشغّل', 'Metadata in player'),
    'player_show_ratings': ('true', None, 'التقييمات في المشغّل', 'Ratings in player'),
    'player_show_studio': ('true', None, 'شعار الشبكة في المشغّل', 'Network logo in player'),
    'metadata_badges': ('true', None, 'تعزيز التقييمات وشعار الشبكة', 'Supplement ratings & network logo'),
    'homeui_trailers': ('true', None, 'التريلر التلقائي', 'Automatic trailers'),
    'homeui_trailer_sound': ('true', None, 'صوت التريلر', 'Trailer sound'),
    'homeui_trailer_delay': ('3', tuple(str(n) for n in range(16)), 'انتظار التريلر', 'Trailer delay'),
    'homeui_trailer_quality': ('720p', ('480p', '720p', '1080p'), 'جودة التريلر', 'Trailer quality'),
    'homeui_immersive': ('true', None, 'التريلر بملء الشاشة', 'Fullscreen trailer'),
    'homeui_carousel': ('true', None, 'تدوير البانر', 'Banner carousel'),
    'homeui_carousel_interval': ('10', tuple(str(n) for n in range(5, 31)), 'مدة البانر', 'Banner interval'),
    'homeui_kenburns': ('true', None, 'تحريك الخلفيات', 'Animated backgrounds'),
    'homeui_focus_gif': ('true', None, 'الصور المتحركة', 'Animated images'),
    'homeui_plot_scroll': ('true', None, 'تحريك النبذة', 'Scrolling plot'),
    # v5.10.141: unfocused rows faded to 60 % and made the Home look grey;
    # light (85 %) by default
    'homeui_row_dim': ('light', ('light', 'off', 'strong'), 'تعتيم الصفوف غير المحددة', 'Dim unfocused rows'),
    'homeui_light_mode': ('true', None, 'الوضع الخفيف', 'Light mode'),
    'homeui_live_preview': ('true', None, 'معاينة القنوات', 'Channel previews'),
    'homeui_show_continue': ('true', None, 'صف متابعة المشاهدة', 'Continue Watching row'),
    'homeui_show_nextup': ('false', None, 'صف الحلقة التالية', 'Next Up row'),
    'favorites_layout': ('separate', ('separate', 'merged'), 'ترتيب المفضلة', 'Favorites layout'),
}
DISPLAY_ONLY = frozenset(k for k in OPTIONS if k.startswith('player_') or k in ('homeui_trailers', 'homeui_trailer_delay', 'homeui_trailer_sound', 'homeui_trailer_quality', 'homeui_immersive', 'homeui_carousel', 'homeui_carousel_interval', 'homeui_kenburns', 'homeui_focus_gif', 'homeui_plot_scroll', 'homeui_live_preview')) | frozenset(('metadata_badges', 'iptv_epg_enabled', 'iptv_show_live', 'iptv_show_vod', 'iptv_show_movies', 'iptv_show_series', 'favorites_layout', 'player_badge_style', 'player_badges_json_url', 'player_badges_folder', 'homeui_row_dim'))


def _addon():
    from .settings_cache import cached_addon
    return cached_addon()


def value(key):
    default, choices, _, _ = OPTIONS[key]
    result = _addon().getSetting(key) or default
    if choices and result not in choices:
        # A slider may have stored "3.0"; preserve its valid numeric choice.
        try:
            result = str(int(float(result)))
        except (ValueError, TypeError):
            result = default
        return result if result in choices else default
    return ('false' if str(result).lower() == 'false' else 'true') if choices is None else result


def enabled(key):
    return value(key) == 'true'


def publish():
    win = xbmcgui.Window(10000)
    for key in OPTIONS:
        shown = value(key)
        prop = 'dhs.pref.' + key
        if win.getProperty(prop) != shown:
            win.setProperty(prop, shown)


def set_value(key, selected):
    if key not in OPTIONS:
        return False
    choices = OPTIONS[key][1] or ('true', 'false')
    if selected not in choices:
        return False
    _addon().setSetting(key, selected)
    publish()
    if key.startswith('player_') and xbmc.getSkinDir() == 'skin.dexhub':
        xbmc.executebuiltin('Skin.SetBool(OSD.DisplayInfoOnControls)')
    return True


def edit(key, txt):
    if key not in OPTIONS:
        return False
    _, choices, ar, en = OPTIONS[key]
    current = value(key)
    if choices is None:
        return set_value(key, 'false' if current == 'true' else 'true')
    labels = {
        'above_info': txt('فوق بيانات العمل', 'Above work details'),
        'top_left': txt('أعلى اليسار', 'Top left'),
        'top_right': txt('أعلى اليمين', 'Top right'),
        'separate': txt('قوائم مستقلة حسب المصدر', 'Separate lists by source'),
        'merged': txt('قائمة موحدة', 'Combined list'),
        'light': txt('خفيف', 'Light'),
        'off': txt('بدون', 'None'),
        'strong': txt('قوي', 'Strong'),
    }
    shown = [labels.get(v, v + txt(' ثوانٍ', ' seconds') if key.endswith(('delay', 'interval')) else v) for v in choices]
    pick = xbmcgui.Dialog().select(txt(ar, en), shown, preselect=choices.index(current))
    return set_value(key, choices[pick]) if pick >= 0 else True


def player_dialog(txt):
    """Equivalent controls remain available when another skin is active."""
    keys = [k for k in OPTIONS if k.startswith('player_')]
    while True:
        labels = [txt('شكل رموز الجودة · صور ومعاينة', 'Quality badge style · preview')]
        display = {'true': txt('مفعّل', 'On'), 'false': txt('مطفأ', 'Off'), 'above_info': txt('فوق البيانات', 'Above details'), 'top_left': txt('أعلى اليسار', 'Top left'), 'top_right': txt('أعلى اليمين', 'Top right')}
        labels += [txt(OPTIONS[k][2], OPTIONS[k][3]) + ' · ' + display.get(value(k), value(k)) for k in keys]
        pick = xbmcgui.Dialog().select(txt('مظهر المشغّل', 'Player appearance'), labels)
        if pick < 0:
            return True
        if pick == 0:
            from . import player_badges
            player_badges.show(txt)
        else:
            edit(keys[pick - 1], txt)


def iptv_available(media=''):
    if media == 'channels':
        return enabled('iptv_show_live')
    if media in ('movie', 'series'):
        return enabled('iptv_show_vod') and enabled('iptv_show_movies' if media == 'movie' else 'iptv_show_series')
    return iptv_available('channels') or iptv_available('movie') or iptv_available('series')
