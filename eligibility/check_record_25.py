import os
import sys
import requests
import json

sys.path.append('/home/tristan8/stanford_redcap')
os.chdir('/home/tristan8/stanford_redcap')

from dotenv import load_dotenv
load_dotenv()

API_TOKEN = os.getenv('REDCAP_API_TOKEN')
API_URL = "https://redcap.stanford.edu/api/"

# Get Record 25
data = {
    'token': API_TOKEN,
    'content': 'record',
    'format': 'json',
    'records': '25'
}

response = requests.post(API_URL, data=data)
if response.status_code == 200:
    record = json.loads(response.text)[0]
    
    print("=== Record 25 Analysis ===")
    print(f"Overall eligibility: {record.get('overall_eligibility', 'Not set')}")
    print(f"Is eligible basic: {record.get('is_eligible_basic', 'Not set')}")
    print(f"Email already sent: {record.get('eligibility_email_sent', '0')}")
    print(f"Study ID: {record.get('study_id', 'Not assigned')}")
    
    # Check regular fields
    print("\nRegular fields:")
    print(f"  Email: {record.get('participant_email', 'Empty')}")
    print(f"  Age: {record.get('age_c4982e_ee0b48_0fa205_450721', 'Empty')}")
    print(f"  QIDS: {record.get('qids_score_screening_42b0d5_714930', 'Empty')}")
    
    # Check _v2 fields
    print("\n_v2 fields:")
    v2_email = record.get('participant_email_ee6446_a52d9a_v2', record.get('participant_email_a29017_723fd8_6c173d_20b3be_v2', ''))
    print(f"  Email: {v2_email}")
    print(f"  Age: {record.get('age_c4982e_ee0b48_0fa205_450721_v2', 'Empty')}")
    print(f"  QIDS: {record.get('qids_score_screening_42b0d5_714930_v2', 'Empty')}")
    print(f"  Travel: {record.get('travel_e4c69a_ec4b4a_09fbe2_0f2753_v2', 'Empty')}")
    print(f"  English: {record.get('english_5c066f_a95c48_a35a95_85e413_v2', 'Empty')}")
    print(f"  TMS contra: {record.get('tms_contra_d3aef1_4917df_ffe8d8_441f15_v2', 'Empty')}")
    
    # Test check_v2_eligibility
    try:
        from eligibility.adaptive_processor import check_v2_eligibility
        is_eligible_v2 = check_v2_eligibility(record)
        print(f"\ncheck_v2_eligibility function result: {is_eligible_v2}")
    except Exception as e:
        print(f"\nError testing check_v2_eligibility: {e}")
    
    # Manual eligibility check
    try:
        age = int(record.get('age_c4982e_ee0b48_0fa205_450721_v2', 0))
        qids = int(record.get('qids_score_screening_42b0d5_714930_v2', 0))
        travel = record.get('travel_e4c69a_ec4b4a_09fbe2_0f2753_v2', '0')
        english = record.get('english_5c066f_a95c48_a35a95_85e413_v2', '0')
        tms = record.get('tms_contra_d3aef1_4917df_ffe8d8_441f15_v2', '1')
        
        manual_eligible = (18 <= age <= 65 and travel == '1' and 
                          english == '1' and tms == '0' and qids >= 11)
        
        print(f"\nManual eligibility check:")
        print(f"  Age OK: {18 <= age <= 65} (age={age})")
        print(f"  Travel OK: {travel == '1'}")
        print(f"  English OK: {english == '1'}")
        print(f"  TMS OK: {tms == '0'}")
        print(f"  QIDS >= 11: {qids >= 11} (qids={qids})")
        print(f"  => ELIGIBLE: {manual_eligible}")
    except Exception as e:
        print(f"\nError in manual check: {e}")
else:
    print(f"Error fetching record: {response.status_code}")
