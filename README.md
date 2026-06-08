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