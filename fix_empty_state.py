import re

with open('views/unit_commonality.py', 'r') as f:
    code = f.read()

# I need to pull out _build_layer_url so it's accessible to the empty state block
# It's currently at line 584: def _build_layer_url(lyr_obj, rot):

# Let's extract _build_layer_url from its current position
target_build_url_start = "                    def _build_layer_url(lyr_obj, rot):"
target_build_url_end = "                    for _cm_cam_ln, _cm_cam_lyr in _cm_cam_pairs:"

idx_start = code.find(target_build_url_start)
idx_end = code.find(target_build_url_end)

build_url_code = code[idx_start:idx_end]

# Remove it from its current position
code = code.replace(build_url_code, "")

# Dedent the build_url_code so it sits nicely at the top of render_unit_commonality
lines = build_url_code.split('\n')
new_lines = []
for line in lines:
    if line.startswith("                    "):
        new_lines.append(line[16:])
    else:
        new_lines.append(line)
build_url_code_dedented = '\n'.join(new_lines)

# Insert it at the top of render_unit_commonality
insert_idx = code.find("    _rodb_cm_check = st.session_state.get('rendered_odb')")
code = code[:insert_idx] + build_url_code_dedented + "\n" + code[insert_idx:]

# Now replace the empty state logic
target_empty_state = """                _design_fig = go.Figure()
                for _na_n, _na_l in _na_sorted:
                    _lyr_b_na = _na_l.bounds
                    _design_fig.add_layout_image(dict(
                        source=get_svg_url(_na_l),
                        xref="x", yref="y",
                        x=_lyr_b_na[0] + _ref_sx_na,
                        y=_lyr_b_na[3] + _ref_sy_na,
                        sizex=_lyr_b_na[2] - _lyr_b_na[0],
                        sizey=_lyr_b_na[3] - _lyr_b_na[1],
                        sizing="stretch", layer="below",
                        opacity=_layer_opacity(_na_n, _na_l.layer_type, _is_multi_na),
                    ))"""

replacement_empty_state = """                _unit_angle_na = 0.0
                if _rodb_cm_check and _rodb_cm_check.panel_layout:
                    _unit_angle_na = getattr(_rodb_cm_check.panel_layout, 'dominant_angle', 0.0)
                _rot_deg_na = float(round(_unit_angle_na) % 360)

                _design_fig = go.Figure()
                for _na_n, _na_l in _na_sorted:
                    _na_data_url = _build_layer_url(_na_l, _rot_deg_na)
                    
                    _lyr_b_na = _na_l.bounds
                    _im_w_nat = _lyr_b_na[2] - _lyr_b_na[0]
                    _im_h_nat = _lyr_b_na[3] - _lyr_b_na[1]
                    
                    if _rot_deg_na in (90.0, 270.0):
                        _place_sizex = _im_h_nat
                        _place_sizey = _im_w_nat
                        _place_x     = 0.0
                        _place_y     = _im_w_nat
                        _no_aoi_cw, _no_aoi_ch = _no_aoi_ch, _no_aoi_cw
                    else:
                        _place_sizex = _im_w_nat
                        _place_sizey = _im_h_nat
                        _place_x     = _lyr_b_na[0] + _ref_sx_na
                        _place_y     = _lyr_b_na[3] + _ref_sy_na

                    _design_fig.add_layout_image(dict(
                        source=_na_data_url,
                        xref="x", yref="y",
                        x=_place_x, y=_place_y,
                        sizex=_place_sizex, sizey=_place_sizey,
                        sizing="stretch", layer="below",
                        opacity=_layer_opacity(_na_n, _na_l.layer_type, _is_multi_na),
                    ))"""

code = code.replace(target_empty_state, replacement_empty_state)

with open('views/unit_commonality.py', 'w') as f:
    f.write(code)
