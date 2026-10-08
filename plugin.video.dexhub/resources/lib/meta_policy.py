# -*- coding: utf-8 -*-
"""Cheap metadata profile checks shared by native and custom UI renderers."""


def source_appearance():
    from .settings_cache import cached_addon
    addon = cached_addon()
    selected = (addon.getSetting('global_meta_source_id') or '').strip()
    if selected:
        return selected == 'native' and (addon.getSetting('global_meta_source_all') or 'true') == 'true'
    return 'native' in (addon.getSetting('default_meta_source') or '').lower()
