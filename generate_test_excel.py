import pandas as pd
import numpy as np

# We'll generate a grid of defects that simulates a repeating fault
# across many units. The user's units are spaced ~38.35 mm in X and Y.
# Let's say there are 6 rows and 10 cols.
# We'll start X at ~50 and Y at ~50.

data = []
defect_types = ['Short', 'Open', 'Pin Hole']
verifications = ['CU22', 'SH', 'OP']

for row in range(6):
    for col in range(10):
        # Base unit origin
        unit_x = 50 + col * 38.35
        unit_y = 50 + row * 38.35
        
        # 1. A highly systematic defect (Short) appearing in ALMOST every unit 
        # at exactly the same local offset (12.5 mm, 18.2 mm)
        if np.random.rand() > 0.1:  # 90% chance to appear
            data.append({
                'DEFECT_TYPE': 'Short',
                'X_COORDINATES': unit_x + 12.5,
                'Y_COORDINATES': unit_y + 18.2,
                'VERIFICATION': 'SH',
                'UNIT_INDEX_X': col,
                'UNIT_INDEX_Y': row,
            })
            
        # 2. A somewhat systematic defect (Open) appearing in half the units
        # at local offset (5.1 mm, 30.5 mm)
        if np.random.rand() > 0.5:
            data.append({
                'DEFECT_TYPE': 'Open',
                'X_COORDINATES': unit_x + 5.1,
                'Y_COORDINATES': unit_y + 30.5,
                'VERIFICATION': 'OP',
                'UNIT_INDEX_X': col,
                'UNIT_INDEX_Y': row,
            })
            
        # 3. Some random noise defects
        for _ in range(np.random.randint(0, 3)):
            data.append({
                'DEFECT_TYPE': np.random.choice(defect_types),
                'X_COORDINATES': unit_x + np.random.uniform(2, 36),
                'Y_COORDINATES': unit_y + np.random.uniform(2, 36),
                'VERIFICATION': np.random.choice(verifications),
                'UNIT_INDEX_X': col,
                'UNIT_INDEX_Y': row,
            })

df = pd.DataFrame(data)
# Save as BU-02F.xlsx so it maps to buildup 2, side Front automatically
df.to_excel('test_commonality_BU-02F.xlsx', index=False)
print("Created test_commonality_BU-02F.xlsx with", len(df), "defects.")
