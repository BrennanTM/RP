from typing import Dict, List, Optional, Any
#!/usr/bin/env python3
"""
Enhanced Adaptive REDCap eligibility processor with automatic _v2 field support
Place this in ~/stanford_redcap/eligibility/adaptive_processor_v2.py
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
        logging.FileHandler('logs/adaptive_eligibility_v2.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class EnhancedFieldDetector(FieldDetector):
    """Enhanced field detector that handles _v2 fields automatically"""
    
    def get_field_value(self, record: Dict, field_type: str, default: Any = None) -> Any:
        """
        Get a field value from a record, checking both regular and _v2 versions
        """
        # First try the regular field detection
        field_name = self.detect_field(field_type, record)
        
        if field_name and field_name in record and record[field_name]:
            return record[field_name]
        
        # If no value found, try _v2 version
        if field_name:
            v2_field_name = f"{field_name}_v2"
            if v2_field_name in record and record[v2_field_name]:
                logger.debug(f"Using _v2 field for {field_type}: {v2_field_name}")
                return record[v2_field_name]
        
        # Also check if the field pattern matches any _v2 fields directly
        pattern_config = self.field_patterns.get(field_type, {})
        for pattern in pattern_config.get('patterns', []):
            for key in record.keys():
                if key.endswith('_v2') and re.match(pattern, key[:-3].lower()):
                    if record[key]:
                        logger.debug(f"Found _v2 field by pattern for {field_type}: {key}")
                        return record[key]
        
        return default
    
    def detect_field(self, field_type: str, record: Dict = None) -> Optional[str]:
        """
        Enhanced field detection that prioritizes populated fields
        """
        # Get base detection
        base_field = super().detect_field(field_type, record)
        
        # If we have a record, check if _v2 version has data while base doesn't
        if record and base_field:
            base_value = record.get(base_field)
            v2_value = record.get(f"{base_field}_v2")
            
            # If base is empty but v2 has data, prefer v2
            if not base_value and v2_value:
                return f"{base_field}_v2"
        
        return base_field


class V2AdaptiveEligibilityProcessor(AdaptiveREDCapProcessor):
    """
    Enhanced processor that handles both regular and _v2 fields seamlessly
    """
    
    def __init__(self, api_url: str, api_token: str, email_sender):
        # Use enhanced field detector
        self.api_url = api_url
        self.api_token = api_token
        self.email_sender = email_sender
        self.detector = EnhancedFieldDetector(api_url, api_token)
        self.rate_limit_delay = 2
        
        # Initialize parent class features
        self._init_tracking_if_needed()
        
        # Log detected fields
        logger.info("=== Enhanced V2 Field Detection Active ===")
    
    def _init_tracking_if_needed(self):
        """Initialize tracking based on field availability"""
        compatibility = self.detector.validate_project_compatibility()
        self.use_external_tracking = any(
            not compatibility.get(field, True) 
            for field in self.detector.tracking_fields
        )
        
        if self.use_external_tracking:
            logger.info("Using external tracking database")
            self._init_tracking_db()
    
    def check_eligibility_with_v2(self, record: Dict) -> Dict:
        """
        Check eligibility using both regular and _v2 fields
        Returns eligibility status and method used
        """
        # First check overall_eligibility field
        overall_eligible = record.get('overall_eligibility', '0') == '1'
        
        if overall_eligible:
            return {'eligible': True, 'method': 'overall_eligibility', 'qids_score': None}
        
        # If not eligible by calculated field, check manually with _v2 fields
        logger.debug(f"Record {record.get('record_id')} - checking _v2 fields manually")
        
        # Check age (18-65)
        age = None
        for age_field in ['age_c4982e_ee0b48_0fa205_450721', 'age_c4982e_ee0b48_0fa205_450721_v2']:
            if age_field in record and record[age_field]:
                try:
                    age = int(record[age_field])
                    break
                except (ValueError, TypeError):
                    continue
        
        if not age or age < 18 or age > 65:
            return {'eligible': False, 'method': 'manual_check', 'reason': f'age={age}'}
        
        # Check travel
        travel_ok = False
        for travel_field in ['travel_e4c69a_ec4b4a_09fbe2_0f2753', 'travel_e4c69a_ec4b4a_09fbe2_0f2753_v2']:
            if record.get(travel_field) == '1':
                travel_ok = True
                break
        
        if not travel_ok:
            return {'eligible': False, 'method': 'manual_check', 'reason': 'travel=0'}
        
        # Check English
        english_ok = False
        for english_field in ['english_5c066f_a95c48_a35a95_85e413', 'english_5c066f_a95c48_a35a95_85e413_v2']:
            if record.get(english_field) == '1':
                english_ok = True
                break
        
        if not english_ok:
            return {'eligible': False, 'method': 'manual_check', 'reason': 'english=0'}
        
        # Check TMS contraindications
        tms_ok = False
        for tms_field in ['tms_contra_d3aef1_4917df_ffe8d8_441f15', 'tms_contra_d3aef1_4917df_ffe8d8_441f15_v2']:
            if record.get(tms_field) == '0':
                tms_ok = True
                break
        
        if not tms_ok:
            return {'eligible': False, 'method': 'manual_check', 'reason': 'tms_contra=1'}
        
        # Get QIDS score
        qids_score = None
        for qids_field in ['qids_score_screening_42b0d5_714930', 'qids_score_screening_42b0d5_714930_v2']:
            if qids_field in record and record[qids_field]:
                try:
                    qids_score = int(record[qids_field])
                    break
                except (ValueError, TypeError):
                    continue
        
        # All criteria met
        return {
            'eligible': True, 
            'method': 'manual_v2_check',
            'qids_score': qids_score,
            'details': {
                'age': age,
                'travel': travel_ok,
                'english': english_ok,
                'tms_ok': tms_ok,
                'qids': qids_score
            }
        }
    
    def get_participant_email(self, record: Dict) -> Optional[str]:
        """Get email from any available field"""
        # Try enhanced detector first
        email = self.detector.get_field_value(record, 'participant_email')
        if email:
            return email
        
        # Manual search for any email field
        email_patterns = ['email', 'e_mail', 'participant_email', 'contact_email']
        for field, value in record.items():
            if value and any(pattern in field.lower() for pattern in email_patterns):
                # Validate it looks like an email
                if '@' in str(value) and '.' in str(value):
                    logger.debug(f"Found email in field: {field}")
                    return value
        
        return None
    
    def process_eligible_records(self, dry_run: bool = False) -> List[Dict]:
        """Process eligible records with enhanced _v2 support"""
        try:
            # Get all records
            data = {
                'token': self.api_token,
                'content': 'record',
                'format': 'json'
            }
            
            response = requests.post(self.api_url, data=data)
            if response.status_code != 200:
                logger.error(f"Failed to fetch records: {response.text}")
                return []
            
            records = json.loads(response.text)
            logger.info(f"Fetched {len(records)} records from REDCap")
            
            processed = []
            failed = []
            v2_eligible_count = 0
            
            for raw_record in records:
                record_id = raw_record.get('record_id')
                
                # Check if already processed
                email_sent = raw_record.get('eligibility_email_sent', '0')
                if email_sent == '1':
                    continue
                
                # Check eligibility (including _v2 fields)
                eligibility_result = self.check_eligibility_with_v2(raw_record)
                
                if not eligibility_result['eligible']:
                    continue
                
                # Track if this was eligible via _v2 check
                if eligibility_result['method'] == 'manual_v2_check':
                    v2_eligible_count += 1
                    logger.info(f"✓ Record {record_id} eligible via _v2 field check")
                
                # Get email
                email = self.get_participant_email(raw_record)
                if not email:
                    logger.warning(f"No email found for eligible record {record_id}")
                    continue
                
                # Get QIDS score
                qids_score = eligibility_result.get('qids_score')
                if qids_score is None:
                    # Try to get it directly
                    qids_score = self.detector.get_field_value(raw_record, 'qids_score')
                    try:
                        qids_score = int(qids_score) if qids_score else 0
                    except:
                        qids_score = 0
                
                if dry_run:
                    logger.info(f"[DRY RUN] Would process record {record_id} -> {email}")
                    logger.info(f"  QIDS Score: {qids_score}")
                    logger.info(f"  Category: {'HC' if qids_score < 11 else 'MDD'}")
                    logger.info(f"  Eligibility method: {eligibility_result['method']}")
                    continue
                
                # Get or assign study ID
                study_id = self.get_or_assign_study_id(raw_record, qids_score)
                
                # Send email
                if self.send_eligibility_email(email, study_id, record_id):
                    # Update tracking
                    self.mark_email_sent(record_id)
                    
                    category = 'HC' if qids_score < 11 else 'MDD'
                    processed.append({
                        'record_id': record_id,
                        'study_id': study_id,
                        'email': email,
                        'category': category,
                        'eligibility_method': eligibility_result['method']
                    })
                    
                    logger.info(f"✓ Sent email to {study_id} ({category}) at {email}")
                else:
                    failed.append({
                        'record_id': record_id,
                        'study_id': study_id,
                        'email': email
                    })
            
            # Summary
            if v2_eligible_count > 0:
                logger.info(f"\n{v2_eligible_count} participants were eligible via _v2 field checking")
            
            if failed:
                logger.warning(f"\n{len(failed)} emails failed to send:")
                for f in failed:
                    logger.warning(f"  - Record {f['record_id']}: {f['email']}")
            
            return processed
            
        except Exception as e:
            logger.error(f"Error in process_eligible_records: {e}")
            logger.exception("Full traceback:")
            return []
    
    def get_or_assign_study_id(self, record: Dict, qids_score: int) -> str:
        """Get existing or assign new study ID"""
        # Check for existing study_id
        study_id = self.detector.get_field_value(record, 'study_id')
        if study_id:
            return study_id
        
        # Assign new ID
        record_id = record.get('record_id')
        is_hc = qids_score < 11
        
        # Get next ID
        study_id = str(self.get_next_id(is_hc))
        
        # Update REDCap
        study_id_field = self.detector.detect_field('study_id')
        if study_id_field:
            self.update_redcap_field(record_id, {study_id_field: study_id})
        
        logger.info(f"Assigned {study_id} to record {record_id} (Category: {'HC' if is_hc else 'MDD'})")
        return study_id
    
    def mark_email_sent(self, record_id: str):
        """Mark that email has been sent"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        updates = {
            'eligibility_email_sent': '1',
            'email_sent_timestamp': timestamp
        }
        
        self.update_redcap_field(record_id, updates)
        
        # Also update external tracking if used
        if self.use_external_tracking:
            self.tracking_db.execute("""
                UPDATE participant_tracking 
                SET eligibility_email_sent = 1, 
                    email_sent_timestamp = ?,
                    updated_at = ?
                WHERE record_id = ?
            """, (timestamp, timestamp, record_id))
            self.tracking_db.commit()
    
    # Inherit other methods from parent class
    
def main():
    """Main execution"""
    # Configuration
    API_URL = "https://redcap.stanford.edu/api/"
    API_TOKEN = os.getenv('REDCAP_API_TOKEN')
    
    if not API_TOKEN:
        logger.error("REDCAP_API_TOKEN not found!")
        return
    
    logger.info("="*60)
    logger.info("Starting Enhanced V2 Adaptive REDCap Eligibility Processor")
    logger.info("This version automatically handles both regular and _v2 fields")
    logger.info("="*60)
    
    # Create email sender
    logger.info("Initializing email providers...")
    providers = create_providers()
    email_sender = MultiProviderEmailSender(providers)
    
    # Create enhanced processor
    processor = V2AdaptiveEligibilityProcessor(API_URL, API_TOKEN, email_sender)
    
    # Menu
    while True:
        print("\n=== Enhanced V2 Processor Options ===")
        print("1. Test field detection (show regular vs _v2)")
        print("2. Dry run (check eligible records)")
        print("3. Process records (send emails)")
        print("4. Run continuously")
        print("5. Check specific record")
        print("6. Exit")
        
        choice = input("\nEnter choice (1-6): ").strip()
        
        if choice == '1':
            # Show field detection for both versions
            print("\n=== Field Detection Test ===")
            data = {
                'token': API_TOKEN,
                'content': 'record',
                'format': 'json',
                'records': '1,25'  # Get record 1 and 25 for comparison
            }
            response = requests.post(API_URL, data=data)
            if response.status_code == 200:
                records = json.loads(response.text)
                for record in records:
                    print(f"\nRecord {record['record_id']}:")
                    
                    # Test email detection
                    email = processor.get_participant_email(record)
                    print(f"  Email: {email}")
                    
                    # Test eligibility
                    elig = processor.check_eligibility_with_v2(record)
                    print(f"  Eligible: {elig['eligible']} (via {elig['method']})")
                    
                    # Show which fields have data
                    v2_fields = [k for k in record.keys() if k.endswith('_v2') and record[k]]
                    if v2_fields:
                        print(f"  Populated _v2 fields: {len(v2_fields)}")
        
        elif choice == '2':
            print("\n=== Dry Run Mode ===")
            processed = processor.process_eligible_records(dry_run=True)
            print(f"\nFound {len(processed)} eligible records")
        
        elif choice == '3':
            confirm = input("Send emails to eligible participants? (yes/no): ").strip()
            if confirm.lower() == 'yes':
                processed = processor.process_eligible_records()
                print(f"\nProcessed {len(processed)} participants")
                
                # Show breakdown by method
                v2_count = sum(1 for p in processed if p.get('eligibility_method') == 'manual_v2_check')
                if v2_count > 0:
                    print(f"  - {v2_count} were eligible via _v2 field checking")
        
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
            record_id = input("Enter record ID to check: ").strip()
            data = {
                'token': API_TOKEN,
                'content': 'record',
                'format': 'json',
                'records': record_id
            }
            response = requests.post(API_URL, data=data)
            if response.status_code == 200:
                records = json.loads(response.text)
                if records:
                    record = records[0]
                    print(f"\n=== Record {record_id} Analysis ===")
                    
                    # Check eligibility
                    elig = processor.check_eligibility_with_v2(record)
                    print(f"Eligible: {elig['eligible']}")
                    print(f"Method: {elig['method']}")
                    if 'details' in elig:
                        print("Details:", json.dumps(elig['details'], indent=2))
                    
                    # Check email
                    email = processor.get_participant_email(record)
                    print(f"Email: {email}")
                    
                    # Show _v2 fields
                    v2_fields = {k: v for k, v in record.items() if k.endswith('_v2') and v}
                    if v2_fields:
                        print(f"\nPopulated _v2 fields ({len(v2_fields)}):")
                        for field, value in v2_fields.items():
                            print(f"  {field}: {value}")
                else:
                    print(f"Record {record_id} not found")
        
        elif choice == '6':
            break
    
    logger.info("Processor stopped")


if __name__ == "__main__":
    main()

    def send_eligibility_email(self, email: str, study_id: str, record_id: str) -> bool:
        """Send eligibility notification email"""
        subject = f"Stanford Neuroscience Study Eligibility - Participant {study_id}"
        
        # Generate scheduling link
        scheduling_link = self.generate_scheduling_link(study_id, record_id)
        
        body = f"""Hello from the Stanford Neuroscience Institute!

I am reaching out from the Precision Neurotherapeutics Lab at Stanford University because you recently filled out the screening survey for one of our studies. Based on your responses, you may be eligible to participate in the study!

Your Study ID is: {study_id}

[Rest of the standard email content...]

**To Schedule Your Consent Session:**
Please click on your personalized scheduling link below:

{scheduling_link}

Thank you so much for your interest in our study!

Best,
Stanford Precision Neurotherapeutics Lab"""
        
        # Send email
        kwargs = {
            'categories': ['redcap', 'eligibility', 'adaptive'],
            'custom_args': {
                'study_id': study_id,
                'record_id': record_id,
                'processor': 'adaptive_v2'
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
    
    def get_next_id(self, is_hc: bool) -> int:
        """Get next available ID"""
        data = {
            'token': self.api_token,
            'content': 'record',
            'format': 'json',
            'fields': 'study_id'
        }
        
        response = requests.post(self.api_url, data=data)
        records = json.loads(response.text) if response.status_code == 200 else []
        
        if is_hc:
            max_id = 3465
            for r in records:
                sid = r.get('study_id', '')
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
                sid = r.get('study_id', '')
                if sid:
                    try:
                        num = int(sid) if sid.isdigit() else int(sid.split('-')[-1])
                        if num >= 10000:
                            max_id = max(max_id, num)
                    except:
                        pass
            return max_id + 1
    
    def update_redcap_field(self, record_id: str, field_updates: Dict) -> bool:
        """Update REDCap fields"""
        data = {
            'token': self.api_token,
            'content': 'record',
            'format': 'json',
            'data': json.dumps([{'record_id': record_id, **field_updates}]),
            'overwriteBehavior': 'overwrite'
        }
        
        response = requests.post(self.api_url, data=data)
        return response.status_code == 200
