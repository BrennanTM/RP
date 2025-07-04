#!/usr/bin/env python3
"""
Continuous processor that handles _v2 fields
"""
import sys
import time
import logging
from adaptive_processor import AdaptiveEligibilityProcessor, main, check_v2_eligibility
import requests
import json

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Monkey patch to add v2 checking
original_process = AdaptiveEligibilityProcessor.process_eligible_records

def process_with_v2(self, dry_run=False):
    # Get regular results first
    processed = []
    
    # Get all records
    data = {
        'token': self.api_token,
        'content': 'record',
        'format': 'json'
    }
    
    response = requests.post(self.api_url, data=data)
    if response.status_code != 200:
        return processed
        
    records = json.loads(response.text)
    
    for record in records:
        record_id = record.get('record_id')
        
        # Skip if already sent
        if record.get('eligibility_email_sent') == '1':
            continue
            
        # Check if eligible via overall_eligibility OR v2 fields
        is_eligible = record.get('overall_eligibility') == '1' or check_v2_eligibility(record)
        
        if is_eligible:
            # Get email - try multiple fields
            email = (record.get('participant_email') or 
                    record.get('participant_email_ee6446_a52d9a_v2') or 
                    record.get('participant_email_a29017_723fd8_6c173d_20b3be_v2'))
            
            if email:
                logger.info(f"Found eligible record {record_id}")
                
                if dry_run:
                    logger.info(f"[DRY RUN] Would process {record_id}")
                    processed.append({'record_id': record_id, 'email': email})
                else:
                    # Process for real
                    qids = int(record.get('qids_score_screening_42b0d5_714930_v2', 0))
                    is_hc = qids < 11
                    study_id = str(self.get_next_id(is_hc))
                    
                    if self.send_eligibility_email(email, study_id, record_id):
                        self.mark_email_sent(record_id)
                        processed.append({
                            'record_id': record_id,
                            'study_id': study_id,
                            'email': email
                        })
                        logger.info(f"✓ Processed record {record_id}")
    
    return processed

# Apply patch
AdaptiveEligibilityProcessor.process_eligible_records = process_with_v2

# Run continuous mode
logger.info("Starting enhanced processor with v2 support...")

# Auto-select option 4 (continuous)
import builtins
class AutoInput:
    def __init__(self):
        self.first = True
    def __call__(self, prompt):
        if self.first:
            self.first = False
            return '4'
        time.sleep(60)
        return ''

builtins.input = AutoInput()
main()
