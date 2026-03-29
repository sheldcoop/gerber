# Coordinate Systems — ODB++ and AOI Alignment

## The Three Spaces

There are three coordinate spaces in this application. Mixing them up breaks alignment.

---

### 1. ODB++ Raw Space (`unit_positions_raw`)

- **Origin:** Centre of the ODB++ panel (0, 0 = panel centre)
- **Values:** Mostly negative, e.g. `(-207.25, -218.15)` for the bottom-left unit
- **Where it comes from:** Directly from the step-repeat hierarchy in the ODB++ file
- **Used for:** Internal step-repeat parsing only (`gerber_renderer.py`)
- ⚠️ **Never use these for AOI alignment**

---

### 2. ODB++ Display Space (`unit_positions`)

- **Origin:** Bottom-left corner of the 510×515 mm physical panel frame (0, 0 = panel corner)
- **Values:** Positive, e.g. `(31.32, 23.10)` for the bottom-left unit
- **Where it comes from:** Raw positions shifted to centre content on the 510×515 frame
- **How the shift is computed:**
  ```
  shift_x = (panel_width  - content_width)  / 2  -  raw_min_x
  shift_y = (panel_height - content_height) / 2  -  raw_min_y
  display_pos = raw_pos + shift
  ```
- **Used for:** AOI alignment (Commonality, Single Unit Inspection)

---

### 3. AOI Machine Space (`X_MM`, `Y_MM` in Excel)

- **Origin:** Bottom-left corner of the same panel frame — matches Display Space
- **Values:** Positive, same range as display positions
- **Where it comes from:** AOI machine (Orbotech) reports coordinates in microns from panel edge; divided by 1000 to get mm
- **Key verified fact:** For every unit row, `min(Y_MM) ≈ unit_positions_y` for that row

  | UNIT_INDEX_Y | AOI Y_MM min | ODB++ display y_pos |
  |---|---|---|
  | 0 | 23.10 mm | 23.10 mm ✓ |
  | 1 | 58.04 mm | 57.40 mm ✓ |
  | 2 | 102.86 mm | 102.10 mm ✓ |

---

## The Alignment Formula

```
ALIGNED_X = X_MM - unit_pos_x
ALIGNED_Y = Y_MM - unit_pos_y
```

Where `unit_pos_x/y` is the **display position** of the unit (from `unit_positions`, not `unit_positions_raw`).

Result is in `[0, cell_width] × [0, cell_height]` — the same space as the CAM SVG in Plotly.

---

## Why NOT `unit_pos + cam_min`?

The CAM design is **centred at the step origin**. Features run from `cam_min_y ≈ -16.25 mm` to `cam_max_y ≈ +15.63 mm` relative to the step origin.

Old (wrong) formula subtracted `unit_pos + cam_min_y`, which added `+16.25 mm` upward shift to every defect. Result: half the defects floated above the board in the plot.

The AOI machine measures from the step origin itself, not from the bottom of features. So `cam_min` must not be subtracted.

---

## CAM SVG Placement in Plotly

The CAM SVG has local coordinates `[cam_min_x, cam_max_x] × [cam_min_y, cam_max_y]`.

Plotly placement:
```python
x     = 0        # left edge
y     = cell_h   # top edge (Plotly y increases upward)
sizex = cell_w
sizey = cell_h
```

This maps:
```
cam_min_x → Plotly x = 0
cam_max_x → Plotly x = cell_w
cam_min_y → Plotly y = 0
cam_max_y → Plotly y = cell_h
```

A defect with `ALIGNED_X/Y = (X_MM - unit_pos_x, Y_MM - unit_pos_y)` lands in the same `[0, cell_w] × [0, cell_h]` space → defect dot sits on the correct copper feature.

---

## ODB++ Step-Repeat Structure

Every unit position is computed by walking the hierarchy and summing offsets:

```
Unit absolute position (raw) =
    panel origin of QTR_PANEL
  + QTR_PANEL origin of CLUSTER
  + CLUSTER origin of UNIT
```

Example from this design:
```
(-121.5, -122.0)   ← QTR_PANEL[0,0] origin in panel space
+ (-115.0, -117.0) ← CLUSTER[0] origin in QTR_PANEL space
+ ( +29.25, +20.85)← UNIT[0,0] origin in CLUSTER space
= (-207.25, -218.15) ← unit_positions_raw[0]
```

### What ODB++ gives you (always, for any design)

| Data | Source |
|---|---|
| Unit width and height | `board_bounds` of the unit step |
| Exact (x, y) of every unit relative to each other | Step-repeat hierarchy |
| Gap between units | `dx - unit_width` at CLUSTER level |
| Gap between clusters | `dy - cluster_height` at QTR_PANEL level |
| Number of rows and columns | `nx`, `ny` at each level |
| CAM feature bounds (`cam_min`, `cam_max`) | Gerbonara render of the unit layer |

### What is hardcoded (fragile)

| Value | Where | Risk |
|---|---|---|
| `510.0 mm` panel width | `gerber_renderer.py`, `alignment.py` | Different customer panel = wrong centering shift |
| `515.0 mm` panel height | same | same |
| `13.5 mm` frame margin X | `alignment.py` | customer-specific |
| `15.0 mm` frame margin Y | `alignment.py` | customer-specific |

The 510×515 hardcode only affects how `unit_positions_raw` is shifted to produce `unit_positions`. If a different customer uses a different panel frame size, the display positions would be wrong and AOI alignment would break.

---

## UNIT_INDEX vs Position Index

The AOI machine labels each unit with `UNIT_INDEX_Y` and `UNIT_INDEX_X`. These are 0-based integers.

`_compute_cm_geometry()` builds an `origins` dict keyed by `(row_index, col_index)` where the indices are the rank of each unit's display position in sorted order (0 = leftmost/bottommost).

For alignment to work, the code normalises `UNIT_INDEX` to 0-based before lookup:

```python
ri = int(UNIT_INDEX_Y) - min(UNIT_INDEX_Y)   # handles 1-based AOI machines
ci = int(UNIT_INDEX_X) - min(UNIT_INDEX_X)
origin = origins[(ri, ci)]
```
