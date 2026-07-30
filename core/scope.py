"""core/scope.py — the single place views read the global Analysis Scope.

The Analysis Scope bar (``app.py``) owns four selections: buildups, side,
panels, and verification codes. It writes them to ``st.session_state`` under the
``scope_*`` keys. Views must not read those keys directly — they call
``scoped_defects(aoi)`` and get a dataframe that already honours every one of
them, so a filter added here applies to every tab at once.

Fallbacks matter: the scope bar only renders when ``aoi.has_data`` is true, so a
view can run before any ``scope_*`` key exists. Every fallback below therefore
means "don't filter on this", never "filter to nothing".
"""

import streamlit as st

from core.data_utils import filter_aoi_cm

# Sides the scope bar can offer, and the SIDE code each maps to in the data.
SIDE_CODES = {'Front': 'F', 'Back': 'B'}


def available_verifications(aoi) -> list[str]:
    """Every verification code in the dataset, ignoring the current scope.

    The global verification multiselect must source its ``options`` from here and
    not from a scope-filtered frame: a keyed ``st.multiselect`` whose options
    shrink underneath a stored selection is a Streamlit footgun.
    """
    if aoi is None or not aoi.has_data:
        return []
    if 'VERIFICATION' not in aoi.all_defects.columns:
        return []
    return sorted(aoi.all_defects['VERIFICATION'].dropna().unique().tolist())


def default_side(aoi) -> list[str]:
    """Single-element side default, honouring which sides the data actually has."""
    sides = list(getattr(aoi, 'sides', []) or [])
    if 'F' in sides:
        return ['Front']
    if 'B' in sides:
        return ['Back']
    return ['Front']


def read_scope(aoi) -> dict:
    """Resolve the active scope selections, with unfiltered fallbacks."""
    bu = st.session_state.get('scope_bu_sel', getattr(aoi, 'buildup_numbers', []))
    side = st.session_state.get('scope_side_sel', default_side(aoi))

    panels = st.session_state.get('scope_panel_sel', None)
    verif = st.session_state.get('scope_verif_sel', None)

    # Panels and verification codes treat "empty" differently on purpose.
    #
    # An empty panel list is never a real user intent — the scope bar enforces a
    # min-1 guard, so empty only happens when the dataset has no panels to pick
    # from. It must therefore mean "don't filter", or a dataset whose panel_ids
    # failed to populate would silently filter every view down to nothing.
    #
    # An empty verification list IS a real intent: the user deliberately cleared
    # the multiselect, and must then see nothing rather than everything.
    return {
        'bu': tuple(sorted(bu)) if bu else (),
        'side': tuple(sorted(side)) if side else tuple(default_side(aoi)),
        'panels': tuple(panels) if panels else None,
        'verif': None if verif is None else tuple(verif),
    }


def scoped_defects(aoi):
    """``aoi.all_defects`` with the full global scope applied."""
    if aoi is None or not aoi.has_data:
        return None
    scope = read_scope(aoi)
    return filter_aoi_cm(
        aoi.all_defects,
        scope['bu'],
        scope['side'],
        panel_filter=scope['panels'],
        verif_filter=scope['verif'],
    )


def scope_caption(aoi) -> str:
    """Human-readable summary of the active scope, for a ``st.caption``."""
    scope = read_scope(aoi)
    bits = [f"BU-{int(b):02d}" for b in scope['bu']]
    bits += list(scope['side'])

    panels = scope['panels']
    if panels is not None:
        # Naming every panel is unreadable past a handful of them.
        bits.append(str(panels[0]) if len(panels) == 1 else f"{len(panels)} panels")

    verif = scope['verif']
    if verif is not None:
        all_codes = available_verifications(aoi)
        if not verif:
            bits.append("no codes")
        elif len(verif) < len(all_codes):
            bits.append(", ".join(str(v) for v in verif))

    return ", ".join(bits) if bits else "all data"
