import os
import sys
sys.path.append('/home/tristan8/stanford_redcap')

#!/usr/bin/env python3
"""
Stanford Precision Neurotherapeutics Lab Scheduling System
In-house scheduling application to replace Calendly
"""

import os
import json
import sqlite3
import secrets
import hashlib
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple
import requests
from flask import Flask, render_template_string, request, jsonify, redirect, url_for
from flask_cors import CORS
import logging
from dotenv import load_dotenv
from functools import wraps
import re
from threading import Thread
import time as time_module

# Import email sender from existing system
from common.email_sender import MultiProviderEmailSender, create_providers

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scheduler.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))
CORS(app, origins=['http://localhost:*', 'http://171.64.52.112:*', 'https://*.stanford.edu'])

# Configuration
REDCAP_API_URL = "https://redcap.stanford.edu/api/"
REDCAP_API_TOKEN = os.environ.get('REDCAP_API_TOKEN')
DATABASE_PATH = 'scheduler.db'

# Business hours configuration
BUSINESS_HOURS = {
    'monday': {'start': time(8, 0), 'end': time(17, 0)},
    'tuesday': {'start': time(8, 0), 'end': time(17, 0)},
    'wednesday': {'start': time(8, 0), 'end': time(17, 0)},
    'thursday': {'start': time(8, 0), 'end': time(17, 0)},
    'friday': {'start': time(8, 0), 'end': time(17, 0)},
    'saturday': None,  # Closed
    'sunday': None     # Closed
}

# Appointment types and durations
APPOINTMENT_TYPES = {
    'consent': {
        'name': 'Consent Session',
        'duration': 60,  # minutes
        'buffer': 15,    # buffer time after appointment
        'description': 'Initial consent and study overview',
        'location': 'Main Hospital, 3rd Floor, Room 3801',
        'instructions': 'This is your initial visit where we will review the study procedures.'
    }
}

# Blackout dates (holidays, etc.)
BLACKOUT_DATES = [
    '2025-07-04',  # Independence Day
    '2025-09-01',  # Labor Day
    '2025-11-27',  # Thanksgiving
    '2025-11-28',  # Day after Thanksgiving
    '2025-12-24',  # Christmas Eve
    '2025-12-25',  # Christmas
    '2025-12-31',  # New Year's Eve
    '2026-01-01',  # New Year's Day
]


class SchedulerDatabase:
    """Handle all database operations for the scheduler"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the SQLite database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Appointments table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS appointments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    appointment_type TEXT NOT NULL,
                    appointment_date DATE NOT NULL,
                    appointment_time TIME NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    status TEXT DEFAULT 'scheduled',
                    confirmation_token TEXT UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    cancelled_at TIMESTAMP,
                    cancellation_reason TEXT,
                    reminder_sent BOOLEAN DEFAULT 0,
                    confirmation_sent BOOLEAN DEFAULT 0,
                    UNIQUE(appointment_date, appointment_time)
                )
            ''')
            
            # Scheduling links table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scheduling_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id TEXT NOT NULL UNIQUE,
                    record_id TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    used_at TIMESTAMP,
                    access_count INTEGER DEFAULT 0,
                    last_accessed TIMESTAMP
                )
            ''')
            
            # Activity log
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id TEXT,
                    action TEXT NOT NULL,
                    details TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
    
    def create_scheduling_link(self, study_id: str, record_id: str, expires_days: int = 30) -> str:
        """Create a unique scheduling link for a participant"""
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=expires_days)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO scheduling_links 
                (study_id, record_id, token, expires_at)
                VALUES (?, ?, ?, ?)
            ''', (study_id, record_id, token, expires_at))
            conn.commit()
        
        return token
    
    def validate_token(self, token: str) -> Optional[Dict]:
        """Validate a scheduling token and return participant info"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT study_id, record_id, expires_at, used_at
                FROM scheduling_links
                WHERE token = ?
            ''', (token,))
            
            result = cursor.fetchone()
            if not result:
                return None
            
            study_id, record_id, expires_at, used_at = result
            
            # Check if expired
            try:
                expires_dt = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                expires_dt = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S')
            if expires_dt < datetime.now():
                return None
            
            # Update access count
            cursor.execute('''
                UPDATE scheduling_links
                SET access_count = access_count + 1,
                    last_accessed = CURRENT_TIMESTAMP
                WHERE token = ?
            ''', (token,))
            conn.commit()
            
            return {
                'study_id': study_id,
                'record_id': record_id,
                'used': used_at is not None
            }
    
    def get_available_slots(self, appointment_type: str, days_ahead: int = 60) -> List[Dict]:
        """Get available appointment slots"""
        slots = []
        appointment_info = APPOINTMENT_TYPES[appointment_type]
        duration = appointment_info['duration'] + appointment_info['buffer']
        
        # Get existing appointments
        existing = self.get_existing_appointments(days_ahead)
        
        # Generate slots for each day
        current_date = datetime.now().date()
        for day_offset in range(1, days_ahead + 1):  # Start from tomorrow
            check_date = current_date + timedelta(days=day_offset)
            
            # Skip weekends and blackout dates
            if check_date.strftime('%Y-%m-%d') in BLACKOUT_DATES:
                continue
            
            weekday = check_date.strftime('%A').lower()
            if weekday not in BUSINESS_HOURS or BUSINESS_HOURS[weekday] is None:
                continue
            
            # Generate time slots for this day
            day_hours = BUSINESS_HOURS[weekday]
            current_time = datetime.combine(check_date, day_hours['start'])
            end_time = datetime.combine(check_date, day_hours['end'])
            
            while current_time + timedelta(minutes=duration) <= end_time:
                slot_str = current_time.strftime('%Y-%m-%d %H:%M')
                
                # Check if slot is available
                if slot_str not in existing:
                    slots.append({
                        'date': current_time.strftime('%Y-%m-%d'),
                        'time': current_time.strftime('%H:%M'),
                        'datetime': current_time.isoformat(),
                        'display': current_time.strftime('%A, %B %d at %I:%M %p')
                    })
                
                # Move to next slot (30-minute increments)
                current_time += timedelta(minutes=30)
        
        return slots
    
    def get_existing_appointments(self, days_ahead: int) -> set:
        """Get set of existing appointment times"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            end_date = (datetime.now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
            
            cursor.execute('''
                SELECT appointment_date, appointment_time
                FROM appointments
                WHERE appointment_date <= ?
                AND status = 'scheduled'
            ''', (end_date,))
            
            return {f"{row[0]} {row[1]}" for row in cursor.fetchall()}
    
    def book_appointment(self, study_id: str, record_id: str, appointment_type: str, 
                        date: str, time: str) -> Tuple[bool, str]:
        """Book an appointment"""
        try:
            appointment_info = APPOINTMENT_TYPES[appointment_type]
            confirmation_token = secrets.token_urlsafe(16)
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Check if slot is still available
                cursor.execute('''
                    SELECT id FROM appointments
                    WHERE appointment_date = ? AND appointment_time = ?
                    AND status = 'scheduled'
                ''', (date, time))
                
                if cursor.fetchone():
                    return False, "This time slot is no longer available"
                
                # Book the appointment
                cursor.execute('''
                    INSERT INTO appointments
                    (study_id, record_id, appointment_type, appointment_date, 
                     appointment_time, duration_minutes, confirmation_token)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (study_id, record_id, appointment_type, date, time, 
                      appointment_info['duration'], confirmation_token))
                
                # Mark scheduling link as used
                cursor.execute('''
                    UPDATE scheduling_links
                    SET used_at = CURRENT_TIMESTAMP
                    WHERE study_id = ?
                ''', (study_id,))
                
                conn.commit()
                
                return True, confirmation_token
                
        except sqlite3.IntegrityError:
            return False, "This time slot is no longer available"
        except Exception as e:
            logger.error(f"Error booking appointment: {e}")
            return False, "An error occurred while booking"
    
    def get_appointment_by_token(self, token: str) -> Optional[Dict]:
        """Get appointment details by confirmation token"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM appointments
                WHERE confirmation_token = ?
            ''', (token,))
            
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def cancel_appointment(self, token: str, reason: str = None) -> bool:
        """Cancel an appointment"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE appointments
                SET status = 'cancelled',
                    cancelled_at = CURRENT_TIMESTAMP,
                    cancellation_reason = ?
                WHERE confirmation_token = ?
                AND status = 'scheduled'
            ''', (reason, token))
            
            conn.commit()
            return cursor.rowcount > 0
    
    def log_activity(self, study_id: str, action: str, details: str = None,
                    ip_address: str = None, user_agent: str = None):
        """Log activity for audit trail"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO activity_log
                (study_id, action, details, ip_address, user_agent)
                VALUES (?, ?, ?, ?, ?)
            ''', (study_id, action, details, ip_address, user_agent))
            conn.commit()


# Initialize database
db = SchedulerDatabase(DATABASE_PATH)

# Initialize email sender
email_providers = create_providers()
email_sender = MultiProviderEmailSender(email_providers)


def update_redcap(record_id: str, appointment_data: Dict):
    """Update REDCap with appointment information"""
    try:
        update_data = {
            'record_id': record_id,
            'calendly_booked': '1',
            'calendly_date': appointment_data['date'],
            'calendly_time': appointment_data['time'],
            'appointment_type': '1',  # Consent session
            'consent_scheduled': '1',
            'consent_date': appointment_data['date'],
            'appointment_scheduled_via': 'stanford_scheduler',
            'appointment_confirmation_sent': '1'
        }
        
        data = {
            'token': REDCAP_API_TOKEN,
            'content': 'record',
            'format': 'json',
            'data': json.dumps([update_data]),
            'overwriteBehavior': 'overwrite'
        }
        
        response = requests.post(REDCAP_API_URL, data=data)
        if response.status_code == 200:
            logger.info(f"Updated REDCap record {record_id} with appointment")
            return True
        else:
            logger.error(f"Failed to update REDCap: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"Error updating REDCap: {e}")
        return False


def send_confirmation_email(study_id: str, appointment: Dict) -> bool:
    """Send appointment confirmation email"""
    try:
        # Get participant email from REDCap
        data = {
            'token': REDCAP_API_TOKEN,
            'content': 'record',
            'format': 'json',
            'fields': 'participant_email',
            'records': appointment['record_id']
        }
        
        response = requests.post(REDCAP_API_URL, data=data)
        if response.status_code != 200:
            return False
        
        records = json.loads(response.text)
        if not records or not records[0].get('participant_email'):
            return False
        
        email = records[0]['participant_email']
        appointment_info = APPOINTMENT_TYPES[appointment['appointment_type']]
        
        # Format appointment datetime
        appt_datetime = datetime.strptime(
            f"{appointment['appointment_date']} {appointment['appointment_time']}", 
            "%Y-%m-%d %H:%M"
        )
        
        subject = f"Appointment Confirmation - Stanford Neuroscience Study {study_id}"
        
        body = f"""Dear Participant {study_id},

This email confirms your appointment for the Stanford Precision Neurotherapeutics Lab study.

**Appointment Details:**
- Type: {appointment_info['name']}
- Date: {appt_datetime.strftime('%A, %B %d, %Y')}
- Time: {appt_datetime.strftime('%I:%M %p')}
- Duration: Approximately {appointment_info['duration']} minutes
- Location: {appointment_info['location']}

**Important Information:**
{appointment_info['instructions']}

**What to Bring:**
- Photo ID
- Insurance card (if applicable)
- Any questions you have about the study

**Cancellation/Rescheduling:**
If you need to cancel or reschedule, please use this link:
http://171.64.52.112:8081/cancel/{appointment['confirmation_token']}

Please provide at least 24 hours notice if you need to cancel or reschedule.

We look forward to seeing you!

Best regards,
Stanford Precision Neurotherapeutics Lab
Department of Psychiatry and Behavioral Sciences
Stanford University Medical Center

---
This is an automated confirmation. Please do not reply directly to this email.
For questions, please contact us at [contact email]."""
        
        # Send email
        kwargs = {
            'categories': ['scheduler', 'appointment', 'confirmation'],
            'custom_args': {
                'study_id': study_id,
                'record_id': appointment['record_id'],
                'appointment_type': appointment['appointment_type'],
                'appointment_date': appointment['appointment_date']
            }
        }
        
        return email_sender.send_email(email, subject, body, **kwargs)
        
    except Exception as e:
        logger.error(f"Error sending confirmation email: {e}")
        return False


# HTML Templates
SCHEDULER_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Schedule Your Appointment - Stanford Neuroscience Study</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background-color: #f5f5f5;
        }
        
        .container {
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
        }
        
        .header {
            background-color: #8C1515;
            color: white;
            padding: 30px 20px;
            text-align: center;
            margin-bottom: 30px;
            border-radius: 8px;
        }
        
        .header h1 {
            font-size: 24px;
            margin-bottom: 10px;
        }
        
        .header p {
            font-size: 16px;
            opacity: 0.9;
        }
        
        .card {
            background: white;
            border-radius: 8px;
            padding: 30px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            color: #555;
        }
        
        .info-box {
            background-color: #f8f9fa;
            border-left: 4px solid #8C1515;
            padding: 15px;
            margin-bottom: 20px;
        }
        
        .calendar-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 10px;
            margin-top: 20px;
            max-height: 400px;
            overflow-y: auto;
            padding: 10px;
            border: 1px solid #e0e0e0;
            border-radius: 4px;
        }
        
        .time-slot {
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.2s;
            text-align: center;
            background: white;
        }
        
        .time-slot:hover {
            border-color: #8C1515;
            background-color: #f8f9fa;
        }
        
        .time-slot.selected {
            border-color: #8C1515;
            background-color: #8C1515;
            color: white;
        }
        
        .btn {
            display: inline-block;
            padding: 12px 30px;
            background-color: #8C1515;
            color: white;
            text-decoration: none;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            transition: background-color 0.2s;
        }
        
        .btn:hover {
            background-color: #6d1010;
        }
        
        .btn:disabled {
            background-color: #ccc;
            cursor: not-allowed;
        }
        
        .error {
            color: #dc3545;
            margin-top: 10px;
        }
        
        .success {
            color: #28a745;
            margin-top: 10px;
        }
        
        .loading {
            display: none;
            text-align: center;
            padding: 20px;
        }
        
        .spinner {
            border: 3px solid #f3f3f3;
            border-top: 3px solid #8C1515;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Stanford Precision Neurotherapeutics Lab</h1>
            <p>Schedule Your Consent Session</p>
        </div>
        
        <div class="card">
            <h2>Welcome, Participant {{ study_id }}</h2>
            
            <div class="info-box">
                <h3>About Your Visit</h3>
                <p><strong>Duration:</strong> Approximately 60 minutes</p>
                <p><strong>Location:</strong> Stanford University Medical Center, Main Hospital, 3rd Floor, Room 3801</p>
                <p><strong>Purpose:</strong> We will review the study procedures and complete the consent process together.</p>
            </div>
            
            <form id="schedulingForm">
                <div class="form-group">
                    <label>Select an Available Time:</label>
                    <div id="timeSlots" class="calendar-grid">
                        <div class="loading">
                            <div class="spinner"></div>
                            <p>Loading available times...</p>
                        </div>
                    </div>
                </div>
                
                <div id="selectedInfo" style="display:none;" class="info-box">
                    <p><strong>Selected Time:</strong> <span id="selectedTimeDisplay"></span></p>
                </div>
                
                <div id="errorMessage" class="error"></div>
                <div id="successMessage" class="success"></div>
                
                <button type="submit" class="btn" id="submitBtn" disabled>
                    Confirm Appointment
                </button>
            </form>
        </div>
        
        <div class="card">
            <h3>Important Information</h3>
            <ul style="margin-left: 20px;">
                <li>Please arrive 10 minutes early to allow time for parking</li>
                <li>Bring a photo ID and insurance card (if applicable)</li>
                <li>The session will be conducted via Zoom if you prefer virtual</li>
                <li>You will receive a confirmation email with additional details</li>
                <li>If you need to cancel or reschedule, please provide 24 hours notice</li>
            </ul>
        </div>
    </div>
    
    <script>
        let selectedSlot = null;
        const token = '{{ token }}';
        
        // Load available time slots
        async function loadTimeSlots() {
            const slotsContainer = document.getElementById('timeSlots');
            slotsContainer.innerHTML = '<div class="loading"><div class="spinner"></div><p>Loading available times...</p></div>';
            
            try {
                const response = await fetch(`/api/available-slots?token=${token}&type=consent`);
                const data = await response.json();
                
                if (data.error) {
                    throw new Error(data.error);
                }
                
                if (data.slots.length === 0) {
                    slotsContainer.innerHTML = '<p>No available times found. Please contact us for assistance.</p>';
                    return;
                }
                
                slotsContainer.innerHTML = '';
                data.slots.forEach(slot => {
                    const slotElement = document.createElement('div');
                    slotElement.className = 'time-slot';
                    slotElement.textContent = slot.display;
                    slotElement.dataset.date = slot.date;
                    slotElement.dataset.time = slot.time;
                    slotElement.dataset.datetime = slot.datetime;
                    
                    slotElement.addEventListener('click', () => selectSlot(slotElement));
                    slotsContainer.appendChild(slotElement);
                });
                
            } catch (error) {
                slotsContainer.innerHTML = '<p class="error">Error loading available times. Please refresh the page.</p>';
                console.error('Error:', error);
            }
        }
        
        function selectSlot(slotElement) {
            // Remove previous selection
            document.querySelectorAll('.time-slot').forEach(slot => {
                slot.classList.remove('selected');
            });
            
            // Select new slot
            slotElement.classList.add('selected');
            selectedSlot = {
                date: slotElement.dataset.date,
                time: slotElement.dataset.time,
                display: slotElement.textContent
            };
            
            // Update display
            document.getElementById('selectedTimeDisplay').textContent = selectedSlot.display;
            document.getElementById('selectedInfo').style.display = 'block';
            document.getElementById('submitBtn').disabled = false;
        }
        
        // Handle form submission
        document.getElementById('schedulingForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            if (!selectedSlot) return;
            
            const submitBtn = document.getElementById('submitBtn');
            const errorMsg = document.getElementById('errorMessage');
            const successMsg = document.getElementById('successMessage');
            
            submitBtn.disabled = true;
            submitBtn.textContent = 'Scheduling...';
            errorMsg.textContent = '';
            successMsg.textContent = '';
            
            try {
                const response = await fetch('/api/book-appointment', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        token: token,
                        type: 'consent',
                        date: selectedSlot.date,
                        time: selectedSlot.time
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    successMsg.textContent = 'Appointment scheduled successfully! Check your email for confirmation.';
                    // Redirect to confirmation page
                    setTimeout(() => {
                        window.location.href = `/confirmation/${data.confirmation_token}`;
                    }, 2000);
                } else {
                    throw new Error(data.error || 'Failed to schedule appointment');
                }
                
            } catch (error) {
                errorMsg.textContent = error.message;
                submitBtn.disabled = false;
                submitBtn.textContent = 'Confirm Appointment';
            }
        });
        
        // Load slots on page load
        loadTimeSlots();
    </script>
</body>
</html>
'''

CONFIRMATION_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Appointment Confirmed - Stanford Neuroscience Study</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background-color: #f5f5f5;
            margin: 0;
            padding: 20px;
        }
        
        .container {
            max-width: 600px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        
        .success-icon {
            width: 60px;
            height: 60px;
            background-color: #28a745;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 20px;
        }
        
        .success-icon::after {
            content: "";
            color: white;
            font-size: 30px;
        }
        
        h1 {
            text-align: center;
            color: #8C1515;
            margin-bottom: 30px;
        }
        
        .appointment-details {
            background-color: #f8f9fa;
            padding: 20px;
            border-radius: 4px;
            margin: 20px 0;
        }
        
        .detail-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
        }
        
        .detail-label {
            font-weight: 600;
            color: #555;
        }
        
        .btn {
            display: inline-block;
            padding: 10px 20px;
            margin: 10px 5px;
            text-decoration: none;
            border-radius: 4px;
            text-align: center;
        }
        
        .btn-danger {
            background-color: #dc3545;
            color: white;
        }
        
        .btn-secondary {
            background-color: #6c757d;
            color: white;
        }
        
        .actions {
            text-align: center;
            margin-top: 30px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="success-icon"></div>
        <h1>Appointment Confirmed!</h1>
        
        <p>Thank you for scheduling your appointment. A confirmation email has been sent to your registered email address.</p>
        
        <div class="appointment-details">
            <h3>Appointment Details</h3>
            <div class="detail-row">
                <span class="detail-label">Study ID:</span>
                <span>{{ appointment.study_id }}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Type:</span>
                <span>{{ appointment_type_name }}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Date:</span>
                <span>{{ appointment_date }}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Time:</span>
                <span>{{ appointment_time }}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Location:</span>
                <span>{{ location }}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Confirmation Code:</span>
                <span>{{ appointment.confirmation_token }}</span>
            </div>
        </div>
        
        <div class="actions">
            <p>Need to make changes?</p>
            <a href="/cancel/{{ appointment.confirmation_token }}" class="btn btn-danger">Cancel Appointment</a>
        </div>
        
        <hr style="margin: 30px 0;">
        
        <h3>What's Next?</h3>
        <ul>
            <li>You will receive a reminder email 2 days before your appointment</li>
            <li>Please arrive 10 minutes early to allow time for parking</li>
            <li>Bring a photo ID and insurance card (if applicable)</li>
            <li>If you have questions, refer to the confirmation email</li>
        </ul>
    </div>
</body>
</html>
'''


# Flask Routes
@app.route('/')
def index():
    return '<h1>Stanford Scheduling System</h1><p>Please use your personalized scheduling link.</p>'


@app.route('/schedule/<token>')
def schedule(token):
    """Main scheduling page for participants"""
    # Validate token
    participant = db.validate_token(token)
    if not participant:
        return '<h1>Invalid or Expired Link</h1><p>Please contact the study coordinator for a new scheduling link.</p>', 403
    
    if participant['used']:
        return '<h1>Link Already Used</h1><p>This scheduling link has already been used. If you need to reschedule, please use the link in your confirmation email.</p>', 403
    
    # Log access
    db.log_activity(
        participant['study_id'], 
        'accessed_scheduler',
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent')
    )
    
    return render_template_string(SCHEDULER_TEMPLATE, 
                                study_id=participant['study_id'],
                                token=token)


@app.route('/api/available-slots')
def api_available_slots():
    """API endpoint to get available appointment slots"""
    token = request.args.get('token')
    appointment_type = request.args.get('type', 'consent')
    
    # Validate token
    participant = db.validate_token(token)
    if not participant:
        return jsonify({'error': 'Invalid token'}), 403
    
    # Get available slots
    slots = db.get_available_slots(appointment_type)
    
    return jsonify({'slots': slots})


@app.route('/api/book-appointment', methods=['POST'])
def api_book_appointment():
    """API endpoint to book an appointment"""
    data = request.get_json()
    
    # Validate token
    participant = db.validate_token(data['token'])
    if not participant:
        return jsonify({'error': 'Invalid token'}), 403
    
    if participant['used']:
        return jsonify({'error': 'This link has already been used'}), 403
    
    # Book appointment
    success, result = db.book_appointment(
        participant['study_id'],
        participant['record_id'],
        data['type'],
        data['date'],
        data['time']
    )
    
    if success:
        # Update REDCap
        appointment_data = {
            'date': data['date'],
            'time': data['time']
        }
        update_redcap(participant['record_id'], appointment_data)
        
        # Get full appointment details
        appointment = db.get_appointment_by_token(result)
        
        # Send confirmation email in background
        Thread(target=send_confirmation_email, 
               args=(participant['study_id'], appointment)).start()
        
        # Log booking
        db.log_activity(
            participant['study_id'],
            'booked_appointment',
            f"Booked {data['type']} on {data['date']} at {data['time']}",
            request.remote_addr,
            request.headers.get('User-Agent')
        )
        
        return jsonify({
            'success': True,
            'confirmation_token': result
        })
    else:
        return jsonify({
            'success': False,
            'error': result
        })


@app.route('/confirmation/<token>')
def confirmation(token):
    """Appointment confirmation page"""
    appointment = db.get_appointment_by_token(token)
    if not appointment:
        return '<h1>Invalid Confirmation</h1><p>Appointment not found.</p>', 404
    
    # Format appointment details
    appointment_info = APPOINTMENT_TYPES[appointment['appointment_type']]
    appt_datetime = datetime.strptime(
        f"{appointment['appointment_date']} {appointment['appointment_time']}", 
        "%Y-%m-%d %H:%M"
    )
    
    return render_template_string(CONFIRMATION_TEMPLATE,
                                appointment=appointment,
                                appointment_type_name=appointment_info['name'],
                                appointment_date=appt_datetime.strftime('%A, %B %d, %Y'),
                                appointment_time=appt_datetime.strftime('%I:%M %p'),
                                location=appointment_info['location'])


@app.route('/cancel/<token>', methods=['GET', 'POST'])
def cancel_appointment(token):
    """Cancel appointment page"""
    appointment = db.get_appointment_by_token(token)
    if not appointment or appointment['status'] != 'scheduled':
        return '<h1>Invalid Request</h1><p>Appointment not found or already cancelled.</p>', 404
    
    if request.method == 'POST':
        reason = request.form.get('reason', 'Participant requested cancellation')
        if db.cancel_appointment(token, reason):
            # Update REDCap
            update_data = {
                'record_id': appointment['record_id'],
                'appointment_cancelled': '1',
                'cancellation_reason': reason,
                'cancellation_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            # Send cancellation to REDCap
            data = {
                'token': REDCAP_API_TOKEN,
                'content': 'record',
                'format': 'json',
                'data': json.dumps([update_data]),
                'overwriteBehavior': 'overwrite'
            }
            requests.post(REDCAP_API_URL, data=data)
            
            return '<h1>Appointment Cancelled</h1><p>Your appointment has been cancelled. You will receive a confirmation email.</p>'
    
    # Show cancellation form
    return '''
    <html>
    <head><title>Cancel Appointment</title></head>
    <body style="font-family: Arial; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1>Cancel Appointment</h1>
        <p>Are you sure you want to cancel your appointment on <strong>{} at {}</strong>?</p>
        <form method="POST">
            <label>Reason for cancellation (optional):</label><br>
            <textarea name="reason" rows="4" cols="50"></textarea><br><br>
            <button type="submit" style="background: #dc3545; color: white; padding: 10px 20px; border: none; cursor: pointer;">
                Confirm Cancellation
            </button>
            <a href="/" style="margin-left: 10px;">Keep Appointment</a>
        </form>
    </body>
    </html>
    '''.format(
        datetime.strptime(appointment['appointment_date'], '%Y-%m-%d').strftime('%B %d, %Y'),
        datetime.strptime(appointment['appointment_time'], '%H:%M').strftime('%I:%M %p')
    )


# API endpoints for dashboard integration
@app.route('/api/appointments')
def api_appointments():
    """Get all appointments for calendar display"""
    # This endpoint would need authentication in production
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT 
                    a.*,
                    at.name as type_name,
                    at.duration,
                    at.location
                FROM appointments a
                JOIN (
                    SELECT 'consent' as type, 'Consent Session' as name, 
                           60 as duration, 'Main Hospital, 3rd Floor, Room 3801' as location
                ) at ON a.appointment_type = at.type
                WHERE a.status = 'scheduled'
                AND a.appointment_date >= date('now')
                ORDER BY a.appointment_date, a.appointment_time
            ''')
            
            appointments = []
            for row in cursor.fetchall():
                appt = dict(row)
                # Format for calendar display
                start_datetime = f"{appt['appointment_date']}T{appt['appointment_time']}"
                appointments.append({
                    'id': appt['id'],
                    'title': f"{appt['study_id']} - {appt['type_name']}",
                    'start': start_datetime,
                    'end': (datetime.fromisoformat(start_datetime) + 
                           timedelta(minutes=appt['duration'])).isoformat(),
                    'study_id': appt['study_id'],
                    'location': appt['location'],
                    'status': appt['status']
                })
            
            return jsonify(appointments)
            
    except Exception as e:
        logger.error(f"Error fetching appointments: {e}")
        return jsonify({'error': 'Failed to fetch appointments'}), 500


@app.route('/api/generate-scheduling-link', methods=['POST'])
def api_generate_link():
    """Generate scheduling link for a participant"""
    # This endpoint would need authentication in production
    data = request.get_json()
    study_id = data.get('study_id')
    record_id = data.get('record_id')
    
    if not study_id or not record_id:
        return jsonify({'error': 'Missing required fields'}), 400
    
    token = db.create_scheduling_link(study_id, record_id)
    base_url = os.environ.get("SCHEDULER_URL", "https://influences-progressive-registrar-route.trycloudflare.com")
    
    return jsonify({
        'success': True,
        'link': f"{base_url}/schedule/{token}",
        'token': token
    })


# Background task for sending reminders
def send_appointment_reminders():
    """Background task to send appointment reminders"""
    while True:
        try:
            # Check for appointments 2 days out
            reminder_date = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
            
            with sqlite3.connect(DATABASE_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM appointments
                    WHERE appointment_date = ?
                    AND status = 'scheduled'
                    AND reminder_sent = 0
                ''', (reminder_date,))
                
                for row in cursor.fetchall():
                    # Send reminder email
                    # Mark as sent
                    cursor.execute('''
                        UPDATE appointments
                        SET reminder_sent = 1
                        WHERE id = ?
                    ''', (row[0],))
                
                conn.commit()
            
            # Run every hour
            time_module.sleep(3600)
            
        except Exception as e:
            logger.error(f"Error in reminder task: {e}")
            time_module.sleep(60)


if __name__ == '__main__':
    # Start reminder thread
    reminder_thread = Thread(target=send_appointment_reminders, daemon=True)
    reminder_thread.start()
    
    # Run Flask app
    app.run(host='0.0.0.0', port=8081, debug=False)