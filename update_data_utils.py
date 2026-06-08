import sys

with open('views/unit_commonality.py', 'r') as f:
    views_code = f.read()

# Extract _align_defects
align_start = views_code.find("def _align_defects(x_mm, y_mm, ox_arr, oy_arr, off_x, off_y):")
align_end = views_code.find("def _compute_pad_fingerprint(")
align_code = views_code[align_start:align_end]

# Extract _compute_pad_fingerprint
finger_start = views_code.find("def _compute_pad_fingerprint(")
finger_end = views_code.find("import numpy as np")
finger_code = views_code[finger_start:finger_end]

with open('core/data_utils.py', 'a') as f:
    f.write("\n\n" + align_code + "\n" + finger_code)

# Remove them from views_code
views_code = views_code.replace(align_code, "")
views_code = views_code.replace(finger_code, "")

with open('views/unit_commonality.py', 'w') as f:
    f.write(views_code)
