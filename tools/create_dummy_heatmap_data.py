import pandas as pd
import numpy as np
import os

def generate_heatmap_data(num_files=20):
    os.makedirs('dummy_data', exist_ok=True)
    # Define some hotspots (x_mm, y_mm, std_mm, defects_per_file)
    hotspots = [
        (45, 120, 1.5, 40), # Very dense sharp cluster
        (150, 45, 5.0, 20), # Wider cluster
        (300, 200, 8.0, 35), # Very wide, diffuse cluster
    ]
    
    for i in range(num_files):
        data = []
        for x, y, std, num in hotspots:
            xs = np.random.normal(x, std, num)
            ys = np.random.normal(y, std, num)
            
            for _x, _y in zip(xs, ys):
                data.append({
                    'DEFECT_ID': np.random.randint(1000, 9999),
                    'DEFECT_TYPE': np.random.choice(['Short', 'Open', 'Nick']),
                    'X_COORDINATES': _x * 1000.0, # convert mm to microns for AOI
                    'Y_COORDINATES': _y * 1000.0,
                    'UNIT_INDEX_X': 0,
                    'UNIT_INDEX_Y': 0,
                })
                
        # Add random scatter
        for _ in range(15):
            data.append({
                'DEFECT_ID': np.random.randint(1000, 9999),
                'DEFECT_TYPE': np.random.choice(['Short', 'Open', 'Nick']),
                'X_COORDINATES': np.random.uniform(0, 400) * 1000.0,
                'Y_COORDINATES': np.random.uniform(0, 300) * 1000.0,
                'UNIT_INDEX_X': 0, 'UNIT_INDEX_Y': 0
            })
            
        df = pd.DataFrame(data)
        # Name convention BU-XXF
        filename = f"dummy_data/Panel_{i}_BU-02F.xlsx"
        df.to_excel(filename, index=False)
        print(f"Generated {filename}")

if __name__ == "__main__":
    generate_heatmap_data()
