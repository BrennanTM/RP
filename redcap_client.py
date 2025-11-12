#!/usr/bin/env python3

import os
import requests
import json
from dotenv import load_dotenv
from typing import Dict, List, Optional, Any
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

# Custom Exception for REDCap API errors with concurrency detection
class RedcapApiError(Exception):
    def __init__(self, message, status_code=None, response_body=None, detection_strings=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.detection_strings = detection_strings or ["unique constraint", "duplicate", "already exists"]

    def is_unique_constraint_violation(self):
        # Logic to detect race conditions based on REDCap API response (The "Parse" step)
        if self.status_code in [400, 409, 422] and self.response_body:
            body_lower = self.response_body.lower()
            # Check against configurable detection strings
            for detection_str in self.detection_strings:
                if detection_str.lower() in body_lower:
                    return True
        return False

class REDCapClient:
    def __init__(self):
        self.api_url = os.getenv('REDCAP_API_URL')
        self.api_token = os.getenv('REDCAP_API_TOKEN')

        if not self.api_url or not self.api_token:
            raise ValueError("REDCap API URL and TOKEN must be set in .env file")

        # Load concurrency detection strings from .env, fallback to defaults
        concurrency_strings = os.getenv('REDCAP_CONCURRENCY_STRINGS', "unique constraint,duplicate,already exists")
        self.concurrency_detection_list = [s.strip().lower() for s in concurrency_strings.split(',')]

        # Initialize session with retry strategy for resilience
        self.session = requests.Session()

        # Configure retry strategy with exponential backoff
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,  # Exponential backoff (0.5s, 1s, 2s, 4s, 8s...)
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"]  # REDCap API uses POST for imports/exports
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _make_request(self, data: Dict[str, Any]) -> requests.Response:
        data['token'] = self.api_token
        data['format'] = 'json'

        try:
            response = self.session.post(self.api_url, data=data)
            # Check for HTTP errors but allow reading the body first
            if response.status_code >= 400:
                raise RedcapApiError(
                    f"REDCap API Error: HTTP {response.status_code}",
                    status_code=response.status_code,
                    response_body=response.text,
                    detection_strings=self.concurrency_detection_list
                )
            return response
        except requests.exceptions.RequestException as e:
            # Handle non-HTTP network errors (e.g., ConnectionError)
            if isinstance(e, RedcapApiError):
                raise e
            raise RedcapApiError(f"Network or Request Error: {str(e)}")

    def export_records(self,
                      records: Optional[List[str]] = None,
                      fields: Optional[List[str]] = None,
                      forms: Optional[List[str]] = None,
                      events: Optional[List[str]] = None,
                      filter_logic: Optional[str] = None,
                      raw_or_label: str = 'raw',
                      export_checkbox_labels: bool = False) -> List[Dict]:
        data = {
            'content': 'record',
            'rawOrLabel': raw_or_label,
            'exportCheckboxLabel': export_checkbox_labels,
            'returnFormat': 'json'
        }

        # Add filterLogic if provided (for efficient server-side filtering)
        if filter_logic:
            data['filterLogic'] = filter_logic

        if records:
            for i, record in enumerate(records):
                data[f'records[{i}]'] = record

        if fields:
            for i, field in enumerate(fields):
                data[f'fields[{i}]'] = field

        if forms:
            for i, form in enumerate(forms):
                data[f'forms[{i}]'] = form

        if events:
            for i, event in enumerate(events):
                data[f'events[{i}]'] = event

        response = self._make_request(data)
        return json.loads(response.text)

    def import_records(self, records: List[Dict],
                      overwrite: str = 'normal',
                      return_content: str = 'count') -> Dict:
        data = {
            'content': 'record',
            'overwriteBehavior': overwrite,
            'data': json.dumps(records),
            'returnContent': return_content,
            'returnFormat': 'json'
        }

        response = self._make_request(data)
        return json.loads(response.text)

    def export_metadata(self, fields: Optional[List[str]] = None,
                       forms: Optional[List[str]] = None) -> List[Dict]:
        data = {'content': 'metadata'}

        if fields:
            for i, field in enumerate(fields):
                data[f'fields[{i}]'] = field

        if forms:
            for i, form in enumerate(forms):
                data[f'forms[{i}]'] = form

        response = self._make_request(data)
        return json.loads(response.text)

    def import_metadata(self, metadata: List[Dict]) -> int:
        data = {
            'content': 'metadata',
            'data': json.dumps(metadata),
            'returnFormat': 'json'
        }

        response = self._make_request(data)
        return int(response.text)

    def export_field_names(self, field: Optional[str] = None) -> List[Dict]:
        data = {'content': 'exportFieldNames'}

        if field:
            data['field'] = field

        response = self._make_request(data)
        return json.loads(response.text)

    def export_instruments(self) -> List[Dict]:
        data = {'content': 'instrument'}
        response = self._make_request(data)
        return json.loads(response.text)

    def export_events(self, arms: Optional[List[str]] = None) -> List[Dict]:
        data = {'content': 'event'}

        if arms:
            for i, arm in enumerate(arms):
                data[f'arms[{i}]'] = arm

        response = self._make_request(data)
        return json.loads(response.text)

    def export_project_info(self) -> Dict:
        data = {'content': 'project'}
        response = self._make_request(data)
        return json.loads(response.text)

    def export_users(self) -> List[Dict]:
        data = {'content': 'user'}
        response = self._make_request(data)
        return json.loads(response.text)

    def export_arms(self, arms: Optional[List[str]] = None) -> List[Dict]:
        data = {'content': 'arm'}

        if arms:
            for i, arm in enumerate(arms):
                data[f'arms[{i}]'] = arm

        response = self._make_request(data)
        return json.loads(response.text)

    def delete_records(self, records: List[str]) -> int:
        data = {
            'content': 'record',
            'action': 'delete'
        }

        for i, record in enumerate(records):
            data[f'records[{i}]'] = record

        response = self._make_request(data)
        return int(response.text)


def main():
    print("Initializing REDCap Client...")
    client = REDCapClient()

    print("\n1. Getting project information...")
    try:
        project_info = client.export_project_info()
        print(f"Project Title: {project_info.get('project_title', 'N/A')}")
        print(f"Project ID: {project_info.get('project_id', 'N/A')}")
        print(f"Creation Time: {project_info.get('creation_time', 'N/A')}")
        print(f"Record Count: {project_info.get('record_count', 'N/A')}")
    except RedcapApiError as e:
        print(f"Error getting project info: {e}")

    print("\n2. Getting instruments (forms)...")
    try:
        instruments = client.export_instruments()
        print(f"Found {len(instruments)} instruments:")
        for inst in instruments[:5]:
            print(f"  - {inst.get('instrument_name')}: {inst.get('instrument_label')}")
        if len(instruments) > 5:
            print(f"  ... and {len(instruments) - 5} more")
    except RedcapApiError as e:
        print(f"Error getting instruments: {e}")

    print("\n3. Getting recent records...")
    try:
        records = client.export_records()
        print(f"Found {len(records)} records")
        if records:
            print("Sample record fields:")
            first_record = records[0]
            for key in list(first_record.keys())[:10]:
                print(f"  - {key}: {first_record[key]}")
    except RedcapApiError as e:
        print(f"Error getting records: {e}")

    print("\n4. Getting field names...")
    try:
        field_names = client.export_field_names()
        print(f"Found {len(field_names)} fields")
        for field in field_names[:10]:
            print(f"  - {field.get('original_field_name')}: {field.get('choice_value', '')}")
        if len(field_names) > 10:
            print(f"  ... and {len(field_names) - 10} more")
    except RedcapApiError as e:
        print(f"Error getting field names: {e}")


if __name__ == "__main__":
    main()