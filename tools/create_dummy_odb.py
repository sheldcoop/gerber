import os
import tarfile
import shutil
import math

def create_complex_dummy():
    base_dir = "test_2F_odb"
    
    if os.path.exists(base_dir):
        shutil.rmtree(base_dir)
        
    os.makedirs(os.path.join(base_dir, "misc"))
    os.makedirs(os.path.join(base_dir, "matrix"))
    os.makedirs(os.path.join(base_dir, "steps", "test_step", "layers", "2F"))
    os.makedirs(os.path.join(base_dir, "steps", "test_step", "layers", "2B"))

    with open(os.path.join(base_dir, "misc", "info"), "w") as f:
        f.write("UNITS=MM\n")
        
    with open(os.path.join(base_dir, "misc", "attrlist"), "w") as f:
        f.write(".origin_x=0.0\n.origin_y=0.0\n")

    with open(os.path.join(base_dir, "matrix", "matrix"), "w") as f:
        f.write("""MATRIX {
  STEP {
    NAME=test_step
  }
  LAYER {
    ROW=1
    TYPE=SIGNAL
    NAME=2F
    POLARITY=POSITIVE
  }
  LAYER {
    ROW=2
    TYPE=SIGNAL
    NAME=2B
    POLARITY=POSITIVE
  }
}
""")

    # Profile: 50x50 with chamfered corners
    with open(os.path.join(base_dir, "steps", "test_step", "profile"), "w") as f:
        f.write("""$1 r0.1
L 5 0 45 0 1 P
L 45 0 50 5 1 P
L 50 5 50 45 1 P
L 50 45 45 50 1 P
L 45 50 5 50 1 P
L 5 50 0 45 1 P
L 0 45 0 5 1 P
L 0 5 5 0 1 P
""")

    features = []
    # Symbol table
    features.append("$1 r0.4")           # BGA pad
    features.append("$2 rect1.0x2.0")    # Top/Bottom perimeter pad
    features.append("$3 rect2.0x1.0")    # Left/Right perimeter pad
    features.append("$4 r0.05")          # Thin traces
    features.append("$5 r0.1")           # Thick bus traces
    features.append("$6 donut_r1.0x0.5") # Fiducials

    # Dimensions
    cx, cy = 25.0, 25.0
    grid_size = 14
    pitch = 1.0

    bga_points = []
    start_x = cx - (grid_size - 1) * pitch / 2
    start_y = cy - (grid_size - 1) * pitch / 2

    for r in range(grid_size):
        for c in range(grid_size):
            # Create a hollow core (die center)
            if 4 <= r <= 9 and 4 <= c <= 9:
                continue
            bx = start_x + c * pitch
            by = start_y + r * pitch
            bga_points.append((bx, by))
            
            # Place BGA Pad
            features.append(f"P {bx:.3f} {by:.3f} 1 0 0 P")

    # Route BGA to perimeter
    for i, (bx, by) in enumerate(bga_points):
        angle = math.atan2(by - cy, bx - cx)
        
        # Route outwards radially
        dist = 20.0
        # Add slight wobble for realism
        wobble = math.sin(i) * 0.5
        px = cx + (dist + wobble) * math.cos(angle)
        py = cy + (dist + wobble) * math.sin(angle)
        
        # Add a kink in the trace
        kx = bx + (px - bx) * 0.5
        ky = by + (py - by) * 0.5 + (0.5 if i % 2 == 0 else -0.5)

        # Draw trace segments
        features.append(f"L {bx:.3f} {by:.3f} {kx:.3f} {ky:.3f} 4 P")
        features.append(f"L {kx:.3f} {ky:.3f} {px:.3f} {py:.3f} 4 P")
        
        # Place perimeter pad based on angle
        if abs(math.cos(angle)) > abs(math.sin(angle)):
            features.append(f"P {px:.3f} {py:.3f} 3 0 0 P")
        else:
            features.append(f"P {px:.3f} {py:.3f} 2 0 0 P")

    # Add corner fiducials (.fiducial=1 attribute)
    features.append("P 5.000 5.000 6 0 0 P ;.fiducial=1")
    features.append("P 45.000 45.000 6 0 0 P ;.fiducial=1")
    features.append("P 5.000 45.000 6 0 0 P ;.fiducial=1")
    features.append("P 45.000 5.000 6 0 0 P ;.fiducial=1")

    # Write 2F features
    with open(os.path.join(base_dir, "steps", "test_step", "layers", "2F", "features"), "w") as f:
        f.write("\n".join(features))

    # Features for 2B (flipped mirror logic)
    features_2b = []
    features_2b.extend(features[:6]) # Copy symbol table
    # Mirror the X-coordinates of BGA and Traces to create orthogonal routing
    for line in features[6:]:
        parts = line.split()
        if parts[0] == "P": # Pad
            x = 50.0 - float(parts[1])
            features_2b.append(f"P {x:.3f} {parts[2]} {parts[3]} 0 0 P")
        elif parts[0] == "L": # Line
            x1 = 50.0 - float(parts[1])
            x2 = 50.0 - float(parts[3])
            features_2b.append(f"L {x1:.3f} {parts[2]} {x2:.3f} {parts[4]} {parts[5]} P")
        else:
            features_2b.append(line)
            
    with open(os.path.join(base_dir, "steps", "test_step", "layers", "2B", "features"), "w") as f:
        f.write("\n".join(features_2b))

    tar_name = "test_2F.tgz"
    with tarfile.open(tar_name, "w:gz") as tar:
        tar.add(base_dir, arcname=os.path.basename(base_dir))
    print(f"Created {tar_name} successfully!")

if __name__ == '__main__':
    create_complex_dummy()
