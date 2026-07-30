import streamlit as st

def init_state():
    """Initialize session state with defaults."""
    defaults = {
        'parsed_odb': None,          # ParsedODB
        'aoi_dataset': None,         # AOIDataset
        'data_loaded': False,
        'align_args': {},            # Reset on each load to prevent stale offsets
        'needs_manual_side': {},     # filename → True if BU/side not detected
        'rendered_odb': None,        # RenderedODB (Gerbonara CAM SVGs)
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val
