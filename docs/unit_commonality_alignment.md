# Unit Commonality — Defect Alignment & Copper Normalization

This document explains how the **Unit Commonality** view places AOI defects onto the PCB
design, how the coordinate frames relate, and how the copper-normalization + outlines work.
It is the reference for anyone touching `views/unit_commonality.py`, `views/cm_render.py`,
`views/cm_geometry.py`, or `core/data_utils.py`.

---

## 1. What the view does

The Commonality view **superimposes the defects of many units onto a single reference
unit**. Each selected unit's defects are normalized into a common local frame and drawn on
top of the rendered copper design, so you can see *where on the unit* defects recur across
the panel ("commonality").

Two things must end up in the **same coordinate frame** for the picture to be correct:

1. **Defects** — `X_MM`, `Y_MM` from the AOI Excel (panel-absolute millimeters).
2. **Copper design** — the rendered ODB++ copper layer (an SVG with its own local bounds).

A third element, the **unit outline**, is drawn for reference but carries no defects.

---

## 2. The three coordinate spaces

| Space | Origin | Where it comes from |
|-------|--------|---------------------|
| **AOI / panel space** | panel corner | `X_MM`, `Y_MM` in the Excel (microns ÷ 1000) |
| **Step origin** | each unit's placement on the panel | `panel_layout.unit_positions` (ODB++ step-repeat) |
| **Copper local frame** | the copper layer's own datum | `RenderedLayer.bounds = (min_x, min_y, max_x, max_y)` |

### Step origin (`origins` dict)
Built in [`core/data_utils.compute_cm_geometry`](../core/data_utils.py) and
[`views/cm_geometry._compute_origins`](../views/cm_geometry.py):

```
origins[(row, col)] = (unit_x, unit_y)   # the unit's display position on the panel
```

These are the unit grid positions in panel millimeters — i.e. **where each unit sits**.

### Why we subtract the step origin
The AOI measures every defect in panel space. Subtracting a unit's step origin removes that
unit's position, leaving the defect **relative to its own unit** — which is what lets all
units be overlaid on one reference unit:

```
local = X_MM − step_origin
```

This is pure **translation** — defect coordinates are never scaled or rotated (scaling would
distort a real physical measurement).

---

## 3. Unit vs Copper — two rectangles

A unit (board profile) is slightly **larger** than the copper on it; there is a dielectric
margin around the copper. Example (fhr0020, native/unrotated):

```
Unit profile : 43.5 × 37.5 mm     ← steps/<step>/profile  → panel_layout.unit_bounds
Copper bbox  : 42.90 × 36.90 mm   ← copper layer render   → first_lyr.bounds
Margin/side  : (43.5 − 42.90)/2 = 0.30 mm   (and (37.5 − 36.90)/2 = 0.30 mm)
```

So the copper sits centered inside the unit with ~0.30mm of dielectric on every side.

> **Where these numbers live in the TGZ**
> - Copper geometry: `steps/<step>/layers/<copper_layer>/features` (or `features.Z`). The
>   render's `bounding_box(MM)` of this file is the copper bbox.
> - Unit outline: `steps/<step>/profile`. Its bounding box gives the unit width/height.
> - Layer list/order: `matrix/matrix`.

---

## 4. Normalizing to the COPPER bounding box

The view normalizes defects to the **copper** frame so that on-copper defects land inside
`[0, copper_w] × [0, copper_h]`. This is done in
[`views/unit_commonality._render_defect_state`](../views/unit_commonality.py):

```python
# copper bbox + per-side margin between copper and the unit cell
_copper_w = _cb[2] - _cb[0]
_copper_h = _cb[3] - _cb[1]
_margin_x = (cell_w - _copper_w) / 2.0     # cell_w/h = unit profile dims
_margin_y = (cell_h - _copper_h) / 2.0

# effective reference cell = the copper bbox
ref_w, ref_h = _copper_w, _copper_h

# re-anchor: unit-corner frame → copper-corner frame by removing the margin
off_x = manual_offset_x - _margin_x
off_y = manual_offset_y - _margin_y
```

Final per-defect placement (in [`core/data_utils._align_defects`](../core/data_utils.py)):

```
ALIGNED_X = X_MM − step_origin_x + off_x
          = (X_MM − step_origin_x) − margin_x   (+ manual nudge)
ALIGNED_Y = (Y_MM − step_origin_y) − margin_y   (+ manual nudge)
```

### Why this works
- `X_MM − step_origin` puts the defect in the **unit** frame `[0, unit]`.
- Subtracting the margin shifts the origin to the **copper corner**, so a defect physically
  on the copper's lower-left lands at `(0, 0)` and on-copper defects fill `[0, copper]`.
- A defect that is genuinely **off-copper** (in the dielectric margin, soldermask,
  contamination, etc.) lands slightly **outside** `[0, copper]` — which is now visible and
  meaningful rather than hidden.

### The copper design uses the SAME frame
The copper SVG is anchored by
[`views/cm_render._design_anchor`](../views/cm_render.py):

```python
ref_shift = ((cell_w - copper_w)/2 - copper_min_x,
             (cell_h - copper_h)/2 - copper_min_y)
```

Because we pass `ref_w = copper_w` (cell == copper), this reduces to `ref_shift =
−copper_min` → the copper is drawn **corner-to-corner** at `[0, copper]`. Defects and copper
therefore share one frame and register exactly. The function is reused unchanged — feeding it
the copper dims is what selects corner-anchoring.

---

## 5. The two outlines

Drawn in `_render_defect_state` after the figure is built:

| Outline | Color | Rectangle | Meaning |
|---------|-------|-----------|---------|
| **Copper boundary** | green | `[0, 0] → [disp_w, disp_h]` | the copper bbox = canvas extent (where defects normalize) |
| **Unit outline** | white | `[−mdx, −mdy] → [disp_w+mdx, disp_h+mdy]` | the full unit profile, one margin out on each side |

```python
fig.add_shape(... x1=disp_w, y1=disp_h, line=green ...)          # copper
_mdx, _mdy = (_margin_y, _margin_x) if theta in (90, 270) else (_margin_x, _margin_y)
fig.add_shape(... x0=-_mdx, y0=-_mdy, x1=disp_w+_mdx, y1=disp_h+_mdy, line=white ...)  # unit
```

The white unit outline sits exactly one dielectric margin outside the green copper box, so
the border around the copper stays visible even though defects are normalized to copper.

---

## 6. Rotation (90° / 270°)

Some panels place units rotated (e.g. fhr0020 at 270°). Handling:

- **Defects** are placed by translation only and are not inverse-rotated — verified to fit
  in-cell for both fhr0010 (0°) and fhr0020 (270°). See the note in
  [`_align_defects`](../core/data_utils.py).
- **Display** rotates the *design* and swaps the *canvas* dimensions to the panel
  orientation via [`_display_dims`](../views/cm_render.py): `(w, h) → (h, w)` for 90/270.
- **Copper placement** pivots about the cell center in
  [`_layer_placement`](../views/cm_render.py); a centered/corner-anchored copper stays
  correctly placed under rotation.
- **Margins** swap with the angle for the white outline:
  `_mdx, _mdy = (_margin_y, _margin_x)` at 90/270. (For square-ish margins like 0.30/0.30
  this is a no-op, but it keeps non-square units correct.)

---

## 7. Function / file map

| Concern | Location |
|---------|----------|
| Unit selection + quadrant buttons | `views/cm_geometry._select_units` |
| Step origins + cell dims | `views/cm_geometry._compute_origins`, `core/data_utils.compute_cm_geometry` |
| Defect translation | `core/data_utils._align_defects` |
| Copper anchor (centering / corner) | `views/cm_render._design_anchor` |
| Layer placement + rotation pivot | `views/cm_render._layer_placement`, `_place_layer_image`, `_place_pairs` |
| Display-dim swap for rotation | `views/cm_render._display_dims` |
| Main orchestration + outlines | `views/unit_commonality._render_defect_state` |

---

## 8. Practical notes

- **Defects slightly outside copper are normal.** A handful in the margin (soldermask,
  contamination, edge rounding) is expected. A *systematic* offset of many defects in one
  direction indicates a real alignment/conversion bug, not margin noise.
- **`unit_bounds` is native (unrotated)**; the panel-space swap for 90/270 is applied where
  needed for display. Don't double-swap.
- **Manual offset** (`align_args.manual_offset_x/y`) stacks on top of the copper re-anchor
  for any residual nudge — useful for debugging alignment.
- **Marker style** defaults to **dot** (`ui/sidebar.py` selectbox, with fallbacks in
  `views/unit_commonality.py` and `viz/layout.py`).
