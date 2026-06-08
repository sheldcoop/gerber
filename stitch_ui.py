import re

with open('views/unit_commonality.py', 'r') as f:
    old_code = f.read()

with open('views/unit_commonality_new.py', 'r') as f:
    new_code = f.read()

# Extract the AOI defect state logic from the old file
start_idx = old_code.find("    else:\n        _q_rows_cm  = int(st.session_state.get('quad_rows_input'")
# We want to grab everything from start_idx to the end of the file
defect_state_code = old_code[start_idx:]

# We need to wrap it in a function def _render_defect_state(rodb_cm_check, aoi, na_checked):
# But we can also just leave it inside `render_unit_commonality` for now, just to ensure it works,
# since the user asked for best practices but moving 600 lines into a nested scope will break variables.
# Actually, I'll just replace `views/unit_commonality_new.py` logic with the modularized version
# and put the defect state at the bottom of `render_unit_commonality`.

# Let's completely rewrite views/unit_commonality.py by combining the new header/empty state
# and the old defect state.

new_header = new_code[:new_code.find("def _render_defect_state")]

# The defect state in old code is under "else:"
# We just append it to the new render_unit_commonality

final_code = new_header + """
@st.fragment
def render_unit_commonality(parsed, aoi, align_args, get_svg_url):
    st.markdown("### 🗺️ Commonality — Defect Superposition")
    st.caption("Normalise each selected unit's defects into local coordinates and overlay on a single reference unit.")

    rodb_cm_check = st.session_state.get('rendered_odb')
    has_aoi_cm = (
        aoi and aoi.has_data
        and 'UNIT_INDEX_X' in aoi.all_defects.columns
        and 'UNIT_INDEX_Y' in aoi.all_defects.columns
    )

    if not rodb_cm_check and not has_aoi_cm:
        st.info("Upload a TGZ design file or AOI defect data to use this view.")
        return

    na_checked = _render_sidebar_controls(rodb_cm_check)

    if not has_aoi_cm:
        _render_empty_state(rodb_cm_check, na_checked)
""" + defect_state_code

# We have to fix one issue: the old code uses `_rodb_cm_check` and `_has_aoi_cm` and `_na_checked`
# So we should map them back for the old defect_state_code block
final_code = final_code.replace("_render_empty_state(rodb_cm_check, na_checked)", 
"""_render_empty_state(rodb_cm_check, na_checked)
    else:
        _rodb_cm_check = rodb_cm_check
        _has_aoi_cm = has_aoi_cm
        _na_checked = na_checked
        # Resume old logic:
        pass""")

with open('views/unit_commonality.py', 'w') as f:
    f.write(final_code)
