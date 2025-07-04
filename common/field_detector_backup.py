#!/usr/bin/env python3
"""
Automatic field detection system for REDCap data dictionaries
Makes the pipeline work with any data dictionary by detecting required fields
Place this in ~/stanford_redcap/common/field_detector.py
"""

import re
import json
import logging
from typing import Dict, List, Optional, Any
import requests
from functools import lru_cache

logger = logging.getLogger(__name__)


class FieldDetector:
    """
    Automatically detect and map REDCap fields based on patterns and metadata
    """
    
    def __init__(self, api_url: str, api_token: str):
        self.api_url = api_url
        self.api_token = api_token
        self._field_cache = {}
        self._metadata_cache = None
        
        # Define field patterns for automatic detection
        self.field_patterns = {
            'participant_email': {
                'patterns': [
                    r'.*email.*',
                    r'.*e[\-_]?mail.*',
                    r'.*contact.*email.*'
                ],
                'keywords': ['email', 'contact', 'participant'],
                'validation_type': 'email'
            },
            'qids_score': {
                'patterns': [
                    r'.*qids.*score.*',
                    r'.*qids.*total.*',
                    r'.*depression.*score.*'
                ],
                'keywords': ['qids', 'score', 'depression'],
                'field_type': 'calc'
            },
            'eligibility': {
                'patterns': [
                    r'.*eligib.*',
                    r'.*overall.*eligib.*',
                    r'.*is.*eligible.*'
                ],
                'keywords': ['eligible', 'eligibility'],
                'choices_pattern': r'1,.*[Yy]es'
            },
            'study_id': {
                'patterns': [
                    r'.*study.*id.*',
                    r'.*participant.*id.*',
                    r'.*subject.*id.*'
                ],
                'keywords': ['study', 'participant', 'subject', 'id'],
                'exclude_patterns': [r'.*record.*id.*']
            },
            'age': {
                'patterns': [
                    r'.*age.*',
                    r'.*how.*old.*',
                    r'.*birth.*year.*'
                ],
                'keywords': ['age', 'old', 'years'],
                'validation_type': ['integer', 'number']
            }
        }
        
        # Required tracking fields that might not exist
        self.tracking_fields = {
            'eligibility_email_sent': {'type': 'yesno', 'default': '0'},
            'email_sent_timestamp': {'type': 'datetime', 'default': None},
            'calendly_booked': {'type': 'yesno', 'default': '0'},
            'calendly_date': {'type': 'date', 'default': None},
            'calendly_time': {'type': 'time', 'default': None},
            'appointment_type': {'type': 'dropdown', 'default': None},
            'appointment_confirmation_sent': {'type': 'yesno', 'default': '0'},
            'confirm_sent_timestamp': {'type': 'datetime', 'default': None}
        }
    
    @lru_cache(maxsize=1)
    def get_field_metadata(self) -> Dict:
        """Fetch and cache field metadata from REDCap"""
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
                return self._metadata_cache
            else:
                logger.error(f"Failed to fetch metadata: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"Error fetching metadata: {e}")
            return []
    
    def detect_field(self, field_type: str, record: Dict = None) -> Optional[str]:
        """
        Detect a field name based on patterns and metadata
        
        Args:
            field_type: Type of field to detect (e.g., 'participant_email')
            record: Optional record to search in if metadata fails
            
        Returns:
            Detected field name or None
        """
        if field_type in self._field_cache:
            return self._field_cache[field_type]
        
        # Get metadata
        metadata = self.get_field_metadata()
        pattern_config = self.field_patterns.get(field_type, {})
        
        # First try metadata-based detection
        for field_info in metadata:
            field_name = field_info.get('field_name', '')
            field_label = field_info.get('field_label', '').lower()
            field_note = field_info.get('field_note', '').lower()
            validation = field_info.get('text_validation_type_or_show_slider_number', '')
            
            # Check validation type
            if 'validation_type' in pattern_config:
                valid_types = pattern_config['validation_type']
                if isinstance(valid_types, str):
                    valid_types = [valid_types]
                if validation in valid_types:
                    self._field_cache[field_type] = field_name
                    return field_name
            
            # Check patterns
            for pattern in pattern_config.get('patterns', []):
                if (re.match(pattern, field_name.lower()) or 
                    re.search(pattern, field_label) or 
                    re.search(pattern, field_note)):
                    
                    # Check exclusions
                    excluded = False
                    for exclude in pattern_config.get('exclude_patterns', []):
                        if re.match(exclude, field_name.lower()):
                            excluded = True
                            break
                    
                    if not excluded:
                        self._field_cache[field_type] = field_name
                        return field_name
            
            # Check keywords
            for keyword in pattern_config.get('keywords', []):
                if (keyword in field_name.lower() or 
                    keyword in field_label or 
                    keyword in field_note):
                    self._field_cache[field_type] = field_name
                    return field_name
        
        # If metadata search fails and we have a record, search the record
        if record:
            for pattern in pattern_config.get('patterns', []):
                for field_name in record.keys():
                    if re.match(pattern, field_name.lower()):
                        self._field_cache[field_type] = field_name
                        return field_name
        
        logger.warning(f"Could not detect field for type: {field_type}")
        return None
    
    def get_field_value(self, record: Dict, field_type: str, default: Any = None) -> Any:
        """
        Get a field value from a record using automatic detection
        
        Args:
            record: REDCap record
            field_type: Type of field to get (e.g., 'participant_email')
            default: Default value if field not found
            
        Returns:
            Field value or default
        """
        field_name = self.detect_field(field_type, record)
        
        if field_name and field_name in record:
            return record[field_name]
        
        # Try variations if it's a complex field name
        if field_name:
            # Try without suffix
            base_name = field_name.split('_')[0]
            for key in record.keys():
                if key.startswith(base_name):
                    return record[key]
        
        return default
    
    def map_record(self, record: Dict) -> Dict:
        """
        Map a record to standard field names
        
        Args:
            record: Raw REDCap record
            
        Returns:
            Record with standardized field names
        """
        mapped = {'record_id': record.get('record_id')}
        
        # Map detected fields
        for field_type in self.field_patterns.keys():
            value = self.get_field_value(record, field_type)
            if value is not None:
                mapped[field_type] = value
        
        # Add tracking fields (with defaults if they don't exist)
        for tracking_field, config in self.tracking_fields.items():
            if tracking_field in record:
                mapped[tracking_field] = record[tracking_field]
            else:
                mapped[tracking_field] = config['default']
        
        return mapped
    
    def validate_project_compatibility(self) -> Dict[str, bool]:
        """
        Check if the current REDCap project has required fields
        
        Returns:
            Dictionary of field types and whether they were found
        """
        results = {}
        
        # Check critical fields
        critical_fields = ['participant_email', 'qids_score', 'eligibility']
        for field_type in critical_fields:
            field_name = self.detect_field(field_type)
            results[field_type] = field_name is not None
            if field_name:
                logger.info(f"✓ Detected {field_type}: {field_name}")
            else:
                logger.warning(f"✗ Could not detect {field_type}")
        
        # Check tracking fields
        metadata = self.get_field_metadata()
        existing_fields = {f['field_name'] for f in metadata}
        
        for tracking_field in self.tracking_fields:
            results[tracking_field] = tracking_field in existing_fields
            if not results[tracking_field]:
                logger.info(f"ℹ Tracking field '{tracking_field}' not in project (will use external tracking)")
        
        return results
    
    def get_field_mapping_config(self) -> Dict:
        """
        Generate a field mapping configuration for the current project
        
        Returns:
            Configuration dictionary that can be saved and reused
        """
        config = {
            'detected_fields': {},
            'missing_tracking_fields': [],
            'metadata_summary': {}
        }
        
        # Detect all fields
        for field_type in self.field_patterns.keys():
            field_name = self.detect_field(field_type)
            if field_name:
                config['detected_fields'][field_type] = field_name
        
        # Check tracking fields
        metadata = self.get_field_metadata()
        existing_fields = {f['field_name'] for f in metadata}
        
        for tracking_field in self.tracking_fields:
            if tracking_field not in existing_fields:
                config['missing_tracking_fields'].append(tracking_field)
        
        # Add metadata summary
        config['metadata_summary'] = {
            'total_fields': len(metadata),
            'forms': list(set(f.get('form_name', 'unknown') for f in metadata))
        }
        
        return config


class AdaptiveREDCapProcessor:
    """
    REDCap processor that adapts to any data dictionary
    """
    
    def __init__(self, api_url: str, api_token: str, email_sender):
        self.api_url = api_url
        self.api_token = api_token
        self.email_sender = email_sender
        self.detector = FieldDetector(api_url, api_token)
        
        # Validate project compatibility
        logger.info("=== Validating REDCap Project Compatibility ===")
        compatibility = self.detector.validate_project_compatibility()
        
        # Check if we need external tracking
        self.use_external_tracking = any(
            not compatibility.get(field, True) 
            for field in self.detector.tracking_fields
        )
        
        if self.use_external_tracking:
            logger.info("ℹ Using external tracking database for missing fields")
            self._init_tracking_db()
    
    def _init_tracking_db(self):
        """Initialize external tracking database if needed"""
        import sqlite3
        self.tracking_db = sqlite3.connect('adaptive_tracking.db')
        
        # Create tracking table
        self.tracking_db.execute('''
            CREATE TABLE IF NOT EXISTS participant_tracking (
                record_id TEXT PRIMARY KEY,
                study_id TEXT UNIQUE,
                eligibility_email_sent BOOLEAN DEFAULT 0,
                email_sent_timestamp DATETIME,
                calendly_booked BOOLEAN DEFAULT 0,
                calendly_date DATE,
                calendly_time TIME,
                appointment_type INTEGER,
                appointment_confirmation_sent BOOLEAN DEFAULT 0,
                confirm_sent_timestamp DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.tracking_db.commit()
    
    def process_record(self, record: Dict) -> Optional[Dict]:
        """
        Process a single record with automatic field detection
        """
        # Map record to standard fields
        mapped = self.detector.map_record(record)
        
        # Get values
        email = mapped.get('participant_email')
        qids_score = mapped.get('qids_score')
        is_eligible = mapped.get('eligibility')
        
        # Convert eligibility to standard format
        if is_eligible in ['1', 1, 'yes', 'Yes', True]:
            is_eligible = '1'
        else:
            is_eligible = '0'
        
        # Check if eligible and has required fields
        if is_eligible == '1' and email and qids_score is not None:
            return {
                'record_id': mapped['record_id'],
                'email': email,
                'qids_score': int(qids_score) if qids_score else 0,
                'is_eligible': True
            }
        
        return None


# Example usage in your main processor
def create_adaptive_processor(api_url: str, api_token: str, email_sender):
    """
    Create a processor that works with any REDCap data dictionary
    """
    return AdaptiveREDCapProcessor(api_url, api_token, email_sender)