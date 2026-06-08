with open('views/unit_commonality.py', 'r') as f:
    code = f.read()

# Fix the function signature
target_sig = "def _build_layer_url(lyr_obj, rot):"
replacement_sig = "def _build_layer_url(lyr_obj, rot, is_multi=False):"
code = code.replace(target_sig, replacement_sig)

# Replace the reference to _is_multi_cm inside _build_layer_url
target_ref = "if _is_multi_cm and lyr_obj.color_svg_urls:"
replacement_ref = "if is_multi and lyr_obj.color_svg_urls:"
code = code.replace(target_ref, replacement_ref)

# Fix the call in empty state
target_call_na = "_na_data_url = _build_layer_url(_na_l, _rot_deg_na)"
replacement_call_na = "_na_data_url = _build_layer_url(_na_l, _rot_deg_na, _is_multi_na)"
code = code.replace(target_call_na, replacement_call_na)

# Fix the call in AOI state
target_call_cm = "_cm_data_url = _build_layer_url(_cm_cam_lyr, _rot_deg)"
replacement_call_cm = "_cm_data_url = _build_layer_url(_cm_cam_lyr, _rot_deg, _is_multi_cm)"
code = code.replace(target_call_cm, replacement_call_cm)

with open('views/unit_commonality.py', 'w') as f:
    f.write(code)
