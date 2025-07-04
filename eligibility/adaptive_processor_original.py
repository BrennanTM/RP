#!/usr/bin/env python3
"""
Adaptive REDCap eligibility processor that works with any data dictionary
This replaces the eligibility/processor.py with a version that auto-detects fields
"""

import os
import sys
import time
import logging
import json
import requests
from datetime import datetime
from typing import Dict, List, Optional
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.email_sender import MultiProviderEmailSender, create_providers
from common.field_detector import FieldDetector, AdaptiveREDCapProcessor

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/adaptive_eligibility.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class AdaptiveEligibilityProcessor(AdaptiveREDCapProcessor):
    """
    Eligibility processor that adapts to any REDCap data dictionary
    """
    
    def __init__(self, api_url: str, api_token: str, email_sender):
        super().__init__(api_url, api_token, email_sender)
        self.rate_limit_delay = 2
        
        # Save field mapping configuration
        config = self.detector.get_field_mapping_config()
        with open('field_mapping_config.json', 'w') as f:
            json.dump(config, f, indent=2)
        logger.info(f"Field mapping configuration saved to field_mapping_config.json")
    
    def get_next_id(self, is_hc: bool) -> int:
        """Get the next available ID for HC or MDD category"""
        # First, get the field name for study_id
        study_id_field = self.detector.detect_field('study_id')
        
        if not study_id_field:
            # If no study_id field exists, we'll need to track it externally
            if hasattr(self, 'tracking_db'):
                cursor = self.tracking_db.execute(
                    "SELECT MAX(CAST(SUBSTR(study_id, INSTR(study_id, '-') + 1) AS INTEGER)) FROM participant_tracking WHERE study_id LIKE ?"
                    ('HC-%' if is_hc else 'MDD-%',)
                )
                max_id = cursor.fetchone()[0]
                if max_id:
                    return max_id + 1
            
            # Default starting IDs
            return 3466 if is_hc else 10926
        
        # Get all records with study IDs
        data = {
            'token': self.api_token,
            'content': 'record',
            'format': 'json',
            'fields': f'record_id,{study_id_field}'
        }
        
        response = requests.post(self.api_url, data=data)
        if response.status_code != 200:
            return 3466 if is_hc else 10926
        
        records = json.loads(response.text)
        
        if is_hc:
            max_id = 3465
            for r in records:
                sid = r.get(study_id_field, '')
                if sid:
                    try:
                        num = int(sid) if sid.isdigit() else int(sid.split('-')[-1])
                        if num < 10000:
                            max_id = max(max_id, num)
                    except:
                        pass
            return max_id + 1
        else:
            max_id = 10925
            for r in records:
                sid = r.get(study_id_field, '')
                if sid:
                    try:
                        num = int(sid) if sid.isdigit() else int(sid.split('-')[-1])
                        if num >= 10000:
                            max_id = max(max_id, num)
                    except:
                        pass
            return max_id + 1
    
    def update_redcap_field(self, record_id: str, field_updates: Dict) -> bool:
        """Update REDCap fields (only if they exist in the project)"""
        # Check which fields actually exist in REDCap
        metadata = self.detector.get_field_metadata()
        existing_fields = {f['field_name'] for f in metadata}
        
        # Filter updates to only include existing fields
        valid_updates = {'record_id': record_id}
        for field, value in field_updates.items():
            if field in existing_fields:
                valid_updates[field] = value
        
        if len(valid_updates) > 1:  # More than just record_id
            data = {
                'token': self.api_token,
                'content': 'record',
                'format': 'json',
                'data': json.dumps([valid_updates]),
                'overwriteBehavior': 'overwrite'
            }
            
            response = requests.post(self.api_url, data=data)
            return response.status_code == 200
        
        return True
    
    def process_eligible_records(self, dry_run: bool = False) -> List[Dict]:
        """Process eligible records with automatic field detection"""
        try:
            # Build field list for API call
            detected_fields = list(self.detector._field_cache.values())
            field_list = ['record_id'] + detected_fields
            
            # Get all records
            data = {
                'token': self.api_token,
                'content': 'record',
                'format': 'json'
                # Don't specify fields - get all to ensure detection works
            }
            
            response = requests.post(self.api_url, data=data)
            if response.status_code != 200:
                logger.error(f"Failed to fetch records: {response.text}")
                return []
            
            records = json.loads(response.text)
            logger.info(f"Fetched {len(records)} records from REDCap")
            
            processed = []
            failed = []
            
            for raw_record in records:
                record_id = raw_record.get('record_id')
                
                # Map record using field detector
                mapped = self.detector.map_record(raw_record)
                
                # Check if already processed
                email_sent = mapped.get('eligibility_email_sent', '0')
                
                # If using external tracking, check there too
                if self.use_external_tracking and email_sent == '0':
                    cursor = self.tracking_db.execute(
                        "SELECT eligibility_email_sent, study_id FROM participant_tracking WHERE record_id = ?",
                        (record_id,)
                    )
                    tracking = cursor.fetchone()
                    if tracking and tracking[0]:
                        continue
                else:
                    if email_sent == '1':
                        continue
                
                # Process the record
                processed_data = self.process_record(raw_record)
                if not processed_data:
                    continue
                
                email = processed_data['email']
                qids_score = processed_data['qids_score']
                
                if dry_run:
                    logger.info(f"[DRY RUN] Would process record {record_id} -> {email}")
                    logger.info(f"  QIDS Score: {qids_score}")
                    logger.info(f"  Category: {'HC' if qids_score < 11 else 'MDD'}")
                    continue
                
                # Determine category
                is_hc = qids_score < 11
                
                # Get or assign study ID
                study_id_field = self.detector.detect_field('study_id')
                study_id = mapped.get('study_id') if study_id_field else None
                
                if not study_id:
                    # Check external tracking
                    if self.use_external_tracking:
                        cursor = self.tracking_db.execute(
                            "SELECT study_id FROM participant_tracking WHERE record_id = ?",
                            (record_id,)
                        )
                        result = cursor.fetchone()
                        if result and result[0]:
                            study_id = result[0]
                    
                    if not study_id:
                        # Assign new ID
                        study_id = str(self.get_next_id(is_hc))
                        
                        # Update REDCap if field exists
                        if study_id_field:
                            self.update_redcap_field(record_id, {study_id_field: study_id})
                        
                        # Update external tracking
                        if self.use_external_tracking:
                            self.tracking_db.execute(
                                "INSERT OR REPLACE INTO participant_tracking (record_id, study_id) VALUES (?, ?)",
                                (record_id, study_id)
                            )
                            self.tracking_db.commit()
                        
                        logger.info(f" Assigned {study_id} to record {record_id} (Category: {'HC' if is_hc else 'MDD'})")
                
                # Send email
                if self.send_eligibility_email(email, study_id, record_id):
                    # Update tracking
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    # Update REDCap if fields exist
                    updates = {
                        'eligibility_email_sent': '1',
                        'email_sent_timestamp': timestamp
                    }
                    self.update_redcap_field(record_id, updates)
                    
                    # Update external tracking
                    if self.use_external_tracking:
                        self.tracking_db.execute("""
                            UPDATE participant_tracking 
                            SET eligibility_email_sent = 1, 
                                email_sent_timestamp = ?,
                                updated_at = ?
                            WHERE record_id = ?
                        """, (timestamp, timestamp, record_id))
                        self.tracking_db.commit()
                    
                    category = 'HC' if is_hc else 'MDD'
                    processed.append({
                        'record_id': record_id,
                        'study_id': study_id,
                        'email': email,
                        'category': category
                    })
                    
                    logger.info(f" Sent email to {study_id} ({category}) at {email}")
                else:
                    failed.append({
                        'record_id': record_id,
                        'study_id': study_id,
                        'email': email
                    })
            
            if failed:
                logger.warning(f"\n{len(failed)} emails failed to send:")
                for f in failed:
                    logger.warning(f"  - Record {f['record_id']}: {f['email']}")
            
            return processed
            
        except Exception as e:
            logger.error(f"Error in process_eligible_records: {e}")
            logger.exception("Full traceback:")
            return []
    
    def send_eligibility_email(self, email: str, study_id: str, record_id: str) -> bool:
        """Send eligibility notification email"""
        subject = f"Stanford Neuroscience Study Eligibility - Participant {study_id}"
        
        # Generate scheduling link
        scheduling_link = self.generate_scheduling_link(study_id, record_id)
        
        body = f"""Hello from the Stanford Neuroscience Institute!

I am reaching out from the Precision Neurotherapeutics Lab at Stanford University because you recently filled out the screening survey for one of our studies. Based on your responses, you may be eligible to participate in the study!

Your Study ID is: {study_id}

[Rest of email content remains the same...]

**To Schedule Your Consent Session:**
Please click on your personalized scheduling link below:

{scheduling_link}

Thank you so much for your interest in our study!

Best,
Stanford Precision Neurotherapeutics Lab"""
        
        # Apply rate limiting
        time.sleep(self.rate_limit_delay)
        
        # Send email
        kwargs = {
            'categories': ['redcap', 'eligibility', 'adaptive'],
            'custom_args': {
                'study_id': study_id,
                'record_id': record_id,
                'processor': 'adaptive'
            }
        }
        
        return self.email_sender.send_email(email, subject, body, **kwargs)
    
    def generate_scheduling_link(self, study_id: str, record_id: str) -> str:
        """Generate scheduling link"""
        try:
            response = requests.post(
                'http://localhost:8081/api/generate-scheduling-link',
                json={'study_id': study_id, 'record_id': record_id},
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get('link', '[Scheduling system offline]')
        except:
            pass
        
        return "[Scheduling link temporarily unavailable - please contact study coordinator]"


def main():
    """Main execution"""
    # Configuration
    API_URL = "https://redcap.stanford.edu/api/"
    API_TOKEN = os.getenv('REDCAP_API_TOKEN')
    
    if not API_TOKEN:
        logger.error("REDCAP_API_TOKEN not found!")
        return
    
    logger.info("="*60)
    logger.info("Starting Adaptive REDCap Eligibility Processor")
    logger.info("This processor automatically detects fields in any data dictionary")
    logger.info("="*60)
    
    # Create email sender
    logger.info("Initializing email providers...")
    providers = create_providers()
    email_sender = MultiProviderEmailSender(providers)
    
    # Create adaptive processor
    processor = AdaptiveEligibilityProcessor(API_URL, API_TOKEN, email_sender)
    
    # Show detected fields
    logger.info("\n=== Detected Fields ===")
    config = processor.detector.get_field_mapping_config()
    for field_type, field_name in config['detected_fields'].items():
        logger.info(f"  {field_type}: {field_name}")
    
    if config['missing_tracking_fields']:
        logger.info("\n=== Missing Tracking Fields (using external DB) ===")
        for field in config['missing_tracking_fields']:
            logger.info(f"  - {field}")
    
    # Menu
    while True:
        print("\n=== Options ===")
        print("1. Test field detection")
        print("2. Dry run (check eligible records)")
        print("3. Process records (send emails)")
        print("4. Run continuously")
        print("5. Exit")
        
        choice = input("\nEnter choice (1-5): ").strip()
        
        if choice == '1':
            # Test detection on a sample record
            print("\nFetching sample record...")
            data = {
                'token': API_TOKEN,
                'content': 'record',
                'format': 'json',
                'records': '1'
            }
            response = requests.post(API_URL, data=data)
            if response.status_code == 200:
                records = json.loads(response.text)
                if records:
                    record = records[0]
                    mapped = processor.detector.map_record(record)
                    print("\nMapped fields:")
                    for key, value in mapped.items():
                        if value is not None and value != '':
                            print(f"  {key}: {value}")
        
        elif choice == '2':
            print("\n=== Dry Run Mode ===")
            processed = processor.process_eligible_records(dry_run=True)
            print(f"\nFound {len(processed)} eligible records")
        
        elif choice == '3':
            confirm = input("Send emails to eligible participants? (yes/no): ").strip()
            if confirm.lower() == 'yes':
                processed = processor.process_eligible_records()
                print(f"\nProcessed {len(processed)} participants")
        
        elif choice == '4':
            print("\nRunning continuously (Ctrl+C to stop)...")
            try:
                while True:
                    processed = processor.process_eligible_records()
                    if processed:
                        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Processed {len(processed)} participants")
                    time.sleep(60)
            except KeyboardInterrupt:
                print("\nStopped")
                
        elif choice == '5':
            break
    
    logger.info("Processor stopped")


if __name__ == "__main__":
    main()