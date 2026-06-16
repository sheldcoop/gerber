# Coordinate Systems & Panelization — Reference

## 1. Three Coordinate Spaces

| Space | Origin | Who uses it |
|-------|--------|-------------|
| **ODB++ Raw** | Step origin (centre of board, can be negative) | Inside ODB++ files (step-repeat, features) |
| **ODB++ Display** | Panel lower-left corner (all positive) | After centering shift applied by `compute_unit_positions` |
| **AOI Machine** | Panel lower-left corner (all positive) | Excel X_MM / Y_MM columns from Orbotech |

AOI Machine ≈ ODB++ Display → alignment formula is simply `ALIGNED = X_MM − unit_origin_X`.

---

## 2. What is `unit_origin_X / Y`?

The panel-absolute coordinate (mm from panel lower-left) of a unit's **copper left/bottom edge**.

Computed by `compute_unit_positions()`:
1. Read step-repeat origins from ODB++ (`stephdr` files) — these are in ODB++ Raw space.
2. Apply centering shift: `shift = (panel_width − content_width) / 2 − raw_min_x`
3. Result = display-space position of each unit's copper edge.

`build_panel_svg` confirms this: it places each unit via `translate(x_mm − viewBox_x)`, which puts the copper left edge exactly at `x_mm` in the composite SVG.

---

## 3. Alignment Formula (Unit Commonality / Cluster Triage)

```
ALIGNED_X = X_MM − unit_origin_X
ALIGNED_Y = Y_MM − unit_origin_Y
```

No division by unit width. No cam_min correction. The result is directly in mm on the Plotly canvas (which also runs 0 → copper_width mm).

**Example:** defect at X_MM = 41 mm, unit origin at 31 mm → ALIGNED_X = 10 mm → plots 10 mm from the unit's left copper edge.

---

## 4. Unit Size — Three Different Numbers

| Metric | Source | Typical value |
|--------|--------|---------------|
| **Profile width/height** | `steps/unit/profile` OB/OS outlines | 33.500 mm ← authoritative |
| **First-layer copper extent** | `bounds` of first non-drill layer | 36.49 × 35.69 mm |
| **Aggregate copper extent** | `board_bounds` = min/max across ALL layers | 40.12 × 47.07 mm |

**Why they differ:** The step origin (0,0) is near the centre of the board. Manufacturing rails and soldermask extend beyond the profile edge on some layers. The aggregate picks the widest reach across all layers.

**What the app uses for centering:** Profile (33.500 mm) — read from `S P / OB / OS / OE` outlines in the profile features file. Falls back to aggregate copper bounds if profile is missing.

---

## 5. Step-Repeat Hierarchy (this design)

```
panel
  └── cluster  (step-repeat of cluster)
        └── unit  (step-repeat of unit within cluster)
```

- **Pitch X/Y** = distance between successive step origins (mm)
- **Inter-unit gap** = Pitch − unit_width (e.g., 34.30 − 33.50 = 0.80 mm)
- **Inter-cluster gap** = cluster pitch − cluster content width
- **Cluster size** = NX × NY units tiled at unit pitch

Step-repeat coordinates in `stephdr` are in **inches** for InCAM Pro exports. The app detects this and multiplies by 25.4.

---

## 6. cam_min — What It Is and Why It Is NOT Subtracted

`cam_min_x = layer.bounds[0]` = the local X coordinate of the leftmost copper feature relative to the step origin. For this design: cam_min_x ≈ −16.4 mm (copper extends 16.4 mm to the LEFT of the step origin).

The SVG is placed in Plotly so that `cam_min_x → Plotly x = 0`. Geometrically you'd expect to subtract cam_min from defect coordinates — but **unit_origin_X already encodes this offset** (via `tx = x_mm − viewBox_x` in `build_panel_svg`). Subtracting cam_min again is a double-count and shifts defects by ~16–20 mm.

---

## 7. Panel Size

Taken from the **top-level step profile** (`steps/panel/profile`). Same OB/OS parsing as unit profile. Fallback: 510 × 515 mm when profile is unavailable (matches physical frame used by the AOI machine).

Panel size drives the centering shift. If the wrong panel size is used, all unit_origin positions shift uniformly, misaligning every defect by the same constant offset.
