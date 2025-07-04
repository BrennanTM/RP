import os
import sys

# Add parent directory to path BEFORE any other imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging
import time
from typing import Dict, List, Set

# Now import from common
from common.email_sender import MultiProviderEmailSender, create_providers

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('appointment_confirmations.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class AppointmentConfirmationSystem:
    def __init__(self, redcap_url: str, redcap_token: str, email_sender):
        self.redcap_url = redcap_url
        self.redcap_token = redcap_token
        self.email_sender = email_sender
        # Track which appointments we've already sent confirmations for
        self.confirmed_appointments = self._load_confirmed_appointments()
        
    def _load_confirmed_appointments(self) -> Set[str]:
        """Load previously confirmed appointments from file"""
        try:
            with open('confirmed_appointments.json', 'r') as f:
                return set(json.load(f))
        except FileNotFoundError:
            return set()
    
    def _save_confirmed_appointments(self):
        """Save confirmed appointments to file"""
        with open('confirmed_appointments.json', 'w') as f:
            json.dump(list(self.confirmed_appointments), f)
    
    def check_for_new_appointments(self) -> List[Dict]:
        """Check REDCap for newly booked appointments that need confirmation"""
        # Get all records with appointments
        data = {
            'token': self.redcap_token,
            'content': 'record',
            'format': 'json',
            'fields': 'record_id,study_id,participant_email,calendly_booked,calendly_date,calendly_time,appointment_type,consent_date,mri_date,tms_date,appointment_confirmation_sent'
        }
        
        response = requests.post(self.redcap_url, data=data)
        if response.status_code != 200:
            logger.error(f"Failed to fetch records: {response.status_code}")
            return []
        
        records = json.loads(response.text)
        new_appointments = []
        
        for record in records:
            # Check if has appointment but no confirmation sent
            if (record.get('calendly_booked') == '1' and 
                record.get('appointment_confirmation_sent') != '1' and
                record.get('participant_email') and
                record.get('study_id')):
                
                # Create unique identifier for this appointment
                appointment_id = f"{record['record_id']}_{record.get('calendly_date', '')}_{record.get('appointment_type', '')}"
                
                if appointment_id not in self.confirmed_appointments:
                    new_appointments.append(record)
        
        logger.info(f"Found {len(new_appointments)} appointments needing confirmation")
        return new_appointments
    
    def send_confirmation_email(self, record: Dict) -> bool:
        """Send appointment confirmation email"""
        study_id = record['study_id']
        email = record['participant_email']
        
        # Determine appointment type and details
        appointment_info = self._get_appointment_details(record)
        
        subject = f"Appointment Confirmation - Stanford Neuroscience Study {study_id}"
        
        # TODO: Replace (650) XXX-XXXX with your actual contact number
        body = f"""Dear Participant {study_id},

This email confirms your appointment for the Stanford Precision Neurotherapeutics Lab study.

**Appointment Details:**
- Type: {appointment_info['type_name']}
- Date: {appointment_info['date_formatted']}
- Time: {appointment_info['time_formatted']}
- Location: Stanford University Medical Center
- Building: {appointment_info['building']}
- Room: {appointment_info['room']}

**Important Reminders:**
{appointment_info['reminders']}

**What to Bring:**
- Photo ID
- Insurance card (if applicable)
- List of current medications
- Completed forms (if sent previously)

**Parking Information:**
- Parking is available at the Medical Center Parking Garage
- We will provide parking validation
- Allow extra time for parking and finding the building

**Contact Information:**
If you need to reschedule or have questions, please reply to this email or call (650) XXX-XXXX.

**Cancellation Policy:**
Please provide at least 24 hours notice if you need to cancel or reschedule.

We look forward to seeing you at your appointment!

Best regards,
Stanford Precision Neurotherapeutics Lab
Department of Psychiatry and Behavioral Sciences
Stanford University Medical Center

---
This is an automated confirmation. Please do not reply directly to this email."""
        
        # Add tracking information
        kwargs = {
            'categories': ['redcap', 'appointment', 'confirmation'],
            'custom_args': {
                'study_id': study_id,
                'record_id': record['record_id'],
                'appointment_type': appointment_info['type_code'],
                'appointment_date': appointment_info['date']
            }
        }
        
        # Send email
        success = self.email_sender.send_email(email, subject, body, **kwargs)
        
        if success:
            logger.info(f"✓ Sent confirmation to {study_id} at {email}")
            # Mark as sent in REDCap
            self._mark_confirmation_sent(record['record_id'])
            # Add to local tracking
            appointment_id = f"{record['record_id']}_{record.get('calendly_date', '')}_{record.get('appointment_type', '')}"
            self.confirmed_appointments.add(appointment_id)
            self._save_confirmed_appointments()
        else:
            logger.error(f"✗ Failed to send confirmation to {study_id}")
        
        return success
    
    def _get_appointment_details(self, record: Dict) -> Dict:
        """Extract and format appointment details"""
        # Map appointment types
        type_map = {
            '1': {
                'name': 'Consent Session',
                'building': 'Main Hospital, 3rd Floor',
                'room': 'Room 3801',
                'duration': '1 hour',
                'reminders': [
                    '- This is your initial consent visit',
                    '- We will review the study procedures',
                    '- Please plan for approximately 1 hour'
                ]
            },
            '2': {
                'name': 'MRI Session',
                'building': 'Lucas Center for Imaging',
                'room': 'MRI Suite 1',
                'duration': '45 minutes',
                'reminders': [
                    '- Remove all metal objects before arriving',
                    '- No piercings, including ear piercings',
                    '- Wear comfortable clothing without metal',
                    '- Let us know if you feel claustrophobic'
                ]
            },
            '3': {
                'name': 'TMS Session',
                'building': 'Psychiatry Building',
                'room': 'TMS Lab, Room 401',
                'duration': '6.5 hours',
                'reminders': [
                    '- This is a full-day session',
                    '- Breakfast and lunch will be provided',
                    '- Wear comfortable clothing',
                    '- You may bring reading material or a laptop'
                ]
            },
            '4': {
                'name': 'General Appointment',
                'building': 'Main Hospital',
                'room': 'Will be confirmed',
                'duration': 'Varies',
                'reminders': [
                    '- Check your email for specific instructions'
                ]
            }
        }
        
        appointment_type = record.get('appointment_type', '4')
        type_info = type_map.get(appointment_type, type_map['4'])
        
        # Format date and time
        date_str = record.get('calendly_date', '')
        time_str = record.get('calendly_time', '')
        
        # Parse and format date
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            date_formatted = date_obj.strftime('%A, %B %d, %Y')
        except:
            date_formatted = date_str
        
        # Format time
        try:
            time_obj = datetime.strptime(time_str, '%H:%M')
            time_formatted = time_obj.strftime('%I:%M %p')
        except:
            time_formatted = time_str or 'To be confirmed'
        
        return {
            'type_code': appointment_type,
            'type_name': type_info['name'],
            'date': date_str,
            'date_formatted': date_formatted,
            'time_formatted': time_formatted,
            'building': type_info['building'],
            'room': type_info['room'],
            'duration': type_info['duration'],
            'reminders': '\n'.join(type_info['reminders'])
        }
    
    def _mark_confirmation_sent(self, record_id: str):
        """Mark that confirmation email has been sent in REDCap"""
        update_data = {
            'record_id': record_id,
            'appointment_confirmation_sent': '1',
            'confirm_sent_timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')  # UPDATED FIELD NAME
        }
        
        data = {
            'token': self.redcap_token,
            'content': 'record',
            'format': 'json',
            'data': json.dumps([update_data]),
            'overwriteBehavior': 'overwrite'
        }
        
        response = requests.post(self.redcap_url, data=data)
        if response.status_code != 200:
            logger.error(f"Failed to mark confirmation sent for record {record_id}")
    
    def send_reminder_emails(self, days_before: int = 2):
        """Send reminder emails X days before appointments"""
        # Calculate target date
        target_date = (datetime.now() + timedelta(days=days_before)).strftime('%Y-%m-%d')
        
        # Get records with appointments on target date
        data = {
            'token': self.redcap_token,
            'content': 'record',
            'format': 'json',
            'fields': 'record_id,study_id,participant_email,calendly_date,appointment_type,appointment_reminder_sent'
        }
        
        response = requests.post(self.redcap_url, data=data)
        if response.status_code != 200:
            logger.error(f"Failed to fetch records for reminders")
            return
        
        records = json.loads(response.text)
        
        for record in records:
            if (record.get('calendly_date') == target_date and
                record.get('appointment_reminder_sent') != '1' and
                record.get('participant_email')):
                
                self._send_reminder_email(record)
    
    def _send_reminder_email(self, record: Dict):
        """Send appointment reminder email"""
        study_id = record['study_id']
        email = record['participant_email']
        appointment_info = self._get_appointment_details(record)
        
        subject = f"Appointment Reminder - Stanford Study {study_id}"
        
        # TODO: Replace (650) XXX-XXXX with your actual contact number
        body = f"""Dear Participant {study_id},

This is a friendly reminder about your upcoming appointment:

**{appointment_info['type_name']}**
Date: {appointment_info['date_formatted']}
Time: {appointment_info['time_formatted']}
Location: {appointment_info['building']}, {appointment_info['room']}

{appointment_info['reminders']}

If you need to reschedule, please contact us as soon as possible at (650) XXX-XXXX.

We look forward to seeing you!

Stanford Precision Neurotherapeutics Lab"""
        
        if self.email_sender.send_email(email, subject, body):
            # Mark reminder as sent
            update_data = {
                'record_id': record['record_id'],
                'appointment_reminder_sent': '1',
                'reminder_sent_timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')  # USES CORRECTED FIELD NAME
            }
            
            data = {
                'token': self.redcap_token,
                'content': 'record',
                'format': 'json',
                'data': json.dumps([update_data]),
                'overwriteBehavior': 'overwrite'
            }
            
            requests.post(self.redcap_url, data=data)
            logger.info(f"✓ Sent reminder to {study_id}")

def main():
    """Main execution"""
    # Configuration
    API_URL = "https://redcap.stanford.edu/api/"
    API_TOKEN = os.getenv('REDCAP_API_TOKEN')
    
    if not API_TOKEN:
        logger.error("REDCAP_API_TOKEN not found in environment variables!")
        return
    
    # Create email sender with your existing providers
    providers = create_providers()
    email_sender = MultiProviderEmailSender(providers)
    
    # Create confirmation system
    confirmation_system = AppointmentConfirmationSystem(API_URL, API_TOKEN, email_sender)
    
    # Menu
    print("\n=== Appointment Confirmation System ===")
    print("1. Send pending confirmations")
    print("2. Send reminder emails (2 days before)")
    print("3. Run continuously")
    print("4. Exit")
    
    choice = input("\nEnter choice (1-4): ").strip()
    
    if choice == '1':
        print("\nChecking for new appointments...")
        new_appointments = confirmation_system.check_for_new_appointments()
        
        if new_appointments:
            print(f"Found {len(new_appointments)} appointments needing confirmation")
            for record in new_appointments:
                confirmation_system.send_confirmation_email(record)
        else:
            print("No new appointments need confirmation")
    
    elif choice == '2':
        print("\nSending reminder emails...")
        confirmation_system.send_reminder_emails(days_before=2)
    
    elif choice == '3':
        print("\nRunning continuously (Ctrl+C to stop)...")
        while True:
            try:
                # Check for new appointments
                new_appointments = confirmation_system.check_for_new_appointments()
                for record in new_appointments:
                    confirmation_system.send_confirmation_email(record)
                
                # Send reminders
                confirmation_system.send_reminder_emails(days_before=2)
                
                # Wait 30 minutes
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting 30 minutes...")
                time.sleep(1800)
                
            except KeyboardInterrupt:
                print("\nStopping...")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                time.sleep(60)

if __name__ == "__main__":
    main()