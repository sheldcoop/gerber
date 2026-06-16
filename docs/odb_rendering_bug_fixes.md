# ODB++ Rendering Bug Fixes — Post-Refactor Audit
**Date:** April 1, 2026  
**Affected layers:** FSR, BSR, 3B, 3F, 2B, 2F (copper layers wrongly classified as drill)  
**Root files changed:** `core/pipeline.py`, `ui/sidebar.py`, `odb/symbols.py`

---

## Background

The codebase was refactored from a monolithic `gerber_renderer.py` into a modular
architecture (`core/pipeline.py`, `core/layer_renderer.py`, `odb/symbols.py`, etc.).
After the refactor, specific layers showed massive solid "blob" pads at the corners of
the board, and copper layers (2B, 2F) were being rendered through the drill pipeline
instead of the copper pipeline.

Three independent bugs were introduced or surfaced during the migration.

---

## Bug 1 — `_DRILL_SPAN_RE` too broad: copper layers mis-routed as drill

### File
`core/pipeline.py`, line 33

### Symptom
Copper layers `2b` and `2f` were rendered in yellow (drill colour) with their Region
objects stripped and their apertures scaled by 0.0254. They appeared as scattered tiny
dots instead of filled copper pours.

### Root cause
During the `3b`/`3f` investigation (see Bug 2) the drill-span regex was widened to make
the hyphen optional:

```python
# Broken (too broad):
_DRILL_SPAN_RE = re.compile(r'^\d+[FB](CO)?(?:[-_]\d+[FB](CO)?)?$', re.IGNORECASE)
```

The intent was to catch `3b` and `3f` as drill layers because they appeared to contain
mils-scale apertures. However, that turned out to be a symbol-parsing bug (Bug 3), not
a classification issue. The optional suffix `(?:...)?$` caused `2b` and `2f` — legitimate
inner copper layers — to also match, routing them through the drill aperture-rescaling
and Region-strip path.

### Fix
Revert to requiring the hyphen, so only true span layers (`2b-3b`, `2f-3f`, `1fco-2f`,
`1bco-2b`) match:

```python
# Fixed:
_DRILL_SPAN_RE = re.compile(r'^\d+[FB](CO)?[-_]\d+[FB](CO)?', re.IGNORECASE)
```

### Key learning
A regex widened as a "quick fix" for one layer can silently mis-classify a different
layer. The drill span regex and the symbol-scale fix are **orthogonal concerns** — never
conflate them.

---

## Bug 2 — `board_bounds` excluded `outline`, clipping edge vias from `2b-3b`

### File
`core/pipeline.py`, line 205

### Symptom
The `2b-3b` (and `2f-3f`) drill layers were missing vias along the perimeter of the
unit. The rendered drill pattern looked fragmented near the board edges.

### Root cause
Phase 4 of the pipeline aggregates `board_bounds` from rendered layer extents and uses
it in Phase 5 to clip panel-scale drill layers down to a single unit. The refactored
pipeline restricted bounds aggregation to copper layers only:

```python
# Broken:
if layer_obj.layer_type in ('copper', 'signal', 'power', 'mixed'):
    all_bounds.append(bounds)
```

The original `gerber_renderer.py` excluded only `drill` (not soldermask/outline). By
also excluding soldermask and outline, the new `board_bounds` was tighter than the
physical board edge. Any via whose center fell between the outermost copper feature and
the actual board profile was outside `board_bounds ± 1 mm` and was deleted by the
clipping filter in Phase 5.

### Fix
Add `'outline'` to the set of types that contribute to `board_bounds`:

```python
# Fixed:
if layer_obj.layer_type in ('copper', 'signal', 'power', 'mixed', 'outline'):
    all_bounds.append(bounds)
```

`soldermask` is intentionally still excluded — solder mask extends slightly past the
board profile (dam geometry) and would inflate the bounding box unnecessarily.
`outline` is the profile layer whose extent exactly equals the physical board edge —
the authoritative upper bound for via placement.

### Key learning
When restricting which layer types drive a bounding box, always ask: *"Is there a
renderable layer type whose extent is larger than copper but still within the physical
board?"*. The answer here is yes: the `outline` (profile) layer.

---

## Bug 3 — Number-extraction fallback in `parse_symbol_descriptor` produced gigantic apertures for FSR/BSR fiducials

### File
`odb/symbols.py`, line 144 (fallback block at the end of `parse_symbol_descriptor`)

### Symptom
FSR and BSR layers rendered with massive solid circles (~1000 mm diameter) at the
corners of the board, exactly where optical fiducials are placed.

### Root cause

The `symbols/` directory of this job contains user-defined complex symbols:

```
fiducial_swiss1000um_board1/
fiducial_swiss600_board1/
sr_coupon_sig/
sr_coupon_sm/
```

When these names appear as symbol descriptors in a features file (`$5 fiducial_swiss1000um_board1`),
`parse_symbol_descriptor` is called. None of the explicit prefix checks (`r`, `rect`,
`oval`, `cross`, …) match, so control falls through to the legacy fallback:

```python
# Broken fallback:
nums = [float(n) for n in re.findall(r'\d+\.?\d*', desc) if float(n) > 0]
if nums:
    sz = max(nums[0], nums[1]) if len(nums) >= 2 else nums[0]
    return _ODBSymbol('round', sz, sz, desc)
```

For `fiducial_swiss1000um_board1`:
- `re.findall` extracts `[1000.0, 1.0]`
- `max(1000, 1)` = `1000`
- Returns `_ODBSymbol('round', 1000, 1000, ...)` — a 1000 mm circle

Because the returned shape is `'round'` (not `'unknown'`), the user-symbol lookup in
`_parse_layer_to_gerbonara` is silently bypassed:

```python
# Only substitutes when shape == 'unknown':
if sym.shape == 'unknown' and sym.raw_desc.lower() in user_sym_map:
    symbols[idx] = user_sym_map[sym.raw_desc.lower()]
```

So `load_user_symbols` (which correctly parsed the fiducial features file and computed
a ~1 mm bounding box) never fired. Gerbonara received `CircleAperture(1000 mm)` and
filled the entire viewport.

**Why the original monolithic file worked:** The pre-refactor `odb_parser.py` returned
`_ODBSymbol('unknown', ...)` for unrecognised descriptors, allowing the user-symbol
substitution to always fire for complex symbols.

### Fix
Replace the number-extraction fallback with a plain `'unknown'` return:

```python
# Fixed:
return _ODBSymbol('unknown', 0.0, 0.0, desc)
```

Flow after fix:
1. `fiducial_swiss1000um_board1` → `shape='unknown'`
2. `_parse_layer_to_gerbonara` → `user_sym_map` lookup fires
3. `load_user_symbols` already has the correct bounding box (~1 mm × 1 mm)
4. Flash renders as a small fiducial mark

For symbols not present in `symbols/` at all, `aperture_cache.get(sym_idx)` returns
`None` and the flash is silently skipped — identical to the original behaviour.

### Key learning
Never extract numbers from a symbol *name* to infer its size. Symbol names like
`fiducial_swiss1000um_board1` embed human-readable annotations, not machine dimensions.
The only authoritative source for a user-defined symbol's size is its `features` file.
`'unknown'` is the correct sentinel — it explicitly signals "go look this up".

---

## Bug 4 — Sidebar: copper and soldermask mixed in one expander

### File
`ui/sidebar.py`

### Symptom
The Layer Controls sidebar had a single "Copper & Soldermask" expander. Soldermask
layers (FSR, BSR) appeared alongside copper layers, making it hard to isolate each
group.

### Fix
Split into three dedicated expanders driven by `layer_type`:

| Expander | Types included |
|---|---|
| **Copper** | `copper`, `signal`, `power`, `mixed` |
| **Soldermask** | `soldermask` |
| **Drill / Via** | `drill` |

Each expander uses the same `_layer_row` helper and `_copper_sort_key` / `_drill_sort_key`
ordering. Only the first copper layer defaults to visible; soldermask and drill default
to hidden.

---

## Summary of all changes

| File | Change |
|---|---|
| `core/pipeline.py` | Reverted `_DRILL_SPAN_RE` to require hyphen |
| `core/pipeline.py` | Added `'outline'` to `board_bounds` aggregation |
| `odb/symbols.py` | Replaced number-extraction fallback with `'unknown'` return |
| `ui/sidebar.py` | Split one expander into three (Copper / Soldermask / Drill) |

---

## Suggestions for future robustness

### 1. Add a symbol-descriptor test suite
`parse_symbol_descriptor` is the single most fragile point in the pipeline. A pytest
parametrize table covering every known prefix (`r`, `rect`, `oval`, `rr`, `cross`,
`fiducial_*`, `sr_coupon_*`, etc.) would catch regressions immediately. Include at
least one negative test: a name containing a large number must **not** produce an
aperture of that size.

### 2. Cap `user_sym_map` bounding box at a sane maximum
Even with the `'unknown'` fix, `load_user_symbols` could return a very large bounding
box if a coupon strip is gigantic (e.g. a 30 mm × 5 mm SR coupon). Consider capping
the substituted aperture:

```python
MAX_USER_SYM_MM = 5.0
result[sym_name.lower()] = _ODBSymbol(
    'rect',
    min(w, MAX_USER_SYM_MM),
    min(h, MAX_USER_SYM_MM),
    sym_name,
)
```

This provides a second safety net without hiding the symbol entirely.

### 3. Validate `board_bounds` sanity after aggregation
After Phase 4, assert that `board_bounds` dimensions are physically plausible:

```python
unit_w = board_bounds[2] - board_bounds[0]
unit_h = board_bounds[3] - board_bounds[1]
assert 1.0 < unit_w < 500.0 and 1.0 < unit_h < 500.0, \
    f"Implausible board_bounds: {board_bounds}"
```

If this fires it almost always means a layer with unscaled mils coordinates leaked into
the bounds aggregation (Bug 1 / Bug 2 class of failure).

### 4. Log `step_name` and drill reclassification decisions
Add a single `warnings.append(...)` line whenever `_DRILL_SPAN_RE` reclassifies a
layer, and whenever the profile overrides the copper-derived `unit_w`/`unit_h`. These
are the two most common sources of silent misconfiguration and they currently leave no
trace in the render output.

### 5. Freeze `_DRILL_SPAN_RE` with a comment explaining the contract
Whoever touches this regex next needs to understand the invariant:

```python
# Matches inter-layer drill SPAN names only (require hyphen/underscore separator).
# Examples that MUST match:  2b-3b  2f-3f  1fco-2f  1bco-2b
# Examples that MUST NOT:    2b  2f  3b  3f  (those are copper layers)
# DO NOT make the second half optional — it will catch copper layer names.
_DRILL_SPAN_RE = re.compile(r'^\d+[FB](CO)?[-_]\d+[FB](CO)?', re.IGNORECASE)
```
