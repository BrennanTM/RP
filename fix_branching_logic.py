import pandas as pd
import re

# Read the dictionary (from home directory)
df = pd.read_csv('~/merged_dictionary_fixed.csv')

# Get all actual field names
actual_fields = set(df['Variable / Field Name'].dropna())

# Find all field references in branching logic
branching_col = 'Branching Logic (Show field only if...)'
all_branching = ' '.join(df[branching_col].fillna('').astype(str))
field_refs = re.findall(r'\[([a-zA-Z0-9_]+)\]', all_branching)
unique_refs = set(field_refs)

# Find missing fields
missing_fields = unique_refs - actual_fields
print(f"Missing fields referenced in branching logic: {len(missing_fields)}")

if missing_fields:
    for field in sorted(missing_fields):
        print(f"  - {field}")
else:
    print("✓ All fields exist - ready to upload!")
