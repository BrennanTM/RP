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

# Check which records have this field
response = requests.post(API_URL, data={
    'token': API_TOKEN,
    'content': 'record',
    'format': 'json',
    'fields': 'record_id,age_eligibility_90e0b5_0319c0_11df7a_ad660e_v2'
})

if response.status_code == 200:
    records = json.loads(response.text)
    populated = [r for r in records if r.get('age_eligibility_90e0b5_0319c0_11df7a_ad660e_v2')]
    print(f"Records with age_eligibility field populated: {len(populated)}")
    print(f"Total records: {len(records)}")
    
    if populated:
        print("\nFirst 5 records with this field:")
        for r in populated[:5]:
            print(f"  Record {r['record_id']}: '{r['age_eligibility_90e0b5_0319c0_11df7a_ad660e_v2']}'")
    
    # Also check the metadata for this field
    print("\nChecking field metadata...")
    metadata_response = requests.post(API_URL, data={
        'token': API_TOKEN,
        'content': 'metadata',
        'format': 'json'
    })
    
    if metadata_response.status_code == 200:
        metadata = json.loads(metadata_response.text)
        for field in metadata:
            if field['field_name'] == 'age_eligibility_90e0b5_0319c0_11df7a_ad660e_v2':
                print(f"\nField details:")
                print(f"  Label: {field.get('field_label', 'N/A')}")
                print(f"  Type: {field.get('field_type', 'N/A')}")
                print(f"  Branching logic: {field.get('branching_logic', 'None')}")
                break
else:
    print(f"Error: {response.status_code}")
