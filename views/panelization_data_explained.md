# Understanding the Panelization Data View

The **📊 Panelization Data** view is a powerful diagnostic tool that provides a transparent, in-depth look at how the application interprets the physical layout of your ODB++ panel. It is the single source of truth for all coordinate and dimension calculations. Use this view to understand the source of alignment offsets and to verify the structural integrity of your ODB++ data.

This document breaks down each section of the view.

---

## 1. Panel Summary

This section provides the high-level, authoritative dimensions of your panel and the individual PCB units within it.

| Metric | Description | Source & Importance |
| :--- | :--- | :--- |
| **Panel Width/Height** | The overall dimensions of the manufacturing panel frame. | Read from the top-level step's `profile` layer in the ODB++ archive. If missing, it falls back to a default (e.g., 510x515 mm). **This is the canvas for all centering calculations.** |
| **Unit Width/Height (profile)** | The physical, mechanical size of a single routed PCB. | Read from the `profile` layer of the leaf step (e.g., `unit`). **This is the authoritative board size** used for calculating gaps and centering the entire grid. |
| **Total Units, Rows, Cols** | The grid dimensions of the panel. | Derived by analyzing the unique X and Y positions from the step-repeat hierarchy. |
| **Copper Extent** | The bounding box of copper features, shown as `(1st layer) / (all layers)`. | See the detailed explanation below. This is **not** the board size. |

### The Three "Unit Sizes"

It's critical to understand that "unit size" can mean three different things. The app uses the **Profile** for its core alignment math.

| Metric | Source | Typical value | Why it's different |
| :--- | :--- | :--- | :--- |
| **Profile Width/Height** | `steps/unit/profile` outlines | **33.500 mm** | **This is the true physical board edge.** It's what the centering logic is based on. |
| **1st Layer Copper** | Bounding box of the first copper layer | 36.49 × 35.69 mm | Copper features like test coupons or fiducials often extend slightly beyond the routed board edge. |
| **Aggregate Copper** | Bounding box of *all* copper layers | 40.12 × 47.07 mm | This is the widest possible reach of any copper feature on any layer, including manufacturing rails. |

---

## 2. Step-Repeat Hierarchy

This table shows the raw structural data read directly from the ODB++ `stephdr` files. It defines how smaller steps (like a `unit`) are tiled to create larger steps (like a `cluster` or `panel`).

```
Anatomy of a Step-Repeat:

  Parent Step (e.g., 'cluster')
  +---------------------------------+
  |                                 |
  |  (Origin X, Origin Y)           |
  |  +--> +-------+                 |
  |       | Child | --Pitch X-- ... |
  |       | Step  |                 |
  |       | (unit)|                 |
  |       +-------+                 |
  |          |                      |
  |      Pitch Y                    |
  |          |                      |
  |       +-------+                 |
  |       | Child |                 |
  |       | Step  |                 |
  |       +-------+                 |
  |                                 |
  +---------------------------------+
```

- **Parent/Child Step**: Defines the relationship (e.g., a `panel` is the parent of a `qtr_panel`).
- **Origin X/Y**: The starting position of the *first* child, relative to the parent's origin.
- **Repeat X/Y (nx, ny)**: The number of times the child is repeated in each direction.
- **Pitch X/Y**: The distance from the start of one child to the start of the next.

> **Note:** For InCAM Pro exports, these coordinates are often in **inches**. The app auto-detects this and converts them to millimeters.

---

## 3. Derived Gaps

This table uses the hierarchy data to calculate the physical space between repeated elements.

**Formula:** `Gap = Pitch - Unit Size`

-   At the `unit` level, this shows the **inter-unit gap** (the space between two adjacent PCBs).
-   At the `cluster` level, this shows the **inter-cluster gap** (the larger space between groups of units).

---

## 4. Unit Coordinates

This is the most important table for debugging alignment. It shows the **final, calculated, absolute position** of each unit on the panel.

-   **Coordinate System**: Panel Display Space. The origin `(0,0)` is the absolute bottom-left corner of the panel frame.
-   **What it represents**: The `X mm (left edge)` and `Y mm (bottom edge)` values are the physical locations where each unit's profile begins.

### How are these coordinates calculated?

The application performs a crucial **centering** operation to convert the ODB++ "raw" coordinates into "display" coordinates that match the physical world.

1.  **Find Content Bounds**: The app walks the step-repeat hierarchy to find the `raw_min_x` and `raw_max_x` of the entire block of units. This gives the `content_width`.
2.  **Calculate Margin**: It calculates the empty space on the panel: `margin = (panel_width - content_width) / 2`.
3.  **Apply Shift**: It applies a final shift to all raw positions: `final_x = raw_x + margin - raw_min_x`.

This process ensures that the entire grid of PCBs is perfectly centered on the panel frame, matching how it's physically manufactured and inspected.

### Example Calculation Walkthrough: Deriving Total Content Size

This section provides a detailed breakdown of how the application calculates the `Total Content Width` and `Total Content Height` by using the raw data from the **Step-Repeat Hierarchy** table.

#### Step 1: Calculate Total Content Width (`448.004 mm`)

The goal is to find the total horizontal distance from the left edge of the very first unit to the right edge of the very last unit.

1.  **Find the number of gaps and the pitch between units.**
    We look at the `cluster → UNIT` row in the **Step-Repeat Hierarchy** table:

| Parent Step | Child Step | ... | Repeat X (nx) | ... | Pitch X (mm) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `cluster` | `UNIT` | ... | **6** | ... | **34.3008** |

    -   `Repeat X (nx) = 6` tells us there are **6 units** arranged horizontally inside each `cluster`.
    -   The number of gaps between 6 units is `6 - 1 = 5`. **This is where the `5` comes from.**
    -   `Pitch X (mm) = 34.3008` is the distance between the start of one unit and the start of the next.
    -   So, the span of the units inside one quadrant's worth of columns is `5 gaps × 34.3008 mm/gap = 171.504 mm`.

2.  **Find the large gap between the left and right halves of the panel.**
    We look at the `panel → QTR_PANEL` row:

| Parent Step | Child Step | ... | Pitch X (mm) |
| :--- | :--- | :--- | :--- |
| `panel` | `QTR_PANEL` | ... | **243.0** |

    -   `Pitch X (mm) = 243.0` is the large gap between the left-side quadrants and the right-side quadrants.

3.  **Combine the spans and add the final unit's width.**
    -   `Span of origins = (span of units in left half) + (middle gap) = 171.504 + 243.0 = 414.504 mm`. This is the distance from the origin of the first unit to the origin of the last unit.
    -   To get the total width of the *content*, we must add the physical width of the last unit itself.
    -   `Total Content Width = 414.504 mm + 33.500 mm (Unit Width) = **448.004 mm**`.

#### Step 2: Calculate Total Content Height (`469.801 mm`)

The same logic applies vertically.

1.  **Find the spans between clusters and units.**
    We look at two rows in the hierarchy:

| Parent Step | Child Step | ... | Repeat Y (ny) | ... | Pitch Y (mm) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `qtr_panel` | `CLUSTER` | ... | **3** | ... | **79.0** |
| `cluster` | `UNIT` | ... | **2** | ... | **34.3008** |

    -   `qtr_panel → CLUSTER`: `Repeat Y (ny) = 3` means there are 3 clusters vertically in a quadrant, so there are `3 - 1 = 2` gaps between them. **This is where the `2` comes from.** The pitch is `79.0 mm`.
    -   `cluster → UNIT`: `Repeat Y (ny) = 2` means there are 2 units vertically in a cluster, so there is `2 - 1 = 1` gap. The pitch is `34.3008 mm`.
    -   The total span of origins within one vertical half of the panel is `(2 gaps × 79.0 mm) + (1 gap × 34.3008 mm) = 158.0 + 34.3008 = 192.3008 mm`.

2.  **Find the large gap between the top and bottom halves.**
    We look at the `panel → QTR_PANEL` row:

| Parent Step | Child Step | ... | Pitch Y (mm) |
| :--- | :--- | :--- | :--- |
| `panel` | `QTR_PANEL` | ... | **244.0** |

    -   `Pitch Y (mm) = 244.0` is the large gap between the bottom half and the top half.

3.  **Combine and add the final unit's height.**
    -   `Span of origins = (span of units in bottom half) + (middle gap) = 192.3008 + 244.0 = 436.3008 mm`.
    -   `Total Content Height = 436.3008 mm + 33.500 mm (Unit Height) = **469.8008 mm**` (which rounds to `469.801`).

#### Step 3: Calculate the Centering Margin

Now that we have the total size of the content block, we can center it on the physical panel to find the starting position of the first unit.

- **Left Margin (X-axis)**: `(Panel Width - Total Content Width) / 2 = (510.0 - 448.004) / 2 = **30.998 mm**`
- **Bottom Margin (Y-axis)**: `(Panel Height - Total Content Height) / 2 = (515.0 - 469.801) / 2 = **22.5995 mm**` (rounds to `22.6`)

This is how the application determines that the very first unit `(0,0)` starts at `X=30.998, Y=22.6`.

---

## A Note on Step Origins (Center vs. Corner)

A common question is: "What if the CAM engineer used different origin points for different steps? For example, the `unit` origin is at its center, but the `cluster` origin is at its lower-left corner."

**This does not affect the accuracy of the final calculation.**

The application's logic is robust to this for the following reason:

1.  **Relative Offsets**: The ODB++ `stephdr` files store the *relative distance* from a parent step's origin to a child step's origin (`sr.x`, `sr.y`). It does not matter where those origins are conceptually located.
2.  **Recursive Calculation**: The `compute_unit_positions` function recursively walks the hierarchy. It starts at `(0,0)` and correctly adds up all the relative offsets defined in the `stephdr` files.
3.  **Correct Final Positions**: As long as the CAM designer correctly specified the distance between origins in their CAM tool, the final list of "raw" unit positions will be mathematically correct before the final centering logic is applied.

This design makes the application independent of the CAM engineer's specific workflow or style for placing step origins.

---

## 5. AOI ↔ Unit Coordinate Verification

This interactive tool lets you confirm that your real AOI data aligns with the calculated unit positions from the ODB++.

### How to Use It

1.  Select a `Unit Row` and `Unit Col` from the dropdowns.
2.  The table shows the first few defects from your AOI file that belong to that unit.
3.  It then performs the core alignment calculation:
    -   `X_MM - unit_origin_X`
    -   `Y_MM - unit_origin_Y`

### Interpreting the Results

-   **`X_MM - unit_origin_X`**: This is the defect's local coordinate within the unit. It should be a value between `0` and the `Unit Width (profile)`.
-   **`In range X?` / `In range Y?`**: These columns show a ✅ or ❌.

-   **✅ All Green Checks**: Your alignment is perfect! The AOI machine's coordinate system matches the ODB++ data's physical layout.
-   **❌ Red Crosses**: Your alignment is off. If the `X_MM - unit_origin_X` values are consistently negative or consistently larger than the unit width, it means there is a constant offset. This is the source of the `3.5mm` offset you observed. It indicates a mismatch between the panel size/margins defined in the ODB++ file and the physical reality of the AOI machine's reference frame. You can correct this using the **X/Y Offset** boxes in the sidebar.