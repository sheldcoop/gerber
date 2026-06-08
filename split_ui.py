with open('views/unit_commonality.py', 'r') as f:
    code = f.read()

# Add imports at the top
import_block = """import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from core.data_utils import compute_cm_geometry, filter_aoi_cm, _align_defects, _compute_pad_fingerprint
from core.svg_utils import build_rotated_svg_url
"""

# Remove old imports inside render_unit_commonality that we moved
code = code.replace("import numpy as np", "")
code = code.replace("import pandas as pd", "")

# The file starts with:
# from core.data_utils import compute_cm_geometry, filter_aoi_cm
# Let's replace the first few lines up to def render_unit_commonality
idx_render = code.find("def render_unit_commonality")
code = import_block + "\n" + code[idx_render:]

# In render_unit_commonality, we need to replace the local `_build_layer_url` with `build_rotated_svg_url`
target_build_url_start = "    def _build_layer_url(lyr_obj, rot, is_multi=False):"
target_build_url_end = "    _rodb_cm_check = st.session_state.get('rendered_odb')"
idx_build_start = code.find(target_build_url_start)
idx_build_end = code.find(target_build_url_end)

if idx_build_start != -1 and idx_build_end != -1:
    code = code[:idx_build_start] + code[idx_build_end:]

# Replace _build_layer_url calls with build_rotated_svg_url
code = code.replace("_build_layer_url", "build_rotated_svg_url")

# Replace _align_defects and _compute_pad_fingerprint calls if any
# They are already matching names

with open('views/unit_commonality.py', 'w') as f:
    f.write(code)
