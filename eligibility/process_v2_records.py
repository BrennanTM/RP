#!/usr/bin/env python3
import os
import sys
sys.path.append('/home/tristan8/stanford_redcap')
os.chdir('/home/tristan8/stanford_redcap')

from dotenv import load_dotenv
load_dotenv()

import requests
import json
from common.email_sender import create_providers, MultiProviderEmailSender

API_URL = "https://redcap.stanford.edu/api/"
API_TOKEN = os.getenv('REDCAP_API_TOKEN')

# Create email sender
providers = create_providers()
email_sender = MultiProviderEmailSender(providers)

# Get all records
response = requests.post(API_URL, data={
    'token': API_TOKEN,
    'content': 'record',
    'format': 'json'
})
records = json.loads(response.text)

print(f"Checking {len(records)} records for _v2 eligibility...\n")

for record in records:
    record_id = record.get('record_id')
    
    # Skip if already sent
    if record.get('eligibility_email_sent') == '1':
        continue
    
    # Get email from _v2 fields
    email = record.get('participant_email_ee6446_a52d9a_v2', '') or record.get('participant_email_a29017_723fd8_6c173d_20b3be_v2', '')
    
    if not email:
        continue
    
    # Check _v2 eligibility
    try:
        age = int(record.get('age_c4982e_ee0b48_0fa205_450721_v2', 0))
        qids = int(record.get('qids_score_screening_42b0d5_714930_v2', 0))
        travel = record.get('travel_e4c69a_ec4b4a_09fbe2_0f2753_v2', '0')
        english = record.get('english_5c066f_a95c48_a35a95_85e413_v2', '0')
        tms = record.get('tms_contra_d3aef1_4917df_ffe8d8_441f15_v2', '1')
        
        if (18 <= age <= 65 and travel == '1' and english == '1' and tms == '0' and qids >= 11):
            print(f"Record {record_id}: ELIGIBLE via _v2 fields")
            print(f"  Email: {email}")
            print(f"  QIDS: {qids}")
            
            if input("\nProcess? (y/n): ").lower() == 'y':
                # Quick process - assign ID and mark sent
                study_id = "10926" if qids >= 11 else "3466"  # Simple ID for now
                
                # Send email
                subject = f"Stanford Neuroscience Study - Participant {study_id}"
                body = "Hello! You are eligible for our study. We'll contact you with next steps."
                
                if email_sender.send_email(email, subject, body):
                    print("  ✓ Email sent!")
                    
                    # Mark as sent
                    requests.post(API_URL, data={
                        'token': API_TOKEN,
                        'content': 'record',
                        'format': 'json',
                        'data': json.dumps([{'record_id': record_id, 'eligibility_email_sent': '1'}]),
                        'overwriteBehavior': 'overwrite'
                    })
    except Exception as e:
        print(f"Error: {e}")

print("\nDone!")
