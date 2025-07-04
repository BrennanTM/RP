import os
import sys
sys.path.append('/home/tristan8/stanford_redcap')
os.chdir('/home/tristan8/stanford_redcap')

from dotenv import load_dotenv
load_dotenv()

import requests
import json

API_TOKEN = os.getenv('REDCAP_API_TOKEN')
API_URL = "https://redcap.stanford.edu/api/"

# Get all metadata
response = requests.post(API_URL, data={
    'token': API_TOKEN,
    'content': 'metadata',
    'format': 'json'
})

if response.status_code == 200:
    metadata = json.loads(response.text)
    
    calc_fields_with_branching = []
    
    for field in metadata:
        # Check if it's a calc field with branching logic
        if field.get('field_type') == 'calc' and field.get('branching_logic'):
            calc_fields_with_branching.append({
                'name': field['field_name'],
                'label': field['field_label'],
                'branching': field['branching_logic']
            })
    
    print(f"Found {len(calc_fields_with_branching)} calculated fields with branching logic:\n")
    
    # Group by branching logic pattern
    branching_patterns = {}
    for field in calc_fields_with_branching:
        pattern = field['branching']
        if pattern not in branching_patterns:
            branching_patterns[pattern] = []
        branching_patterns[pattern].append(field['name'])
    
    # Show grouped results
    for pattern, fields in branching_patterns.items():
        print(f"Branching Logic: {pattern}")
        print(f"Affects {len(fields)} fields:")
        for field_name in fields[:5]:  # Show first 5
            print(f"  - {field_name}")
        if len(fields) > 5:
            print(f"  ... and {len(fields) - 5} more")
        print()
else:
    print(f"Error: {response.status_code}")
