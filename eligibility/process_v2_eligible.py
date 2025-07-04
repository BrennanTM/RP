#!/usr/bin/env python3
import os
import sys
sys.path.append('/home/tristan8/stanford_redcap')
os.chdir('/home/tristan8/stanford_redcap')

from dotenv import load_dotenv
load_dotenv()

import time
import logging
from eligibility.adaptive_processor import AdaptiveEligibilityProcessor, check_v2_eligibility
from common.email_sender import create_providers, MultiProviderEmailSender

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize
API_URL = "https://redcap.stanford.edu/api/"
API_TOKEN = os.getenv('REDCAP_API_TOKEN')

providers = create_providers()
email_sender = MultiProviderEmailSender(providers)
processor = AdaptiveEligibilityProcessor(API_URL, API_TOKEN, email_sender)

logger.info("Starting V2-aware eligibility processor...")

while True:
    try:
        # Get all records
        import requests
        import json
        
        response = requests.post(API_URL, data={
            'token': API_TOKEN,
            'content': 'record',
            'format': 'json'
        })
        
        if response.status_code == 200:
            records = json.loads(response.text)
            logger.info(f"Fetched {len(records)} records")
            
            for record in records:
                record_id = record.get('record_id')
                
                # Skip if already sent
                if record.get('eligibility_email_sent') == '1':
                    continue
                
                # Check eligibility - EITHER overall_eligibility OR v2 check
                is_eligible = record.get('overall_eligibility') == '1' or check_v2_eligibility(record)
                
                if is_eligible:
                    # Get email
                    email = record.get('participant_email_a29017_723fd8_6c173d_20b3be_v2')
                    if email:
                        logger.info(f"Processing eligible record {record_id}")
                        
                        # Get QIDS for category
                        qids = int(record.get('qids_score_screening_42b0d5_714930_v2', 0))
                        is_hc = qids < 11
                        study_id = str(processor.get_next_id(is_hc))
                        
                        # Send email
                        if processor.send_eligibility_email(email, study_id, record_id):
                            processor.mark_email_sent(record_id)
                            logger.info(f"✓ Sent email to {study_id} at {email}")
        
        time.sleep(60)  # Check every minute
        
    except KeyboardInterrupt:
        break
    except Exception as e:
        logger.error(f"Error: {e}")
        time.sleep(60)
