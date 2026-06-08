import re
with open('views/unit_commonality.py', 'r') as f:
    code = f.read()

# Fix viewBox replacement
target1 = """                                    # Replace the old viewBox with the new one
                                    svg_start = svg[:tag.end()]
                                    svg_start = _re_svg.sub(r'viewBox=[\\"\\'][^\\"\\']+[\\"\\']', f'viewBox="{new_vb}"', svg_start)
                                    
                                    svg = (
                                        svg_start
                                        + f'<g transform="rotate({rot},{cx:.4f},{cy:.4f})">'
                                        + inner + '</g>' + svg[close:]
                                    )"""

# In current code (amazing-repeatbality), this logic is totally missing!
# So we need to find:
target_to_replace = """                                    svg = (
                                        svg[:tag.end()]
                                        + f'<g transform="rotate({rot},{cx:.4f},{cy:.4f})">'
                                        + inner + '</g>' + svg[close:]
                                    )"""

replacement1 = """                                    rot_norm = round(rot) % 360
                                    new_vb = f"{vx} {vy} {vw} {vh}"
                                    if rot_norm in (90, 270):
                                        new_vx = cx - vh / 2
                                        new_vy = cy - vw / 2
                                        new_vb = f"{new_vx:.4f} {new_vy:.4f} {vh:.4f} {vw:.4f}"
                                    
                                    import re as _re_plain
                                    svg_start = svg[:tag.end()]
                                    svg_start = _re_plain.sub(r'viewBox=[\"\'][^\"\']+[\"\']', f'viewBox="{new_vb}"', svg_start)
                                    
                                    svg = (
                                        svg_start
                                        + f'<g transform="rotate({rot},{cx:.4f},{cy:.4f})">'
                                        + inner + '</g>' + svg[close:]
                                    )"""
code = code.replace(target_to_replace, replacement1)

# Fix Plotly sizing
target2 = """                        _cb_cm = _cm_cam_lyr.bounds
                        _im_x  = _cb_cm[0] + _ref_shift_x
                        _im_y  = _cb_cm[3] + _ref_shift_y
                        _im_w  = _cb_cm[2] - _cb_cm[0]
                        _im_h  = _cb_cm[3] - _cb_cm[1]
                        _cm_fig.add_layout_image(dict(
                            source=_cm_data_url,
                            xref="x", yref="y",
                            x=_im_x, y=_im_y,
                            sizex=_im_w, sizey=_im_h,
                            sizing="stretch", layer="below",
                            opacity=_layer_opacity(_cm_cam_ln, _cm_cam_lyr.layer_type, _is_multi_cm),
                        ))"""

replacement2 = """                        _cb_cm = _cm_cam_lyr.bounds
                        _im_w_nat = _cb_cm[2] - _cb_cm[0]
                        _im_h_nat = _cb_cm[3] - _cb_cm[1]
                        
                        _angle_norm_img = round(_unit_angle_cm) % 360
                        if _angle_norm_img in (90, 270):
                            _place_sizex = _im_h_nat
                            _place_sizey = _im_w_nat
                            _place_x     = 0.0
                            _place_y     = _im_w_nat
                        else:
                            _place_sizex = _im_w_nat
                            _place_sizey = _im_h_nat
                            _place_x     = _cb_cm[0] + _ref_shift_x
                            _place_y     = _cb_cm[3] + _ref_shift_y

                        _cm_fig.add_layout_image(dict(
                            source=_cm_data_url,
                            xref="x", yref="y",
                            x=_place_x, y=_place_y,
                            sizex=_place_sizex, sizey=_place_sizey,
                            sizing="stretch", layer="below",
                            opacity=_layer_opacity(_cm_cam_ln, _cm_cam_lyr.layer_type, _is_multi_cm),
                        ))"""

code = code.replace(target2, replacement2)

with open('views/unit_commonality.py', 'w') as f:
    f.write(code)
