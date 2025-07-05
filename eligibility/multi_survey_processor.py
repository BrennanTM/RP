#!/usr/bin/env python3
"""
Multi-Survey Eligibility Processor for Healthy and MDD surveys
Place this in ~/stanford_redcap/eligibility/multi_survey_processor.py
"""

import os
import sys
import time
import logging
import json
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.email_sender import MultiProviderEmailSender, create_providers
from common.multi_survey_field_detector import MultiSurveyFieldDetector, MultiSurveyProcessor

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/multi_survey_eligibility.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class HealthyMDDEligibilityProcessor(MultiSurveyProcessor):
    """
    Specialized processor for Healthy and MDD surveys
    """
    
    def __init__(self, api_url: str, api_token: str, email_sender):
        super().__init__(api_url, api_token, email_sender)
        self.rate_limit_delay = 2
        
        # Track study IDs separately
        self.next_hc_id = self._get_next_id('HC')
        self.next_mdd_id = self._get_next_id('MDD')
    
    def _get_next_id(self, category: str) -> int:
        """Get the next available ID for a category"""
        try:
            data = {
                'token': self.api_token,
                'content': 'record',
                'format': 'json',
                'fields': 'study_id'
            }
            
            response = requests.post(self.api_url, data=data)
            if response.status_code != 200:
                return 3466 if category == 'HC' else 10926
            
            records = json.loads(response.text)
            
            if category == 'HC':
                max_id = 3465
                for r in records:
                    sid = r.get('study_id', '')
                    if sid and sid.startswith('HC-'):
                        try:
                            num = int(sid.split('-')[1])
                            max_id = max(max_id, num)
                        except:
                            pass
                return max_id + 1
            else:
                max_id = 10925
                for r in records:
                    sid = r.get('study_id', '')
                    if sid and sid.startswith('MDD-'):
                        try:
                            num = int(sid.split('-')[1])
                            max_id = max(max_id, num)
                        except:
                            pass
                return max_id + 1
        except Exception as e:
            logger.error(f"Error getting next ID: {e}")
            return 3466 if category == 'HC' else 10926
    
    def process_eligible_records(self, dry_run: bool = False) -> List[Dict]:
        """Process eligible records from both surveys"""
        processed = []
        
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
                return processed
            
            records = json.loads(response.text)
            logger.info(f"Fetched {len(records)} records from REDCap")
            
            # Process each record
            for record in records:
                record_id = record.get('record_id')
                
                # Skip if already processed
                if record.get('eligibility_email_sent') == '1':
                    continue
                
                # Detect survey type
                survey_type = self.detector.detect_participant_type(record)
                
                if survey_type == 'unknown':
                    logger.debug(f"Record {record_id}: Cannot determine survey type")
                    continue
                
                logger.info(f"Record {record_id}: Detected as {survey_type} survey")
                
                # Check eligibility
                eligibility = self.detector.check_eligibility(record, survey_type)
                
                if not eligibility['eligible']:
                    logger.debug(f"Record {record_id}: Not eligible - {eligibility['reason']}")
                    continue
                
                email = eligibility.get('email')
                if not email:
                    logger.warning(f"Record {record_id}: Eligible but no email found")
                    continue
                
                # Assign study ID
                category = eligibility['category']
                if category == 'HC':
                    study_id = f"HC-{self.next_hc_id}"
                    self.next_hc_id += 1
                else:
                    study_id = f"MDD-{self.next_mdd_id}"
                    self.next_mdd_id += 1
                
                if dry_run:
                    logger.info(f"[DRY RUN] Would process record {record_id}")
                    logger.info(f"  Survey: {survey_type}")
                    logger.info(f"  Category: {category}")
                    logger.info(f"  Study ID: {study_id}")
                    logger.info(f"  Email: {email}")
                    processed.append({
                        'record_id': record_id,
                        'study_id': study_id,
                        'email': email,
                        'category': category,
                        'survey_type': survey_type
                    })
                else:
                    # Update study ID in REDCap
                    self._update_study_id(record_id, study_id)
                    
                    # Send email
                    if self._send_eligibility_email(record_id, study_id, email, category, survey_type):
                        self._mark_email_sent(record_id)
                        processed.append({
                            'record_id': record_id,
                            'study_id': study_id,
                            'email': email,
                            'category': category,
                            'survey_type': survey_type
                        })
                        logger.info(f"✓ Processed {study_id} ({survey_type}) -> {email}")
                    
                    # Rate limiting
                    time.sleep(self.rate_limit_delay)
            
            return processed
            
        except Exception as e:
            logger.error(f"Error in process_eligible_records: {e}")
            logger.exception("Full traceback:")
            return processed
    
    def _update_study_id(self, record_id: str, study_id: str):
        """Update study ID in REDCap"""
        update_data = {
            'record_id': record_id,
            'study_id': study_id
        }
        
        data = {
            'token': self.api_token,
            'content': 'record',
            'format': 'json',
            'data': json.dumps([update_data]),
            'overwriteBehavior': 'overwrite'
        }
        
        response = requests.post(self.api_url, data=data)
        if response.status_code != 200:
            logger.error(f"Failed to update study ID for record {record_id}")
    
    def _send_eligibility_email(self, record_id: str, study_id: str, email: str, 
                               category: str, survey_type: str) -> bool:
        """Send eligibility notification email"""
        subject = f"Stanford Neuroscience Study Eligibility - Participant {study_id}"
        
        # Customize message based on survey type
        if survey_type == 'healthy':
            study_description = "healthy control group"
        else:
            study_description = "clinical research group"
        
        body = f"""Hello from the Stanford Neuroscience Institute!

I am reaching out from the Precision Neurotherapeutics Lab at Stanford University because you recently completed our {survey_type.upper()} screening survey. Based on your responses, you may be eligible to participate in our study as part of the {study_description}!

Your Study ID is: {study_id}

Measuring brain activity in humans is critical to better understand important cognitive processes (memory, language, vision) and gain insight into brain diseases. We have developed a new and improved way to quantify how the brain is connected using EEG brain recordings after applying Transcranial Magnetic Stimulation (TMS), a non-invasive and safe method that has been around for 30+ years.

**Study Details:**
Participation in the study would entail two separate visits to Stanford between 8am and 5pm during weekdays:
- One 45-minute MRI session (all ear piercings must be removed)
- One 6.5-hour TMS-EEG session

The MRI will be scheduled before the TMS to help us identify the stimulation target for the TMS session. In the TMS-EEG session, we will apply single and/or repetitive pulses of TMS and measure your brain activity using EEG.

**Compensation:**
You will be compensated hourly for your time.

**Next Steps:**
If you are still interested in participating, we would like to first meet with you via Zoom for a one-hour virtual session to review and sign the consent and additional forms together. We may also schedule your MRI and TMS sessions during this call.

**To Schedule Your Consent Session:**
Please click on your personalized scheduling link below:

{self._generate_scheduling_link(study_id, record_id)}

This secure link is unique to you and will allow you to:
- View available appointment times
- Select a convenient time for your consent session
- Receive an immediate confirmation email with Zoom details

If you have any questions or need assistance with scheduling, please don't hesitate to contact us by replying to this email.

Thank you so much for your interest in our study!

Best,
Stanford Precision Neurotherapeutics Lab
Department of Psychiatry and Behavioral Sciences
Stanford University Medical Center"""
        
        # Send email
        kwargs = {
            'categories': ['redcap', 'eligibility', survey_type],
            'custom_args': {
                'study_id': study_id,
                'record_id': record_id,
                'category': category,
                'survey_type': survey_type
            }
        }
        
        return self.email_sender.send_email(email, subject, body, **kwargs)
    
    def _generate_scheduling_link(self, study_id: str, record_id: str) -> str:
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
    
    def _mark_email_sent(self, record_id: str):
        """Mark that email has been sent"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        update_data = {
            'record_id': record_id,
            'eligibility_email_sent': '1',
            'email_sent_timestamp': timestamp
        }
        
        data = {
            'token': self.api_token,
            'content': 'record',
            'format': 'json',
            'data': json.dumps([update_data]),
            'overwriteBehavior': 'overwrite'
        }
        
        response = requests.post(self.api_url, data=data)
        if response.status_code != 200:
            logger.error(f"Failed to mark email sent for record {record_id}")
    
    def generate_survey_report(self) -> Dict:
        """Generate a report on survey completion and eligibility"""
        try:
            data = {
                'token': self.api_token,
                'content': 'record',
                'format': 'json'
            }
            
            response = requests.post(self.api_url, data=data)
            if response.status_code != 200:
                return {}
            
            records = json.loads(response.text)
            
            report = {
                'total_records': len(records),
                'by_survey': {
                    'healthy': {'total': 0, 'eligible': 0, 'emailed': 0},
                    'mdd': {'total': 0, 'eligible': 0, 'emailed': 0},
                    'unknown': {'total': 0}
                }
            }
            
            for record in records:
                survey_type = self.detector.detect_participant_type(record)
                
                if survey_type in ['healthy', 'mdd']:
                    report['by_survey'][survey_type]['total'] += 1
                    
                    eligibility = self.detector.check_eligibility(record, survey_type)
                    if eligibility['eligible']:
                        report['by_survey'][survey_type]['eligible'] += 1
                    
                    if record.get('eligibility_email_sent') == '1':
                        report['by_survey'][survey_type]['emailed'] += 1
                else:
                    report['by_survey']['unknown']['total'] += 1
            
            return report
            
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            return {}


def main():
    """Main execution"""
    # Configuration
    API_URL = "https://redcap.stanford.edu/api/"
    API_TOKEN = os.getenv('REDCAP_API_TOKEN')
    
    if not API_TOKEN:
        logger.error("REDCAP_API_TOKEN not found!")
        return
    
    logger.info("="*60)
    logger.info("Starting Multi-Survey REDCap Eligibility Processor")
    logger.info("Handles both Healthy and MDD surveys")
    logger.info("="*60)
    
    # Create email sender
    logger.info("Initializing email providers...")
    providers = create_providers()
    email_sender = MultiProviderEmailSender(providers)
    
    # Create processor
    processor = HealthyMDDEligibilityProcessor(API_URL, API_TOKEN, email_sender)
    
    # Generate initial report
    logger.info("\n=== Survey Status Report ===")
    report = processor.generate_survey_report()
    if report:
        logger.info(f"Total records: {report['total_records']}")
        for survey_type, stats in report['by_survey'].items():
            if survey_type != 'unknown':
                logger.info(f"\n{survey_type.upper()} Survey:")
                logger.info(f"  Total: {stats['total']}")
                logger.info(f"  Eligible: {stats['eligible']}")
                logger.info(f"  Emailed: {stats['emailed']}")
            else:
                logger.info(f"\nUnknown/Incomplete: {stats['total']}")
    
    # Menu
    while True:
        print("\n=== Multi-Survey Processor Options ===")
        print("1. View survey report")
        print("2. Dry run (check eligible records)")
        print("3. Process records (send emails)")
        print("4. Run continuously")
        print("5. Exit")
        
        choice = input("\nEnter choice (1-5): ").strip()
        
        if choice == '1':
            report = processor.generate_survey_report()
            print("\n=== Current Survey Status ===")
            print(json.dumps(report, indent=2))
        
        elif choice == '2':
            print("\n=== Dry Run Mode ===")
            processed = processor.process_eligible_records(dry_run=True)
            print(f"\nFound {len(processed)} eligible records:")
            
            # Group by survey type
            by_survey = {'healthy': [], 'mdd': []}
            for p in processed:
                by_survey[p['survey_type']].append(p)
            
            for survey_type, participants in by_survey.items():
                if participants:
                    print(f"\n{survey_type.upper()} Survey ({len(participants)} participants):")
                    for p in participants[:5]:  # Show first 5
                        print(f"  - {p['study_id']}: {p['email']}")
                    if len(participants) > 5:
                        print(f"  ... and {len(participants) - 5} more")
        
        elif choice == '3':
            confirm = input("Send emails to eligible participants? (yes/no): ").strip()
            if confirm.lower() == 'yes':
                processed = processor.process_eligible_records()
                print(f"\nProcessed {len(processed)} participants")
                
                # Show breakdown
                by_survey = {'healthy': 0, 'mdd': 0}
                for p in processed:
                    by_survey[p['survey_type']] += 1
                
                print("\nBreakdown by survey:")
                for survey_type, count in by_survey.items():
                    if count > 0:
                        print(f"  {survey_type.upper()}: {count}")
        
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