# Gerber ODB++ + AOI Overlay Visualization

This project is a Streamlit-based web application for visualizing ODB++ (Open Database) PCB design files overlaid with Automated Optical Inspection (AOI) defect data. It enables engineers to inspect PCB manufacturing defects by aligning and overlaying AOI defect coordinates onto rendered PCB layers.

## Project Structure

### Root Files

- **app.py**: Main Streamlit application entry point. Orchestrates file uploads, ODB++ parsing, AOI data loading, coordinate alignment, and interactive visualization with Plotly.

- **alignment.py**: Handles coordinate alignment between ODB++ Gerber layers and AOI defect data. Includes functions for computing transformations, detecting fiducials, and applying alignments.

- **aoi_loader.py**: Loads and processes AOI defect data from Excel files. Parses defect coordinates, types, and metadata for overlay visualization.

- **clustering.py**: Implements clustering algorithms for grouping similar defects or components, useful for defect analysis and triage.

- **export.py**: Provides functionality for exporting visualization results, processed data, or reports in various formats.

- **gerber_renderer.py**: Core rendering engine for converting ODB++ Gerber files into SVG visualizations. Handles layer parsing, caching, and CAM-quality rendering.

- **odb_parser.py**: Parses ODB++ archive files, extracting layer geometries, symbols, and metadata from the structured format.

- **requirements.txt**: Python dependencies required for the project, including Streamlit, Plotly, pandas, Shapely, and testing libraries.

- **scoring.py**: Implements scoring algorithms for defect severity, commonality analysis, or quality metrics.

- **visualizer.py**: Contains visualization utilities and helpers for rendering PCB layers and defect overlays.

### Core Modules (`core/`)

- **cache.py**: Implements caching mechanisms for rendered SVGs and parsed data to improve performance.

- **data_utils.py**: Utility functions for data manipulation, transformation, and processing across the application.

- **layer_renderer.py**: Handles rendering of individual PCB layers using the Gerbonara library.

- **panel_builder.py**: Constructs panel-level SVG representations from individual step layouts.

- **pipeline.py**: Main rendering pipeline that orchestrates ODB++ parsing, layer rendering, and SVG generation.

- **state.py**: Manages Streamlit session state and application state synchronization.

- **step_layout.py**: Computes unit positions and layouts within PCB steps/panels.

### ODB++ Parsing (`odb/`)

- **__init__.py**: Package initialization for the ODB parsing module.

- **archive.py**: Handles extraction and management of ODB++ tar.gz archives.

- **constants.py**: Defines constants used in ODB++ parsing and processing.

- **features.py**: Parses feature files containing geometric data from ODB++ layers.

- **geometry.py**: Processes geometric shapes and transformations from ODB++ data.

- **layout.py**: Manages layout information and step definitions from ODB++ files.

- **models.py**: Data classes and models representing ODB++ structures (layers, symbols, components).

- **symbols.py**: Handles parsing and processing of aperture/symbol definitions.

### User Interface (`ui/`)

- **sidebar.py**: Implements the Streamlit sidebar with controls for file uploads, layer selection, alignment settings, and rendering options.

### Views (`views/`)

- **cluster_triage.py**: View for defect clustering and triage interface.

- **panel_heatmap.py**: Heatmap visualization of defects across PCB panels.

- **panel_overview.py**: Overview visualization of entire PCB panels with defect overlays.

- **panelization_data.py**: Displays and analyzes panelization data and statistics.

- **panelization_data_explained.md**: Documentation explaining panelization concepts and data structures.

- **unit_commonality.py**: Analyzes commonality between PCB units for defect pattern recognition.

### Services (`services/`)

*(Directory appears empty in current structure - likely for future service integrations)*

### Tests (`tests/`)

- **__init__.py**: Test package initialization.

- **conftest.py**: Pytest configuration and fixtures.

- **test_alignment.py**: Unit tests for coordinate alignment functionality.

- **test_aoi_loader.py**: Tests for AOI data loading and processing.

- **test_clustering.py**: Tests for defect clustering algorithms.

- **test_export.py**: Tests for data export functionality.

- **test_scoring.py**: Tests for defect scoring algorithms.

- **test_visualizer.py**: Tests for visualization components.

### Tools (`tools/`)

- **benchmark.py**: Performance benchmarking utilities.

- **create_dummy_aoi_files.py**: Generates dummy AOI defect data files for testing.

- **create_dummy_heatmap_data.py**: Creates sample heatmap data for visualization testing.

- **create_dummy_odb.py**: Generates dummy ODB++ archives for development and testing.

- **create_realistic_dummy.py**: Creates more realistic dummy data for testing scenarios.

- **create_sample_dataset.py**: Generates sample datasets for demonstration purposes.

- **create_test_svg.py**: Creates test SVG files for rendering validation.

- **debug_coords.py**: Debugging utilities for coordinate systems and transformations.

- **inspect_excel.py**: Tools for inspecting and analyzing Excel AOI files.

### Documentation (`docs/`)

- **coordinate_systems.md**: Documentation on coordinate systems used in PCB design and AOI data.

- **odb_rendering_bug_fixes.md**: Notes on bug fixes and improvements in ODB++ rendering.

### Dummy Data (`dummy_data/`)

- **BU-01/**, **BU-02/**, **BU-03/**: Sample directories containing dummy AOI defect data for different board units.

## Getting Started

1. Install dependencies: `pip install -r requirements.txt`
2. Run the application: `streamlit run app.py`
3. Upload an ODB++ archive (.tgz) and corresponding AOI Excel files
4. Use the sidebar controls to adjust alignment, layer visibility, and visualization settings

## Key Features

- **ODB++ Parsing**: Full support for ODB++ v7+ archives with layer geometry extraction
- **AOI Integration**: Loads defect data from Excel files with automatic coordinate alignment
- **Interactive Visualization**: Plotly-based overlays with zoom, pan, and layer toggling
- **Alignment Algorithms**: Automatic and manual alignment between design and inspection coordinates
- **Caching**: Efficient rendering cache for improved performance with large files
- **Export Capabilities**: Export visualizations and processed data
- **Testing Suite**: Comprehensive unit tests for all major components

---

## ODB++ Step-Repeat: NX, NY and Where to Find Them

### What are NX and NY?

Every STEP-REPEAT block in an ODB++ `stephdr` file has two repeat-count fields:

| Field | Meaning |
|---|---|
| `NX` | How many times the child step is repeated along the **X axis** |
| `NY` | How many times the child step is repeated along the **Y axis** |
| `DX` | Spacing between repeats in X (in the file's native units) |
| `DY` | Spacing between repeats in Y |

A single STEP-REPEAT with `NX=6, NY=2, DX=1.35, DY=1.35` places 12 child units in a 6×2 grid. If NX=1 and NY=1 with DX=DY=0, it places exactly one child at position (X, Y).

### Where to find them in the archive

Inside the `.tgz` archive, every step level has a `stephdr` file:

```
<job_name>/
  steps/
    unit/stephdr        ← leaf unit (usually no STEP-REPEAT here)
    cluster/stephdr     ← places UNIT children  ← NX/NY for unit grid
    qtr_panel/stephdr   ← places CLUSTER children
    panel/stephdr       ← places QTR_PANEL children  ← top-level grid
```

Example from a working design (`cluster/stephdr`):
```
STEP-REPEAT {
    NAME=UNIT
    X=1.151496062992126
    Y=0.8208503937007874
    DX=1.350425196850394    ← pitch between units in X
    DY=1.350425196850394    ← pitch between units in Y
    NX=6                    ← 6 columns
    NY=2                    ← 2 rows
    ANGLE=0
    MIRROR=NO
}
```

### Two encoding styles InCAM Pro uses — and why it matters

When `ANGLE=0`, InCAM Pro writes a **compact grid**: one STEP-REPEAT with NX/NY > 1 and non-zero DX/DY.

When `ANGLE ≠ 0` (e.g. 270°), InCAM Pro writes **individual placements**: one STEP-REPEAT per unit, each with NX=1, NY=1, DX=0, DY=0, because each may have its own rotation.

```
# ANGLE=270 design — 5 separate entries instead of one NX=5 entry:
STEP-REPEAT { NAME=UNIT  X=1.5078  Y=1.4961  DX=0  DY=0  NX=1  NY=1  ANGLE=270 }
STEP-REPEAT { NAME=UNIT  X=3.0177  Y=1.4961  DX=0  DY=0  NX=1  NY=1  ANGLE=270 }
STEP-REPEAT { NAME=UNIT  X=4.5276  Y=1.4961  DX=0  DY=0  NX=1  NY=1  ANGLE=270 }
...
```

This encoding difference was the root cause of the unit coordinate bug described below.

---

## Unit Coordinate Fix — InCAM Pro Undeclared Inches + ANGLE=270

### The problem

InCAM Pro (v6.01SP2) saves ODB++ archives with coordinates in **inches** but does **not** write a `UNITS=INCH` line in `misc/info`. The app must auto-detect the unit system.

The existing detection worked for ANGLE=0 designs (compact NX/NY grids produce non-zero DX/DY values that trigger the inch heuristic), but **failed for ANGLE=270 designs** where all DX/DY are zero at the cluster and qtr_panel levels — leaving the step-repeat hierarchy in raw inch values while the profile parser correctly returned mm dimensions. The mismatch crammed all 60 units into a ~16mm blob at the centre of a 510mm panel.

### The fix (committed in `core/pipeline.py` and `core/step_layout.py`)

**1. Post-profile scale check** (`core/pipeline.py`):

After the unit profile parser correctly derives `unit_w` in mm, a second inch-detection pass checks whether the maximum absolute coordinate in the step_hierarchy is smaller than `unit_w`. If it is, the entire hierarchy is still in inch scale and is re-parsed with `uf=25.4`.

```
if max_SR_coord < unit_w_mm  →  re-parse step_hierarchy with uf=25.4
```

| Design | max SR coord | unit_w | Triggers? |
|---|---|---|---|
| fhr0010 (ANGLE=0, already re-parsed) | 243 mm | 33.5 mm | No — already correct |
| fhr0020 (ANGLE=270, not yet re-parsed) | 9.606 (inch) | 43.5 mm | **Yes** → re-parses ✓ |

**2. ANGLE-aware bounding box** (`core/step_layout.py`):

`_expand()` now tracks cumulative rotation through the hierarchy. When the dominant leaf angle is 90° or 270°, `unit_width` and `unit_height` are swapped before computing the content bounding box used for panel centering — because a 270°-rotated unit occupies its height in X and its width in Y.

### How to verify it works

Load either design and check the **Step-Repeat Hierarchy** table in the app:
- All Origin and Pitch values should be in **mm scale** (e.g. −115 mm, 243 mm, 79 mm)
- Unit count, rows, and cols must be correct (e.g. 60 units, 6 rows × 10 cols)
- The warning log should contain: `Step-repeat inch quirk (post-profile): max SR coord X.XXX < unit_w XX.XX mm`
- Exported unit coordinates CSV: column X values must span the full panel width (e.g. 35 mm → 475 mm), not be bunched in a narrow band (e.g. 225–284 mm)

## Architecture

The application follows a modular architecture:

1. **Data Ingestion**: ODB++ archives and AOI Excel files are uploaded via Streamlit
2. **Parsing Pipeline**: ODB++ files are extracted and parsed into geometric data structures
3. **Alignment**: Coordinate systems are aligned using fiducial detection and transformation matrices
4. **Rendering**: Layers are rendered as SVGs with defect overlays
5. **Visualization**: Interactive Plotly charts display the results with user controls

## Development

- Use the `tools/` directory scripts to generate test data
- Run tests with `pytest`
- Follow the existing code structure for new features
- Cache rendered results to improve development iteration speed