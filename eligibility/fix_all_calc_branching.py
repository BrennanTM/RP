import os
import sys
sys.path.append('/home/tristan8/stanford_redcap')
os.chdir('/home/tristan8/stanford_redcap')

from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import requests
import json
from datetime import datetime

API_TOKEN = os.getenv('REDCAP_API_TOKEN')
API_URL = "https://redcap.stanford.edu/api/"

# First, download the current data dictionary
print("Downloading current data dictionary...")
response = requests.post(API_URL, data={
    'token': API_TOKEN,
    'content': 'metadata',
    'format': 'csv'
})

if response.status_code == 200:
    # Save original
    with open('data_dictionary_original.csv', 'w') as f:
        f.write(response.text)
    
    # Load into pandas
    df = pd.read_csv('data_dictionary_original.csv')
    
    # Check column names
    print("\nColumn names in data dictionary:")
    for col in df.columns:
        print(f"  - {col}")
    
    # Find the correct column names (REDCap uses different format)
    field_type_col = None
    branching_col = None
    
    for col in df.columns:
        if 'field' in col.lower() and 'type' in col.lower():
            field_type_col = col
        if 'branching' in col.lower():
            branching_col = col
    
    print(f"\nUsing columns:")
    print(f"  Field Type: {field_type_col}")
    print(f"  Branching: {branching_col}")
    
    if field_type_col and branching_col:
        # Find calculated fields with branching logic
        calc_with_branching = df[(df[field_type_col] == 'calc') & (df[branching_col].notna()) & (df[branching_col] != '')]
        
        print(f"\nFound {len(calc_with_branching)} calculated fields with branching logic")
        
        # Remove branching logic from calculated fields
        df.loc[(df[field_type_col] == 'calc'), branching_col] = ''
        
        # Save the fixed version
        df.to_csv('data_dictionary_FIXED_no_calc_branching.csv', index=False)
        
        print("\n✓ Created fixed data dictionary: data_dictionary_FIXED_no_calc_branching.csv")
        print("\nRemoved branching logic from these calculated fields:")
        
        field_name_col = df.columns[0]  # Usually first column
        for field in calc_with_branching[field_name_col].tolist():
            print(f"  - {field}")
        
        print(f"\n📋 Next steps:")
        print("1. Download the file: data_dictionary_FIXED_no_calc_branching.csv")
        print("2. Go to REDCap → Project Setup → Data Dictionary")
        print("3. Upload the fixed CSV file")
        print("4. Review and commit the changes")
    else:
        print("\nError: Could not find required columns")
    
else:
    print(f"Error downloading data dictionary: {response.status_code}")
