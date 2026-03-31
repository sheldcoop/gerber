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

### Example Calculation Walkthrough

Let's trace exactly how the app calculates the display coordinates for five consecutive units in Row 0: `(0,0)` to `(0,4)`.

**Panel & Content Dimensions (from ODB++):**
- Panel Size: `510.0 x 515.0 mm`
- Unit Size: `33.5 x 33.5 mm`
- Unit Pitch X: `34.3008 mm`

**Step 1: Calculate Total Content Width & Height**
First, the app finds the raw distance from the leftmost unit to the rightmost unit, and adds the physical width of one unit to get the total "content block" size.

*   **X-Axis (Width)**:
    *   Left quadrant spans 5 unit pitches: `5 × 34.3008 = 171.504 mm`
    *   Middle gap between quadrants: `243.0 mm`
    *   Span from first to last unit origin: `171.504 + 243.0 = 414.504 mm`
    *   Total Width = Span + Unit Width = `414.504 + 33.500 = 448.004 mm`

*   **Y-Axis (Height)**:
    *   A quadrant spans 2 cluster pitches (`2 × 79.0 = 158.0 mm`) and 1 unit pitch (`34.3008 mm`) = `192.3008 mm`
    *   Middle gap between top and bottom halves: `244.0 mm`
    *   Span from bottom to top unit origin: `192.3008 + 244.0 = 436.3008 mm`
    *   Total Height = Span + Unit Height = `436.3008 + 33.500 = 469.8008 mm` (rounds to `469.801`)

**Step 2: Calculate the Centering Margin**
The app centers the total content block onto the physical panel.

- **Left Margin (X-axis)**: `(510.0 - 448.004) / 2 = 30.998 mm`
- **Bottom Margin (Y-axis)**: `(515.0 - 469.801) / 2 = 22.5995 mm` (rounds to `22.6 mm`)

**Step 3: Calculate the Unit Positions (Row 0, Cols 0 to 4)**
Since all these units are in Row 0, they all sit at the bottom edge. Their Y-coordinate is exactly the Bottom Margin: **`22.6 mm`**.

For the X-coordinates, we start at the Left Margin and add the `34.3008 mm` unit pitch for each subsequent column:

-   **Unit (0, 0)**
    -   `X = Left Margin = 30.998 mm`
-   **Unit (0, 1)**
    -   `X = 30.998 + 34.3008 = 65.2988 mm` (rounds to `65.299`)
-   **Unit (0, 2)**
    -   `X = 65.2988 + 34.3008 = 99.5996 mm` (rounds to `99.600`)
-   **Unit (0, 3)**
    -   `X = 99.5996 + 34.3008 = 133.9004 mm` (rounds to `133.900`)
-   **Unit (0, 4)**
    -   `X = 133.9004 + 34.3008 = 168.2012 mm` (rounds to `168.201`)

These calculated values (`30.998, 65.299, 99.6, 133.9, 168.201`) match the table output exactly. This demonstrates how the app dynamically centers any ODB++ grid layout flawlessly without needing hardcoded offsets.

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