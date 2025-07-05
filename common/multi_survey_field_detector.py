#!/usr/bin/env python3
"""
Multi-Survey Field Detector for REDCap
Handles separate healthy.csv and mdd.csv surveys
"""

import re
import json
import logging
import pandas as pd
from typing import Dict, List, Optional, Any, Tuple
import requests
from functools import lru_cache

logger = logging.getLogger(__name__)


class MultiSurveyFieldDetector:
    """
    Field detector that handles multiple surveys (healthy and MDD)
    """
    
    def __init__(self, api_url: str, api_token: str):
        self.api_url = api_url
        self.api_token = api_token
        self._field_cache = {}
        self._metadata_cache = None
        self._survey_mapping = {}
        
        # Define patterns for common fields across both surveys
        self.common_patterns = {
            'email': {
                'patterns': [r'.*email.*', r'.*e[\-_]?mail.*', r'.*contact.*'],
                'validation_type': 'email'
            },
            'age': {
                'patterns': [r'.*age.*', r'.*how.*old.*', r'.*birth.*'],
                'validation_type': ['integer', 'number']
            },
            'consent': {
                'patterns': [r'.*consent.*', r'.*agree.*', r'.*participate.*']
            }
        }
        
        # Survey-specific patterns
        self.survey_patterns = {
            'healthy': {
                'identifier_patterns': [r'.*healthy.*', r'.*control.*', r'.*hc.*'],
                'form_name_pattern': r'healthy'
            },
            'mdd': {
                'identifier_patterns': [r'.*mdd.*', r'.*depression.*', r'.*mood.*'],
                'form_name_pattern': r'mdd'
            }
        }
    
    def load_data_dictionaries(self, healthy_path: str = None, mdd_path: str = None):
        """Load and analyze the CSV data dictionaries"""
        survey_info = {}
        
        if healthy_path:
            try:
                healthy_df = pd.read_csv(healthy_path)
                survey_info['healthy'] = self._analyze_dictionary(healthy_df, 'healthy')
                logger.info(f"Loaded healthy survey: {len(healthy_df)} fields")
            except Exception as e:
                logger.error(f"Error loading healthy.csv: {e}")
        
        if mdd_path:
            try:
                mdd_df = pd.read_csv(mdd_path)
                survey_info['mdd'] = self._analyze_dictionary(mdd_df, 'mdd')
                logger.info(f"Loaded MDD survey: {len(mdd_df)} fields")
            except Exception as e:
                logger.error(f"Error loading mdd.csv: {e}")
        
        return survey_info
    
    def _analyze_dictionary(self, df: pd.DataFrame, survey_type: str) -> Dict:
        """Analyze a data dictionary to extract key fields"""
        info = {
            'survey_type': survey_type,
            'total_fields': len(df),
            'forms': [],
            'key_fields': {},
            'all_fields': []
        }
        
        # Get form names
        if 'Form Name' in df.columns:
            info['forms'] = df['Form Name'].unique().tolist()
        
        # Analyze each field
        for _, row in df.iterrows():
            field_name = row.get('Variable / Field Name', '')
            if not field_name:
                continue
            
            field_info = {
                'name': field_name,
                'type': row.get('Field Type', ''),
                'label': row.get('Field Label', ''),
                'validation': row.get('Text Validation Type OR Show Slider Number', ''),
                'required': row.get('Required Field?', '') == 'y'
            }
            
            info['all_fields'].append(field_info)
            
            # Check if this is a key field
            for field_type, patterns in self.common_patterns.items():
                for pattern in patterns.get('patterns', []):
                    if re.match(pattern, field_name.lower()) or re.search(pattern, field_info['label'].lower()):
                        info['key_fields'][field_type] = field_name
                        break
        
        return info
    
    @lru_cache(maxsize=1)
    def get_field_metadata(self) -> List[Dict]:
        """Fetch field metadata from REDCap API"""
        if self._metadata_cache is not None:
            return self._metadata_cache
        
        try:
            data = {
                'token': self.api_token,
                'content': 'metadata',
                'format': 'json'
            }
            
            response = requests.post(self.api_url, data=data)
            if response.status_code == 200:
                self._metadata_cache = json.loads(response.text)
                self._analyze_survey_structure()
                return self._metadata_cache
            else:
                logger.error(f"Failed to fetch metadata: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Error fetching metadata: {e}")
            return []
    
    def _analyze_survey_structure(self):
        """Analyze which fields belong to which survey"""
        if not self._metadata_cache:
            return
        
        for field in self._metadata_cache:
            form_name = field.get('form_name', '').lower()
            field_name = field.get('field_name', '').lower()
            
            # Determine which survey this field belongs to
            if any(pattern in form_name for pattern in ['healthy', 'control', 'hc']):
                self._survey_mapping[field['field_name']] = 'healthy'
            elif any(pattern in form_name for pattern in ['mdd', 'depression', 'mood']):
                self._survey_mapping[field['field_name']] = 'mdd'
            elif any(pattern in field_name for pattern in ['healthy', 'hc']):
                self._survey_mapping[field['field_name']] = 'healthy'
            elif any(pattern in field_name for pattern in ['mdd', 'depression']):
                self._survey_mapping[field['field_name']] = 'mdd'
    
    def detect_participant_type(self, record: Dict) -> str:
        """Determine if a participant completed healthy or MDD survey"""
        # Check which fields are populated
        healthy_fields = 0
        mdd_fields = 0
        
        for field_name, value in record.items():
            if value and field_name in self._survey_mapping:
                if self._survey_mapping[field_name] == 'healthy':
                    healthy_fields += 1
                elif self._survey_mapping[field_name] == 'mdd':
                    mdd_fields += 1
        
        # Also check form completion status if available
        if record.get('healthy_complete') == '2':
            return 'healthy'
        elif record.get('mdd_complete') == '2':
            return 'mdd'
        
        # Determine based on field count
        if healthy_fields > mdd_fields:
            return 'healthy'
        elif mdd_fields > healthy_fields:
            return 'mdd'
        
        # Default based on any specific indicators
        return 'unknown'
    
    def get_email_field(self, record: Dict, survey_type: str = None) -> Optional[str]:
        """Get email field value, checking survey-specific fields first"""
        # If we know the survey type, check those fields first
        if survey_type:
            prefix = 'healthy_' if survey_type == 'healthy' else 'mdd_'
            
            # Check survey-specific email fields
            for field_name, value in record.items():
                if value and field_name.startswith(prefix) and 'email' in field_name.lower():
                    return value
        
        # Check any email field
        for field_name, value in record.items():
            if value and 'email' in field_name.lower():
                # Basic email validation
                if '@' in str(value) and '.' in str(value):
                    return value
        
        return None
    
    def check_eligibility(self, record: Dict, survey_type: str) -> Dict:
        """Check eligibility based on survey type"""
        result = {
            'eligible': False,
            'survey_type': survey_type,
            'reason': '',
            'category': ''
        }
        
        # Get email
        email = self.get_email_field(record, survey_type)
        if not email:
            result['reason'] = 'No email found'
            return result
        
        result['email'] = email
        
        # For healthy controls
        if survey_type == 'healthy':
            # Check age (if available)
            age = self._get_age_value(record)
            if age and (age < 18 or age > 65):
                result['reason'] = f'Age {age} outside range'
                return result
            
            # Check any exclusion criteria specific to healthy controls
            # (You can add specific field checks here based on your healthy.csv)
            
            result['eligible'] = True
            result['category'] = 'HC'
        
        # For MDD participants
        elif survey_type == 'mdd':
            # Check age
            age = self._get_age_value(record)
            if age and (age < 18 or age > 65):
                result['reason'] = f'Age {age} outside range'
                return result
            
            # Check depression-related criteria
            # (You can add specific field checks here based on your mdd.csv)
            
            result['eligible'] = True
            result['category'] = 'MDD'
        
        else:
            result['reason'] = 'Unknown survey type'
        
        return result
    
    def _get_age_value(self, record: Dict) -> Optional[int]:
        """Extract age from various possible field names"""
        age_patterns = ['age', 'participant_age', 'demographics_age']
        
        for field_name, value in record.items():
            if value and any(pattern in field_name.lower() for pattern in age_patterns):
                try:
                    return int(value)
                except (ValueError, TypeError):
                    continue
        
        return None
    
    def get_field_mapping_report(self) -> Dict:
        """Generate a report of detected fields and mappings"""
        metadata = self.get_field_metadata()
        
        report = {
            'total_fields': len(metadata),
            'surveys_detected': {
                'healthy': [],
                'mdd': [],
                'unknown': []
            },
            'key_fields': {
                'healthy': {},
                'mdd': {}
            }
        }
        
        # Categorize fields by survey
        for field in metadata:
            field_name = field.get('field_name', '')
            survey = self._survey_mapping.get(field_name, 'unknown')
            report['surveys_detected'][survey].append(field_name)
        
        return report


class MultiSurveyProcessor:
    """Processor that handles both healthy and MDD surveys"""
    
    def __init__(self, api_url: str, api_token: str, email_sender):
        self.api_url = api_url
        self.api_token = api_token
        self.email_sender = email_sender
        self.detector = MultiSurveyFieldDetector(api_url, api_token)
        self.processed_tracker = self._load_processed_tracker()
    
    def _load_processed_tracker(self) -> Dict:
        """Load tracking of processed records"""
        try:
            with open('processed_records.json', 'r') as f:
                return json.load(f)
        except:
            return {'healthy': [], 'mdd': []}
    
    def _save_processed_tracker(self):
        """Save tracking of processed records"""
        with open('processed_records.json', 'w') as f:
            json.dump(self.processed_tracker, f, indent=2)
    
    def process_records(self, dry_run: bool = False) -> Dict:
        """Process all eligible records from both surveys"""
        results = {
            'healthy': {'processed': 0, 'eligible': 0, 'emailed': 0},
            'mdd': {'processed': 0, 'eligible': 0, 'emailed': 0},
            'errors': []
        }
        
        # Fetch all records
        try:
            data = {
                'token': self.api_token,
                'content': 'record',
                'format': 'json'
            }
            
            response = requests.post(self.api_url, data=data)
            if response.status_code != 200:
                logger.error(f"Failed to fetch records: {response.status_code}")
                return results
            
            records = json.loads(response.text)
            logger.info(f"Fetched {len(records)} total records")
            
            for record in records:
                record_id = record.get('record_id')
                
                # Skip if already processed
                if record.get('eligibility_email_sent') == '1':
                    continue
                
                # Detect which survey this participant completed
                survey_type = self.detector.detect_participant_type(record)
                
                if survey_type == 'unknown':
                    continue
                
                results[survey_type]['processed'] += 1
                
                # Check eligibility
                eligibility = self.detector.check_eligibility(record, survey_type)
                
                if eligibility['eligible']:
                    results[survey_type]['eligible'] += 1
                    
                    if dry_run:
                        logger.info(f"[DRY RUN] Would email {survey_type} participant {record_id}: {eligibility['email']}")
                    else:
                        # Send email
                        if self._send_eligibility_email(record_id, eligibility):
                            results[survey_type]['emailed'] += 1
                            self._mark_email_sent(record_id)
                
            return results
            
        except Exception as e:
            logger.error(f"Error processing records: {e}")
            results['errors'].append(str(e))
            return results
    
    def _send_eligibility_email(self, record_id: str, eligibility: Dict) -> bool:
        """Send eligibility email based on survey type"""
        email = eligibility['email']
        survey_type = eligibility['survey_type']
        category = eligibility['category']
        
        # Generate study ID
        study_id = self._generate_study_id(category)
        
        subject = f"Stanford Neuroscience Study Eligibility - {category} Participant {study_id}"
        
        body = f"""Dear Participant,

Thank you for completing the {survey_type.upper()} screening survey for our neuroscience study at Stanford University.

Based on your responses, you may be eligible to participate in our study!

Your Study ID is: {study_id}
Your Category: {category} ({'Healthy Control' if category == 'HC' else 'Major Depressive Disorder'})

[Rest of standard email content...]

Best regards,
Stanford Precision Neurotherapeutics Lab"""
        
        return self.email_sender.send_email(email, subject, body)
    
    def _generate_study_id(self, category: str) -> str:
        """Generate appropriate study ID based on category"""
        # This would connect to your existing ID generation logic
        if category == 'HC':
            return 'HC-' + str(3466 + len(self.processed_tracker.get('healthy', [])))
        else:
            return 'MDD-' + str(10926 + len(self.processed_tracker.get('mdd', [])))
    
    def _mark_email_sent(self, record_id: str):
        """Mark that email has been sent"""
        # Update REDCap
        update_data = {
            'record_id': record_id,
            'eligibility_email_sent': '1',
            'email_sent_timestamp': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        data = {
            'token': self.api_token,
            'content': 'record',
            'format': 'json',
            'data': json.dumps([update_data]),
            'overwriteBehavior': 'overwrite'
        }
        
        requests.post(self.api_url, data=data)


# Utility function to update field detector in existing code
def update_field_detector_for_multi_survey():
    """Update the existing field_detector.py to use multi-survey approach"""
    import os
    import sys
    
    # Add this class to the existing field_detector module
    detector_path = os.path.expanduser('~/stanford_redcap/common/field_detector.py')
    
    # Create backup
    if os.path.exists(detector_path):
        import shutil
        shutil.copy(detector_path, detector_path + '.backup')
        logger.info(f"Created backup: {detector_path}.backup")
    
    # Now you can import this module in your existing code
    return MultiSurveyFieldDetector


if __name__ == "__main__":
    # Test the multi-survey detector
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    API_URL = "https://redcap.stanford.edu/api/"
    API_TOKEN = os.getenv('REDCAP_API_TOKEN')
    
    if API_TOKEN:
        detector = MultiSurveyFieldDetector(API_URL, API_TOKEN)
        
        # Load local CSV files if available
        survey_info = detector.load_data_dictionaries(
            healthy_path='healthy.csv',
            mdd_path='mdd.csv'
        )
        
        print("Survey Analysis:")
        print(json.dumps(survey_info, indent=2))
        
        # Get field mapping report
        report = detector.get_field_mapping_report()
        print("\nField Mapping Report:")
        print(f"Total fields: {report['total_fields']}")
        print(f"Healthy fields: {len(report['surveys_detected']['healthy'])}")
        print(f"MDD fields: {len(report['surveys_detected']['mdd'])}")