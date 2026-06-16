"""
core/render_plan.py — pure planning logic for incremental layer rendering.

When the layer-render selection changes, most per-layer SVGs from the previous
render are still valid: copper/soldermask/outline SVGs depend only on their own
features. Drill SVGs are the exception — the pipeline clips them to board
bounds aggregated from the SELECTED copper-family set (core/pipeline.py
Phases 4–5), so a drill layer is reusable only when that copper set is
unchanged.

Pure module: no Streamlit, no pipeline/cache imports (avoids cycles).
"""

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

# Layer families that drive board_bounds (pipeline Phase 4) — their own SVGs
# are selection-independent.
BOUNDS_LAYER_TYPES = frozenset({'copper', 'signal', 'power', 'mixed', 'outline'})
SELECTION_INDEPENDENT_TYPES = BOUNDS_LAYER_TYPES | {'soldermask'}

# ODB++ sometimes exports drill span layers (e.g. "2B-3B", "2F_3F") with matrix
# TYPE=MIXED or SIGNAL. Single source of the reclassification rule — the render
# pipeline applies the exact same rule via effective_layer_type below.
DRILL_SPAN_RE = re.compile(r'^\d+[FB](CO)?[-_]\d+[FB](CO)?', re.IGNORECASE)


def effective_layer_type(name: str, matrix_type: str) -> str:
    """Matrix layer type corrected by the drill-span naming rule."""
    if matrix_type != 'drill' and DRILL_SPAN_RE.match(name):
        return 'drill'
    return matrix_type


def _norm_types(layer_types: Mapping[str, str]) -> dict:
    return {k.lower(): v for k, v in layer_types.items()}


def copper_set_signature(selection: Optional[Iterable[str]],
                         layer_types: Mapping[str, str]) -> tuple:
    """Sorted lowercase names of the selected copper-family layers.

    Drill clipping (pipeline Phase 5) uses board bounds aggregated from exactly
    this set, so two selections with equal signatures clip drills identically.
    """
    if not selection:
        return ()
    types = _norm_types(layer_types)
    out = []
    for n in selection:
        if effective_layer_type(n, types.get(n.lower(), '')) in BOUNDS_LAYER_TYPES:
            out.append(n.lower())
    return tuple(sorted(out))


@dataclass(frozen=True)
class ReusePlan:
    reusable: tuple        # names (as given in new_selection) to copy from the previous render
    to_render: tuple       # names to parse + render fresh
    drill_invalidated: bool  # copper-set signature changed vs the previous render


def plan_layer_reuse(prev_selection: Optional[list],
                     prev_rendered_names: Iterable[str],
                     new_selection: Optional[list],
                     layer_types: Mapping[str, str]) -> ReusePlan:
    """Partition ``new_selection`` into reusable vs must-render layers.

    Rules (conservative — any ambiguity means re-render):
    - ``None`` selection on either side means "render everything"; v1 does no
      reuse for that rare path.
    - A layer is reusable iff it exists in ``prev_rendered_names`` AND its
      effective type is selection-independent, OR it is a drill layer and the
      copper-set signature is unchanged.
    - A name absent from ``prev_rendered_names`` (e.g. a drill layer the clip
      deleted, or a layer that failed to parse) is always re-rendered.
    - Name comparisons are lowercase, matching compose_render_key semantics.
    """
    if prev_selection is None or new_selection is None:
        return ReusePlan((), tuple(new_selection or ()), False)

    types = _norm_types(layer_types)
    prev_names = {n.lower() for n in prev_rendered_names}
    drill_ok = (copper_set_signature(prev_selection, layer_types)
                == copper_set_signature(new_selection, layer_types))

    reusable, to_render = [], []
    for n in new_selection:
        t = effective_layer_type(n, types.get(n.lower(), ''))
        ok = n.lower() in prev_names and (
            t in SELECTION_INDEPENDENT_TYPES or (t == 'drill' and drill_ok))
        (reusable if ok else to_render).append(n)
    return ReusePlan(tuple(reusable), tuple(to_render), not drill_ok)
