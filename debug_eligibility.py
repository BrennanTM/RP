#!/usr/bin/env python3
"""
Comprehensive debugger for eligibility processing issues
"""
import os
import sys
sys.path.append('/home/tristan8/stanford_redcap')
os.chdir('/home/tristan8/stanford_redcap')

from dotenv import load_dotenv
load_dotenv()

import requests
import json
from datetime import datetime

API_TOKEN = os.getenv('REDCAP_API_TOKEN')
API_URL = "https://redcap.stanford.edu/api/"

print("="*80)
print("REDCap Eligibility Debugger")
print("="*80)

# Get specific records
record_ids = input("Enter record IDs to check (comma-separated, or 'all' for all records): ").strip()

if record_ids.lower() == 'all':
    records_param = None
else:
    records_param = record_ids.replace(' ', '')

# Fetch records
data = {
    'token': API_TOKEN,
    'content': 'record',
    'format': 'json'
}
if records_param:
    data['records'] = records_param

response = requests.post(API_URL, data=data)
if response.status_code != 200:
    print(f"Error fetching records: {response.status_code}")
    sys.exit(1)

records = json.loads(response.text)
print(f"\nAnalyzing {len(records)} record(s)...\n")

# Check the v2 eligibility function
def check_v2_eligibility(record):
    """Check eligibility using _v2 fields"""
    try:
        age = int(record.get('age_c4982e_ee0b48_0fa205_450721_v2', 0))
        travel = record.get('travel_e4c69a_ec4b4a_09fbe2_0f2753_v2', '0')
        english = record.get('english_5c066f_a95c48_a35a95_85e413_v2', '0')
        tms_contra = record.get('tms_contra_d3aef1_4917df_ffe8d8_441f15_v2', '1')
        qids = int(record.get('qids_score_screening_42b0d5_714930_v2', 0))
        
        is_eligible = (18 <= age <= 65 and travel == '1' and 
                      english == '1' and tms_contra == '0' and qids >= 11)
        
        return {
            'eligible': is_eligible,
            'age': age,
            'age_ok': 18 <= age <= 65,
            'travel': travel,
            'travel_ok': travel == '1',
            'english': english,
            'english_ok': english == '1',
            'tms_contra': tms_contra,
            'tms_ok': tms_contra == '0',
            'qids': qids,
            'qids_ok': qids >= 11
        }
    except Exception as e:
        return {'eligible': False, 'error': str(e)}

# Analyze each record
for record in records:
    record_id = record.get('record_id')
    print(f"\n{'='*60}")
    print(f"RECORD {record_id}")
    print(f"{'='*60}")
    
    # 1. Check email fields
    print("\n1. EMAIL FIELDS:")
    email_fields = []
    for field, value in record.items():
        if 'email' in field.lower() and value:
            email_fields.append(f"  {field}: {value}")
    if email_fields:
        print('\n'.join(email_fields))
    else:
        print("  NO EMAIL FOUND!")
    
    # 2. Check if already processed
    print("\n2. PROCESSING STATUS:")
    print(f"  eligibility_email_sent: {record.get('eligibility_email_sent', 'NOT SET')}")
    print(f"  study_id: {record.get('study_id', 'NOT SET')}")
    print(f"  email_sent_timestamp: {record.get('email_sent_timestamp', 'NOT SET')}")
    
    # 3. Check calculated eligibility fields
    print("\n3. CALCULATED ELIGIBILITY FIELDS:")
    print(f"  overall_eligibility: {record.get('overall_eligibility', 'NOT SET')}")
    print(f"  age_eligibility_..._v2: {record.get('age_eligibility_90e0b5_0319c0_11df7a_ad660e_v2', 'NOT SET')}")
    print(f"  travel_eligibility_..._v2: {record.get('travel_eligibility_7344fa_6d3250_608c28_610fc6_v2', 'NOT SET')}")
    print(f"  language_eligibility_..._v2: {record.get('language_eligibility_ea8835_e870c4_07c569_1c692d_v2', 'NOT SET')}")
    print(f"  contra_eligibility_..._v2: {record.get('contra_eligibility_f0a36f_7a7a58_579239_11252f_v2', 'NOT SET')}")
    
    # 4. Check raw data fields
    print("\n4. RAW DATA FIELDS:")
    print(f"  Age: {record.get('age_c4982e_ee0b48_0fa205_450721_v2', 'NOT SET')}")
    print(f"  Travel: {record.get('travel_e4c69a_ec4b4a_09fbe2_0f2753_v2', 'NOT SET')}")
    print(f"  English: {record.get('english_5c066f_a95c48_a35a95_85e413_v2', 'NOT SET')}")
    print(f"  TMS Contra: {record.get('tms_contra_d3aef1_4917df_ffe8d8_441f15_v2', 'NOT SET')}")
    print(f"  QIDS Score: {record.get('qids_score_screening_42b0d5_714930_v2', 'NOT SET')}")
    
    # 5. Manual eligibility check
    print("\n5. MANUAL ELIGIBILITY CHECK (using _v2 fields):")
    v2_check = check_v2_eligibility(record)
    if 'error' in v2_check:
        print(f"  ERROR: {v2_check['error']}")
    else:
        print(f"  ELIGIBLE: {v2_check['eligible']}")
        print(f"  - Age OK (18-65): {v2_check['age_ok']} (age={v2_check['age']})")
        print(f"  - Travel OK: {v2_check['travel_ok']} (value={v2_check['travel']})")
        print(f"  - English OK: {v2_check['english_ok']} (value={v2_check['english']})")
        print(f"  - TMS OK: {v2_check['tms_ok']} (value={v2_check['tms_contra']})")
        print(f"  - QIDS OK (>=11): {v2_check['qids_ok']} (qids={v2_check['qids']})")
    
    # 6. Check all fields containing 'elig'
    print("\n6. ALL ELIGIBILITY-RELATED FIELDS:")
    elig_fields = [(k, v) for k, v in record.items() if 'elig' in k.lower()]
    for field, value in sorted(elig_fields):
        print(f"  {field}: {value}")
    
    # 7. Diagnosis
    print("\n7. DIAGNOSIS:")
    
    # Check if email exists
    has_email = any('email' in k.lower() and v for k, v in record.items())
    if not has_email:
        print("  ❌ NO EMAIL - Cannot send notification")
        continue
    
    # Check if already sent
    if record.get('eligibility_email_sent') == '1':
        print("  ✓ Email already sent")
        continue
    
    # Check overall eligibility
    if record.get('overall_eligibility') == '1':
        print("  ✓ Should be processed (overall_eligibility = 1)")
    elif v2_check.get('eligible'):
        print("  ⚠️ Should be processed via _v2 check")
        print("     BUT overall_eligibility != 1")
        print("     The processor should catch this with check_v2_eligibility")
    else:
        print("  ❌ Not eligible based on criteria")
        if not v2_check.get('age_ok'):
            print(f"     - Age issue: {v2_check.get('age')} not in 18-65")
        if not v2_check.get('travel_ok'):
            print(f"     - Travel issue: {v2_check.get('travel')} != '1'")
        if not v2_check.get('english_ok'):
            print(f"     - English issue: {v2_check.get('english')} != '1'")
        if not v2_check.get('tms_ok'):
            print(f"     - TMS issue: {v2_check.get('tms_contra')} != '0'")
        if not v2_check.get('qids_ok'):
            print(f"     - QIDS issue: {v2_check.get('qids')} < 11")

print("\n" + "="*80)
print("SUMMARY")
print("="*80)

# Test what the processor sees
print("\nTesting what the adaptive processor would see...")
sys.path.append('/home/tristan8/stanford_redcap/eligibility')
try:
    from adaptive_processor import AdaptiveEligibilityProcessor, check_v2_eligibility
    
    # Test the check_v2_eligibility function
    eligible_count = 0
    for record in records:
        if record.get('eligibility_email_sent') != '1':
            if record.get('overall_eligibility') == '1' or check_v2_eligibility(record):
                eligible_count += 1
                print(f"Record {record['record_id']} would be processed")
    
    print(f"\nTotal records that should be processed: {eligible_count}")
    
except Exception as e:
    print(f"\nError loading processor: {e}")

print("\n" + "="*80)
