#!/usr/bin/env python3

import os
import json
import logging
import time
import webbrowser
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from redcap_client import REDCapClient, RedcapApiError
import msal
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import urllib.parse
import sys

load_dotenv()

class AuthHandler(BaseHTTPRequestHandler):
    """Handle OAuth callback"""
    def do_GET(self):
        """Handle the OAuth redirect"""
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if 'code' in params:
            self.server.auth_code = params['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h1>Authentication successful!</h1><p>You can close this window.</p></body></html>')
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress log messages

class OutlookAutonomousScheduler:
    """
    Fully autonomous email scheduler with automatic token refresh.
    Uses DELEGATED permissions to send emails from kellerlab@stanford.edu
    via your tristan8@stanford.edu account that has delegation rights.

    This version:
    - Uses REDCap as Single Source of Truth (SSOT)
    - No local SQLite databases
    - Uses MSAL standard caching with file persistence
    - Implements resilient API calls with retry logic
    """

    def __init__(self):
        # Setup logging
        os.makedirs('./logs', exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('./logs/outlook_autonomous.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

        # REDCap client
        self.redcap_client = REDCapClient()

        # Microsoft Graph settings for DELEGATED auth
        self.tenant_id = os.getenv('AZURE_TENANT_ID', '396573cb-f378-4b68-9bc8-15755c0c51f3')
        self.client_id = os.getenv('AZURE_CLIENT_ID', '3d360571-8a54-4a1b-9373-58d35333d068')
        self.client_secret = os.getenv('AZURE_CLIENT_SECRET')
        self.redirect_uri = 'http://localhost:8000'
        self.sender_email = 'kellerlab@stanford.edu'
        self.your_email = 'tristan8@stanford.edu'

        # Graph API endpoints
        self.graph_base = 'https://graph.microsoft.com/v1.0'
        self.authority = f'https://login.microsoftonline.com/{self.tenant_id}'

        # Token storage (MSAL cache file)
        self.token_cache_file = './.auth_cache_scheduler.json'

        # Initialize Graph API session with retry strategy
        self.graph_session = requests.Session()
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.graph_session.mount("http://", adapter)
        self.graph_session.mount("https://", adapter)

        # Create MSAL app with persistent cache
        self.app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority,
            token_cache=self.load_token_cache()
        )

        # Initialize token
        self.access_token = None
        self.token_expiry = 0
        self.initialize_authentication()

    def load_token_cache(self):
        """Load token cache from file if it exists"""
        cache = msal.SerializableTokenCache()
        if os.path.exists(self.token_cache_file):
            try:
                with open(self.token_cache_file, 'r') as f:
                    cache.deserialize(f.read())
            except (IOError, json.JSONDecodeError):
                self.logger.warning("Could not load token cache, starting fresh")
        return cache

    def save_token_cache(self):
        """Save token cache to file"""
        if self.app.token_cache.has_state_changed:
            try:
                with open(self.token_cache_file, 'w') as f:
                    f.write(self.app.token_cache.serialize())
            except IOError as e:
                self.logger.error(f"Failed to save token cache: {e}")

    def initialize_authentication(self):
        """Initialize authentication - try various methods"""
        self.logger.info("Initializing authentication...")

        # Try to get token from cache
        accounts = self.app.get_accounts()
        if accounts:
            self.logger.info(f"Found {len(accounts)} cached account(s)")
            result = self.app.acquire_token_silent(
                scopes=['Mail.Send.Shared', 'Mail.Send', 'User.Read'],
                account=accounts[0]
            )
            if result and 'access_token' in result:
                self.access_token = result['access_token']
                self.token_expiry = time.time() + result.get('expires_in', 3600)
                self.save_token_cache()
                self.logger.info("✅ Authenticated using cached token")
                return True

        # If no cached token, need interactive authentication
        self.logger.info("No valid cached token found. Interactive authentication required.")
        return self.authenticate_interactively()

    def authenticate_interactively(self):
        """Perform interactive authentication"""
        self.logger.info("Starting interactive authentication...")

        # Start local server for redirect
        server = HTTPServer(('localhost', 8000), AuthHandler)
        server.auth_code = None

        # Get auth URL and open browser
        auth_url = self.app.get_authorization_request_url(
            scopes=['Mail.Send.Shared', 'Mail.Send', 'User.Read'],
            redirect_uri=self.redirect_uri
        )

        self.logger.info(f"Opening browser for authentication...")
        webbrowser.open(auth_url)

        # Wait for callback (with timeout)
        def handle_request():
            server.handle_request()

        thread = threading.Thread(target=handle_request)
        thread.daemon = True
        thread.start()
        thread.join(timeout=60)  # 60 second timeout

        if not server.auth_code:
            self.logger.error("Authentication timeout or cancelled")
            return False

        # Exchange code for token
        result = self.app.acquire_token_by_authorization_code(
            code=server.auth_code,
            scopes=['Mail.Send.Shared', 'Mail.Send', 'User.Read'],
            redirect_uri=self.redirect_uri
        )

        if 'access_token' in result:
            self.access_token = result['access_token']
            self.token_expiry = time.time() + result.get('expires_in', 3600)
            self.save_token_cache()
            self.logger.info("✅ Interactive authentication successful")
            return True
        else:
            self.logger.error(f"Authentication failed: {result.get('error_description', 'Unknown error')}")
            return False

    def ensure_valid_token(self):
        """Ensure we have a valid access token"""
        # Check if token is expiring soon (within 5 minutes)
        if self.access_token and self.token_expiry > time.time() + 300:
            return True

        self.logger.info("Token expired or expiring soon, refreshing...")

        # Try to refresh using MSAL cache
        accounts = self.app.get_accounts()
        if accounts:
            result = self.app.acquire_token_silent(
                scopes=['Mail.Send.Shared', 'Mail.Send', 'User.Read'],
                account=accounts[0],
                force_refresh=True
            )
            if result and 'access_token' in result:
                self.access_token = result['access_token']
                self.token_expiry = time.time() + result.get('expires_in', 3600)
                self.save_token_cache()
                self.logger.info("✅ Token refreshed successfully")
                return True

        # If refresh failed, need interactive auth
        self.logger.warning("Token refresh failed, requiring interactive authentication")
        return self.authenticate_interactively()

    def send_scheduling_email(self, recipient_email, participant_name, study_id, qids_score, group):
        """Send scheduling invitation email"""
        if not self.ensure_valid_token():
            self.logger.error("Failed to ensure valid token")
            return False

        # Determine group label (matching original logic)
        if 0 <= qids_score <= 10:
            group_display = "Healthy Control"
        elif 11 <= qids_score <= 20:
            group_display = "MDD"
        else:
            group_display = "Severe MDD"

        # EXACT original booking URL from pre-refactoring version
        booking_url = "https://outlook.office.com/book/SU-Bookings-EConsentREDCapBooking@bookings.stanford.edu/"

        # EXACT original HTML email template
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.8; color: #333;">
            <div style="max-width: 700px; margin: 0 auto; padding: 20px;">

                <h2 style="color: #8C1515;">Hello from the Stanford Neuroscience Institute!</h2>

                <div style="background-color: #fff; padding: 20px; border-left: 4px solid #8C1515; margin: 20px 0;">
                    <p style="font-size: 18px; margin: 10px 0;">
                        <strong>Your Study ID:</strong> <span style="color: #8C1515; font-size: 22px; font-weight: bold;">{study_id}</span>
                    </p>
                    <p style="color: #666; font-size: 14px; margin: 0;">
                        Please save this ID for all future communications
                    </p>
                </div>

                <p>I am reaching out from the Precision Neurotherapeutics Lab at Stanford University because you recently filled out the screening survey for one of our studies. Based on your responses you may be eligible to participate in the study!</p>

                <p>Measuring brain activity in humans is critical to better understand important cognitive processes (memory, language, vision) and gain insight to better understand brain diseases. Unfortunately the current toolbox to measure brain activity is not ideal. We have developed a new and improved way to quantify how the brain is connected using EEG brain recordings after applying Transcranial Magnetic Stimulation (TMS), a non-invasive and safe method that has been around for 30+ years. Unfortunately there are some signals in this methodology that we need to better understand before this tool can be helpful. That's where we could use your help!</p>

                <p>Participation in the study would entail two separate visits to Stanford between 8am and 5pm during weekdays: one 45-min MRI session (all ear piercings must be removed) and one 6.5-hour TMS-EEG session. The MRI will be scheduled before the TMS to help us identify the stimulation target for the TMS session. In the TMS-EEG session, we will apply single and/or repetitive pulses of TMS and measure your brain activity using EEG. I've attached a consent form to this email that provides more information about our research. Please review the consent form, and we'll also go over it again during our virtual visit before signing together. You will be compensated hourly for your time.</p>

                <p>If you are still interested in participating, we would like to first meet with you via Zoom for a one-hour virtual session to review and sign the consent and additional forms together prior to participation in the study. We may also schedule your sessions during the call.</p>

                <div style="background-color: #f5f5f5; padding: 20px; border-radius: 5px; margin: 20px 0; text-align: center;">
                    <p style="margin: 0 0 15px 0;"><strong>To schedule your virtual consent session:</strong></p>
                    <a href="{booking_url}"
                       style="display: inline-block; padding: 12px 30px; background-color: #0078d4; color: white;
                              text-decoration: none; border-radius: 5px; font-size: 16px; font-weight: bold;">
                        Book Your E-Consent Session
                    </a>
                    <p style="margin: 15px 0 0 0; font-size: 14px; color: #666;">
                        Click the button above to select your preferred time.<br>
                        <strong>Please use your Study ID as your name when booking.</strong><br>
                        This helps protect your privacy while allowing us to identify your appointment.
                    </p>
                </div>

                <div style="background-color: #fff3cd; border: 1px solid #ffc107; padding: 15px; margin: 20px 0; border-radius: 5px;">
                    <p style="margin: 0 0 10px 0; color: #856404;">
                        <strong>📝 IMPORTANT - For Privacy Protection:</strong>
                    </p>
                    <p style="margin: 10px 0; color: #856404;">
                        When asked for your name in the booking form, please enter:
                        <span style="font-size: 20px; color: #8C1515; font-weight: bold; display: block; text-align: center; margin: 10px 0;">{study_id}</span>
                        <em style="font-size: 13px;">(Just your Study ID number - nothing else)</em>
                    </p>
                    <p style="margin: 10px 0 0 0; color: #856404; font-size: 13px;">
                        This ensures your real name doesn't appear in our calendar system, protecting your privacy.
                    </p>
                </div>

                <p>Thank you so much for your interest in our study!</p>

                <p>Best,<br>
                <strong>Stanford Precision Neurotherapeutics Lab</strong><br>
                Department of Psychiatry and Behavioral Sciences<br>
                Stanford University Medical Center</p>

                <hr style="margin-top: 30px; border: none; border-top: 1px solid #ddd;">

                <p style="font-size: 12px; color: #666;">
                This email was sent from kellerlab@stanford.edu<br>
                Stanford University | 401 Quarry Road, Stanford, CA 94305
                </p>
            </div>
        </body>
        </html>
        """

        # Create email
        email_data = {
            "message": {
                "subject": f"Schedule Your Research Appointment - Study ID: {study_id}",
                "body": {
                    "contentType": "HTML",
                    "content": html_body
                },
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": recipient_email
                        }
                    }
                ],
                "from": {
                    "emailAddress": {
                        "address": self.sender_email
                    }
                },
                "replyTo": [
                    {
                        "emailAddress": {
                            "address": self.sender_email
                        }
                    }
                ]
            }
        }

        # Send email using Graph API with session
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

        # Send on behalf of kellerlab@stanford.edu
        send_url = f"{self.graph_base}/users/{self.your_email}/sendMail"

        try:
            response = self.graph_session.post(send_url, json=email_data, headers=headers)
            if response.status_code == 202:
                self.logger.info(f"✅ Invitation email sent to {recipient_email} (ID: {study_id})")
                return True
            else:
                self.logger.error(f"Failed to send email: {response.status_code} - {response.text}")
                return False
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Network error sending email: {e}")
            return False

    def check_new_eligible_participants(self):
        """Check for eligible participants who haven't been invited yet"""
        self.logger.info("Checking for new eligible participants...")

        try:
            # Use filterLogic for efficient server-side filtering
            # Logic: (Status is 'eligible_id_assigned') AND (Timestamp is empty) AND (Email is not empty)
            filter_logic = (
                "[pipeline_processing_status] = 'eligible_id_assigned' and "
                "[pipeline_invitation_sent_timestamp] = '' and "
                "[participant_email_a29017_723fd8_6c173d_v2_98aab5] <> ''"
            )

            records = self.redcap_client.export_records(
                fields=[
                    'record_id',
                    'assigned_study_id_a690e9',
                    'pipeline_processing_status',
                    'pipeline_invitation_sent_timestamp',
                    'participant_email_a29017_723fd8_6c173d_v2_98aab5',
                    'qids_score_screening_42b0d5_v2_1d2371'
                ],
                filter_logic=filter_logic
            )

            new_invitations = 0
            errors = 0

            for record in records:
                record_id = record.get('record_id')
                study_id = record.get('assigned_study_id_a690e9', '').strip()
                email = record.get('participant_email_a29017_723fd8_6c173d_v2_98aab5', '').strip()
                qids_score = record.get('qids_score_screening_42b0d5_v2_1d2371', '').strip()

                # All records returned by filterLogic are ready for invitation
                if study_id and email:
                    # Determine group based on study ID
                    try:
                        id_value = int(study_id)
                        if 3000 <= id_value <= 10199:
                            group = "healthy_control"
                        elif 10200 <= id_value <= 20000:
                            group = "mdd_participant"
                        else:
                            self.logger.warning(f"Study ID {study_id} out of expected ranges")
                            continue
                    except ValueError:
                        self.logger.warning(f"Invalid study ID format: {study_id}")
                        continue

                    # Send the invitation email
                    self.logger.info(f"Sending invitation to {email} (Record: {record_id}, Study ID: {study_id})")

                    # Convert qids_score to int
                    try:
                        qids_int = int(qids_score) if qids_score else 0
                    except (ValueError, TypeError):
                        qids_int = 0

                    if self.send_scheduling_email(
                        recipient_email=email,
                        participant_name="Participant",
                        study_id=study_id,
                        qids_score=qids_int,
                        group=group
                    ):
                        # Update REDCap to mark as invited (SSOT)
                        update_data = {
                            'record_id': record_id,
                            'pipeline_invitation_sent_timestamp': datetime.now().isoformat(),
                            'pipeline_processing_status': 'eligible_invited'
                        }

                        try:
                            self.redcap_client.import_records([update_data])
                            new_invitations += 1
                            self.logger.info(f"✓ Recorded invitation in REDCap for {record_id}")
                        except RedcapApiError as e:
                            self.logger.error(f"CRITICAL: Email sent to {email} but failed to record in REDCap: {e}. Risk of duplicate emails.")
                            errors += 1
                    else:
                        self.logger.error(f"Failed to send invitation email to {email}")
                        errors += 1

            # Summary
            self.logger.info(f"Invitation check complete: {new_invitations} sent, {errors} errors")
            return new_invitations

        except RedcapApiError as e:
            self.logger.error(f"Error fetching records from REDCap: {e}")
            return 0

    def run_continuous(self, check_interval_minutes=2):
        """Run continuously"""
        self.logger.info(f"Starting autonomous scheduler (checking every {check_interval_minutes} minutes)")
        self.logger.info("Using REDCap as Single Source of Truth (SSOT)")

        consecutive_failures = 0
        max_consecutive_failures = 5

        while True:
            try:
                current_time = datetime.now()
                self.logger.info(f"\n{'='*60}")
                self.logger.info(f"Check at {current_time.strftime('%Y-%m-%d %H:%M:%S')}")

                # Check for new eligible participants
                sent = self.check_new_eligible_participants()

                if sent > 0:
                    self.logger.info(f"Successfully sent {sent} invitations")
                    consecutive_failures = 0
                else:
                    self.logger.info("No new eligible participants to invite")

                # Sleep until next check
                self.logger.info(f"Sleeping for {check_interval_minutes} minutes...")
                time.sleep(check_interval_minutes * 60)

            except KeyboardInterrupt:
                self.logger.info("\nShutting down autonomous scheduler...")
                break
            except (RedcapApiError, requests.exceptions.RequestException) as e:
                consecutive_failures += 1
                self.logger.error(f"API error (attempt {consecutive_failures}/{max_consecutive_failures}): {e}")

                if consecutive_failures >= max_consecutive_failures:
                    self.logger.error(f"Too many consecutive failures ({max_consecutive_failures}). Exiting.")
                    sys.exit(1)

                self.logger.info(f"Retrying in {check_interval_minutes} minutes...")
                time.sleep(check_interval_minutes * 60)
            # Remove broad exception handler - fail fast on unexpected errors

    def test_authentication(self):
        """Test if authentication is working"""
        if not self.ensure_valid_token():
            self.logger.error("Failed to authenticate")
            return False

        # Test by getting user info
        headers = {'Authorization': f'Bearer {self.access_token}'}
        try:
            response = self.graph_session.get(f"{self.graph_base}/me", headers=headers)
            if response.status_code == 200:
                user_info = response.json()
                self.logger.info(f"✅ Authenticated as: {user_info.get('displayName')} ({user_info.get('mail')})")
                return True
            else:
                self.logger.error(f"Authentication test failed: {response.status_code}")
                return False
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Network error during authentication test: {e}")
            return False

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Autonomous Outlook Scheduler for Study Invitations (SSOT Version)')
    parser.add_argument('--test', action='store_true', help='Test authentication and exit')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--interval', type=int, default=2, help='Check interval in minutes (default: 2)')

    args = parser.parse_args()

    scheduler = OutlookAutonomousScheduler()

    if args.test:
        scheduler.test_authentication()
    elif args.once:
        scheduler.check_new_eligible_participants()
    else:
        scheduler.run_continuous(check_interval_minutes=args.interval)

if __name__ == "__main__":
    main()