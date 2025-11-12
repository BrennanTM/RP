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

class IneligibleEmailSender:
    """
    Send emails to ineligible participants using Microsoft Graph API.

    This version:
    - Uses REDCap as Single Source of Truth (SSOT)
    - No local SQLite databases
    - Independent authentication (not dependent on outlook_autonomous_scheduler)
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
                logging.FileHandler('./logs/ineligible_emails.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

        # REDCap client
        self.redcap = REDCapClient()

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

        # Token storage (MSAL cache file - independent from scheduler)
        self.token_cache_file = './.auth_cache_ineligible.json'

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

    def send_ineligible_email(self, record_id, recipient_email, ineligibility_reasons):
        """Send ineligible notification email"""
        if not self.ensure_valid_token():
            self.logger.error("Failed to ensure valid token")
            return False

        # Format reasons for display
        reasons_html = "<ul>"
        for reason in ineligibility_reasons:
            reasons_html += f"<li>{reason}</li>"
        reasons_html += "</ul>"

        # Create email
        email_data = {
            "message": {
                "subject": "Thank You for Your Interest in Our Research Study",
                "body": {
                    "contentType": "HTML",
                    "content": """
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <style>
                            body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
                            .container { max-width: 600px; margin: 0 auto; padding: 20px; }
                            .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                                     color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }
                            .content { background: #f8f9fa; padding: 30px; border-radius: 0 0 10px 10px; }
                            .stanford-logo { max-width: 200px; margin: 20px 0; }
                            h1 { margin: 0; font-size: 28px; }
                            .footer { text-align: center; margin-top: 30px; padding-top: 20px;
                                     border-top: 1px solid #ddd; font-size: 12px; color: #666; }
                            .info-box { background: white; padding: 20px; border-radius: 8px; margin-top: 20px;
                                       border-left: 4px solid #8B0000; }
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <div class="header">
                                <h1>Stanford Precision Neurotherapeutics Lab</h1>
                                <p style="margin: 10px 0 0 0; font-size: 18px;">Department of Psychiatry and Behavioral Sciences</p>
                            </div>

                            <div class="content">
                                <p>Dear Participant,</p>

                                <p>Thank you for your interest in our research study and for taking the time to complete the screening questionnaire. We sincerely appreciate your willingness to contribute to advancing mental health research.</p>

                                <p>We have received your screening questionnaire and have carefully reviewed your responses. Our research team maintains a participant pool based on current study needs and enrollment capacity.</p>

                                <p><strong>We will reach out to you if an opening becomes available that matches your profile.</strong> Please note that study enrollment is limited and based on various research parameters that may change over time.</p>

                                <div class="info-box">
                                    <h3 style="margin-top: 0;">What Happens Next</h3>
                                    <p>• Your information has been securely stored in our participant database<br>
                                    • If a suitable opening becomes available, our team will contact you directly<br>
                                    • No further action is required from you at this time</p>
                                </div>

                                <p>In the meantime, we encourage you to explore other research opportunities at Stanford. The Department of Psychiatry regularly conducts various studies, and you may find other projects that interest you at <a href="https://med.stanford.edu/psychiatry/research.html">Stanford Psychiatry Research</a>.</p>

                                <p>Thank you once again for your interest in our research. Your engagement with scientific studies, even at the screening stage, contributes valuable information that helps advance our understanding of mental health.</p>

                                <p>If you have any questions about the study or your screening questionnaire, please feel free to contact us.</p>

                                <p>Best regards,<br>
                                <strong>The Stanford Precision Neurotherapeutics Lab Team</strong></p>
                            </div>

                            <div class="footer">
                                <p>Stanford University School of Medicine<br>
                                Department of Psychiatry and Behavioral Sciences<br>
                                401 Quarry Road, Stanford, CA 94305</p>
                                <p style="margin-top: 10px;">This email was sent from kellerlab@stanford.edu</p>
                            </div>
                        </div>
                    </body>
                    </html>
                    """
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
                self.logger.info(f"✅ Ineligible notification sent to {recipient_email} (Record: {record_id})")

                # Update REDCap to mark as notified (SSOT)
                update_data = {
                    'record_id': record_id,
                    'pipeline_ineligible_notification_sent_timestamp': datetime.now().isoformat(),
                    'pipeline_processing_status': 'ineligible_notified'
                }

                try:
                    self.redcap.import_records([update_data])
                    self.logger.info(f"✓ Recorded notification in REDCap for {record_id}")
                except RedcapApiError as e:
                    self.logger.error(f"CRITICAL: Ineligible email sent but failed to record in REDCap: {e}.")

                return True
            else:
                self.logger.error(f"Failed to send email: {response.status_code} - {response.text}")
                return False
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Network error sending email: {e}")
            return False

    def check_for_ineligible_participants(self):
        """Check for ineligible participants who haven't been notified yet"""
        self.logger.info("Checking for ineligible participants to notify...")

        try:
            # Use filterLogic for efficient server-side filtering
            # Logic: (Status is 'ineligible') AND (Timestamp is empty) AND (Email is not empty)
            filter_logic = (
                "[pipeline_processing_status] = 'ineligible' and "
                "[pipeline_ineligible_notification_sent_timestamp] = '' and "
                "[participant_email_a29017_723fd8_6c173d_v2_98aab5] <> ''"
            )

            records = self.redcap.export_records(
                fields=[
                    'record_id',
                    'pipeline_processing_status',
                    'pipeline_ineligibility_reasons',
                    'pipeline_ineligible_notification_sent_timestamp',
                    'participant_email_a29017_723fd8_6c173d_v2_98aab5'
                ],
                filter_logic=filter_logic
            )

            notifications_sent = 0
            errors = 0

            for record in records:
                record_id = record.get('record_id')
                email = record.get('participant_email_a29017_723fd8_6c173d_v2_98aab5', '').strip()
                reasons_str = record.get('pipeline_ineligibility_reasons', '').strip()

                # All records returned by filterLogic are ready for notification
                if email and reasons_str:
                    # Parse reasons (comma-separated)
                    reasons = [r.strip() for r in reasons_str.split(',') if r.strip()]

                    self.logger.info(f"Notifying {email} (Record: {record_id}) - Reasons: {', '.join(reasons)}")

                    if self.send_ineligible_email(record_id, email, reasons):
                        notifications_sent += 1
                    else:
                        errors += 1

            # Summary
            self.logger.info(f"Notification check complete: {notifications_sent} sent, {errors} errors")
            return notifications_sent

        except RedcapApiError as e:
            self.logger.error(f"Error fetching records from REDCap: {e}")
            return 0

    def run_continuous(self, check_interval_minutes=5):
        """Run continuously"""
        self.logger.info(f"Starting ineligible email sender (checking every {check_interval_minutes} minutes)")
        self.logger.info("Using REDCap as Single Source of Truth (SSOT)")

        while True:
            try:
                current_time = datetime.now()
                self.logger.info(f"\n{'='*60}")
                self.logger.info(f"Check at {current_time.strftime('%Y-%m-%d %H:%M:%S')}")

                # Check for ineligible participants
                sent = self.check_for_ineligible_participants()

                if sent > 0:
                    self.logger.info(f"Successfully sent {sent} notifications")
                else:
                    self.logger.info("No new ineligible participants to notify")

                # Sleep until next check
                self.logger.info(f"Sleeping for {check_interval_minutes} minutes...")
                time.sleep(check_interval_minutes * 60)

            except KeyboardInterrupt:
                self.logger.info("\nShutting down ineligible email sender...")
                break
            except (RedcapApiError, requests.exceptions.RequestException) as e:
                self.logger.error(f"API error: {e}")
                self.logger.info(f"Retrying in {check_interval_minutes} minutes...")
                time.sleep(check_interval_minutes * 60)

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Send emails to ineligible participants (SSOT Version)')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--interval', type=int, default=5, help='Check interval in minutes (default: 5)')

    args = parser.parse_args()

    sender = IneligibleEmailSender()

    if args.once:
        sender.check_for_ineligible_participants()
    else:
        sender.run_continuous(check_interval_minutes=args.interval)

if __name__ == "__main__":
    main()