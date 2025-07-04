import requests
import json
import time
import smtplib
import os
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import logging
from typing import Dict, List, Optional, Tuple
import socket
from abc import ABC, abstractmethod
import enum
from collections import deque
import random
from threading import Lock

# SendGrid imports
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import (
        Mail, From, To, ReplyTo, Category, CustomArg, 
        MailSettings, SandBoxMode, Header
    )
    from python_http_client.exceptions import HTTPError
    SENDGRID_AVAILABLE = True
except ImportError:
    SENDGRID_AVAILABLE = False
    logging.warning("SendGrid not installed. Install with: pip install sendgrid")

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('eligibility_processor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# === EMAIL PROVIDER ABSTRACTION ===

class EmailProvider(ABC):
    """Abstract base class for email providers"""
    
    @abstractmethod
    def send_email(self, email_data: Dict) -> Dict:
        """Send email and return result with success status"""
        pass
    
    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """Test provider connection"""
        pass
    
    @abstractmethod
    def is_healthy(self) -> bool:
        """Check if provider is operational"""
        pass
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """Return provider identifier"""
        pass


# === RATE LIMITER ===

class RateLimiter:
    """Token bucket rate limiter"""
    
    def __init__(self, max_calls: int, time_window: int):
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = deque()
        self.lock = Lock()
        
    def acquire(self):
        """Acquire permission to make a call"""
        with self.lock:
            now = time.time()
            
            # Remove old calls outside the time window
            while self.calls and self.calls[0] <= now - self.time_window:
                self.calls.popleft()
                
            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return True
            else:
                # Calculate wait time
                oldest_call = self.calls[0]
                wait_time = oldest_call + self.time_window - now
                return wait_time


# === CIRCUIT BREAKER ===

class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker for fault tolerance"""
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60, success_threshold: int = 2):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
        
    def record_success(self):
        self.failure_count = 0
        self.success_count += 1
        
        if self.state == CircuitState.HALF_OPEN and self.success_count >= self.success_threshold:
            self.state = CircuitState.CLOSED
            logging.info("Circuit breaker closed after successful recovery")
            
    def record_failure(self):
        self.failure_count += 1
        self.success_count = 0
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logging.warning(f"Circuit breaker opened after {self.failure_count} failures")
            
    def can_attempt(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
            
        if self.state == CircuitState.OPEN:
            if self.last_failure_time and (time.time() - self.last_failure_time) > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logging.info("Circuit breaker entering half-open state")
                return True
            return False
            
        return True  # HALF_OPEN state


# === SENDGRID PROVIDER ===

class SendGridProvider(EmailProvider):
    """SendGrid email provider with Stanford compliance"""
    
    def __init__(self, api_key: str, from_email: str, from_name: str = None, 
                 max_calls_per_minute: int = 100):
        if not SENDGRID_AVAILABLE:
            raise ImportError("SendGrid library not available")
            
        self.client = SendGridAPIClient(api_key=api_key)
        self.from_email = from_email
        self.from_name = from_name or "Stanford Precision Neurotherapeutics Lab"
        self.consecutive_failures = 0
        self.last_failure_time = None
        self.rate_limiter = RateLimiter(max_calls_per_minute, 60)
        
        # Validate Stanford domain
        if not from_email.endswith('@stanford.edu'):
            logger.warning(f"From email ({from_email}) is not a stanford.edu address")
            logger.warning("SendGrid may need sender verification for non-Stanford addresses")
    
    def send_email(self, email_data: Dict) -> Dict:
        """Send email via SendGrid API"""
        # Check rate limit
        result = self.rate_limiter.acquire()
        if isinstance(result, float):
            # Need to wait
            time.sleep(result)
            self.rate_limiter.acquire()
            
        try:
            # Create Mail object with simpler constructor
            from_email = From(self.from_email, self.from_name)
            to_email = To(email_data['to_email'])
            subject = email_data['subject']
            
            # Set content
            content = None
            if 'body' in email_data:
                content = email_data['body']
            elif 'text_content' in email_data:
                content = email_data['text_content']
            
            # Create message with basic constructor
            message = Mail(
                from_email=from_email,
                to_emails=to_email,
                subject=subject,
                plain_text_content=content
            )
            
            # Add HTML content if provided
            if 'html_content' in email_data:
                message.html_content = email_data['html_content']
            elif 'body' in email_data and content:
                message.html_content = content.replace('\n', '<br>')
            
            # Add reply-to if specified
            if 'reply_to' in email_data:
                message.reply_to = ReplyTo(email_data['reply_to'])
            
            # Add categories for tracking
            categories = email_data.get('categories', ['redcap', 'eligibility'])
            if categories:
                for category in categories:
                    message.add_category(Category(category))
            
            # Add custom args for tracking
            custom_args = email_data.get('custom_args', {})
            if custom_args:
                for key, value in custom_args.items():
                    message.add_custom_arg(CustomArg(key, str(value)))
            
            # Add Stanford-specific headers
            # SendGrid uses add_header method with Header objects
            message.add_header(Header('X-Stanford-System', 'REDCap'))
            message.add_header(Header('X-Stanford-Department', os.environ.get('STANFORD_DEPT', 'Psychiatry')))
            message.add_header(Header('X-Mailer', 'Stanford REDCap Processor'))
            
            # Send the email
            response = self.client.send(message)
            
            if response.status_code in [200, 202]:
                self.consecutive_failures = 0
                return {
                    'success': True,
                    'provider': 'sendgrid',
                    'message_id': response.headers.get('X-Message-Id'),
                    'status_code': response.status_code
                }
            else:
                self.consecutive_failures += 1
                self.last_failure_time = time.time()
                return {
                    'success': False,
                    'provider': 'sendgrid',
                    'error': f"Status code: {response.status_code}",
                    'status_code': response.status_code,
                    'body': response.body
                }
                
        except HTTPError as e:
            self.consecutive_failures += 1
            self.last_failure_time = time.time()
            error_info = self._handle_sendgrid_error(e)
            
            # Add specific guidance for 403 errors
            if e.status_code == 403:
                error_info['guidance'] = (
                    "\n\nSendGrid 403 Error - Most likely causes:\n"
                    f"1. The sender address '{self.from_email}' is not verified\n"
                    "2. Domain authentication is incomplete\n"
                    "3. API key lacks proper permissions\n\n"
                    "To fix:\n"
                    "1. Go to SendGrid Dashboard � Settings � Sender Authentication\n"
                    "2. Add and verify the sender address or complete domain authentication\n"
                    "3. Ensure all CNAME records are validated (if using domain auth)\n"
                    "4. For testing, use Single Sender Verification with your email"
                )
            
            return {
                'success': False,
                'provider': 'sendgrid',
                'error': error_info['reason'],
                'details': error_info.get('details'),
                'guidance': error_info.get('guidance'),
                'retry': error_info.get('retry', False),
                'status_code': e.status_code
            }
        except Exception as e:
            self.consecutive_failures += 1
            self.last_failure_time = time.time()
            return {
                'success': False,
                'provider': 'sendgrid',
                'error': str(e)
            }
    
    def _handle_sendgrid_error(self, error: HTTPError) -> Dict:
        """Parse SendGrid error responses"""
        error_mapping = {
            400: {'retry': False, 'reason': 'Bad request - check email format and content'},
            401: {'retry': False, 'reason': 'Authentication failed - check API key'},
            403: {'retry': False, 'reason': 'Forbidden - check API key permissions'},
            413: {'retry': False, 'reason': 'Payload too large - reduce attachment size'},
            429: {'retry': True, 'reason': 'Rate limited', 'wait': int(error.headers.get('Retry-After', 60))},
            500: {'retry': True, 'reason': 'SendGrid server error'},
            502: {'retry': True, 'reason': 'Bad gateway'},
            503: {'retry': True, 'reason': 'Service unavailable'},
            504: {'retry': True, 'reason': 'Gateway timeout'}
        }
        
        status_code = error.status_code
        error_info = error_mapping.get(status_code, {'retry': False, 'reason': 'Unknown error'})
        
        # Parse error body for additional details
        try:
            if hasattr(error, 'body'):
                error_body = error.body
                if isinstance(error_body, dict) and 'errors' in error_body:
                    error_info['details'] = error_body['errors']
                elif isinstance(error_body, str):
                    error_info['details'] = error_body
        except:
            pass
            
        return error_info
    
    def check_sender_verification(self) -> Dict:
        """Check SendGrid sender verification status"""
        try:
            # Check verified senders
            # Get the API key from the client's auth header
            api_key = self.client.api_key
            headers = {'Authorization': f'Bearer {api_key}'}
            response = requests.get('https://api.sendgrid.com/v3/verified_senders', headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                verified_emails = [sender['from_email'] for sender in data.get('results', [])]
                
                return {
                    'verified_senders': verified_emails,
                    'is_verified': self.from_email in verified_emails,
                    'message': f"Sender '{self.from_email}' is {'verified' if self.from_email in verified_emails else 'NOT verified'}"
                }
            else:
                return {
                    'error': f"API returned status {response.status_code}",
                    'message': f"Could not check sender verification: Status {response.status_code}"
                }
            
        except Exception as e:
            return {
                'error': str(e),
                'message': f"Could not check sender verification: {e}"
            }
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test SendGrid connection"""
        try:
            # First check sender verification
            verification = self.check_sender_verification()
            if not verification.get('is_verified', False) and 'error' not in verification:
                return False, (
                    f"SendGrid sender not verified!\n"
                    f"The address '{self.from_email}' is not in your verified senders list.\n"
                    f"Verified senders: {', '.join(verification.get('verified_senders', []))}\n\n"
                    f"To fix: Go to SendGrid Dashboard � Settings � Sender Authentication � Single Sender Verification"
                )
            
            # Test with a simple API call
            from sendgrid.helpers.mail import MailSettings, SandBoxMode
            
            message = Mail(
                from_email=(self.from_email, self.from_name),
                to_emails='test@example.com',
                subject='Connection Test',
                plain_text_content='Test'
            )
            
            # Enable sandbox mode to not actually send
            message.mail_settings = MailSettings()
            message.mail_settings.sandbox_mode = SandBoxMode(True)
            
            response = self.client.send(message)
            
            if response.status_code in [200, 202]:
                return True, "SendGrid connection successful"
            else:
                return False, f"SendGrid returned status code: {response.status_code}"
                
        except Exception as e:
            return False, f"SendGrid connection error: {str(e)}"
    
    def is_healthy(self) -> bool:
        """Check if provider is healthy"""
        # Consider unhealthy after 5 consecutive failures
        if self.consecutive_failures >= 5:
            # Check if we should reset after cooldown period
            if self.last_failure_time and (time.time() - self.last_failure_time) > 300:
                self.consecutive_failures = 0
                return True
            return False
        return True
    
    def get_provider_name(self) -> str:
        return 'sendgrid'


# === SMTP PROVIDERS ===

class StanfordSMTPHandler(EmailProvider):
    """Stanford-specific SMTP handler"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.consecutive_failures = 0
        self.validate_config()
        
    def validate_config(self):
        """Validate Stanford SMTP configuration"""
        # Extract SUNet ID from email or username
        username = self.config['username']
        from_email = self.config['from_email']
        
        # If username contains @, it's likely the full email - extract SUNet ID
        if '@' in username:
            sunet_id = username.split('@')[0]
            logger.warning(f"Username contains '@' - extracting SUNet ID: {sunet_id}")
            self.config['username'] = sunet_id
        else:
            sunet_id = username
        
        # Verify from_email matches the authenticated account
        expected_from = f"{sunet_id}@stanford.edu"
        if from_email.lower() != expected_from.lower():
            logger.error(f"From email ({from_email}) doesn't match SUNet ID ({expected_from})")
            logger.error("This WILL cause 554 5.7.1 authorization errors")
            logger.info(f"Please update SMTP_FROM_EMAIL in .env to: {expected_from}")
            # Auto-fix if possible
            self.config['from_email'] = expected_from
            logger.info(f"Auto-corrected from_email to: {expected_from}")
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test SMTP connection with detailed diagnostics"""
        try:
            logger.info(f"Testing connection to {self.config['server']}:{self.config['port']}")
            
            # Test basic connectivity
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            result = sock.connect_ex((self.config['server'], self.config['port']))
            sock.close()
            
            if result != 0:
                return False, f"Cannot connect to {self.config['server']}:{self.config['port']}"
            
            # Test SMTP connection
            if self.config.get('use_ssl'):
                server = smtplib.SMTP_SSL(self.config['server'], self.config['port'])
            else:
                server = smtplib.SMTP(self.config['server'], self.config['port'])
                server.set_debuglevel(1)  # Enable debug output
                
                if self.config.get('use_tls', True):
                    server.starttls()
            
            # Test authentication
            server.login(self.config['username'], self.config['password'])
            server.quit()
            
            return True, "Connection successful"
            
        except smtplib.SMTPAuthenticationError as e:
            return False, f"Authentication failed - check SUNet ID and password: {e}"
        except Exception as e:
            return False, f"Connection error: {e}"
    
    def send_email(self, email_data: Dict) -> Dict:
        """Send email via SMTP"""
        try:
            msg = MIMEMultipart()
            msg['From'] = self.config['from_email']
            msg['To'] = email_data['to_email']
            msg['Subject'] = email_data['subject']
            
            # Add body
            body = email_data.get('body') or email_data.get('text_content', '')
            msg.attach(MIMEText(body, 'plain'))
            
            # Add Stanford-specific headers
            msg['X-Mailer'] = 'Stanford REDCap Processor'
            msg['Reply-To'] = email_data.get('reply_to', self.config['from_email'])
            
            # Connect with proper settings
            if self.config.get('use_ssl'):
                server = smtplib.SMTP_SSL(self.config['server'], self.config['port'])
            else:
                server = smtplib.SMTP(self.config['server'], self.config['port'])
                if self.config.get('use_tls', True):
                    server.starttls()
            
            server.login(self.config['username'], self.config['password'])
            
            # Send the email
            rejected = server.send_message(msg)
            server.quit()
            
            if rejected:
                self.consecutive_failures += 1
                return {
                    'success': False,
                    'provider': self.get_provider_name(),
                    'error': f"Recipients rejected: {rejected}"
                }
            
            self.consecutive_failures = 0
            return {
                'success': True,
                'provider': self.get_provider_name(),
                'message': "Email sent successfully"
            }
            
        except smtplib.SMTPRecipientsRefused as e:
            self.consecutive_failures += 1
            error_detail = str(e.recipients)
            if '554' in error_detail and '5.7.1' in error_detail:
                return {
                    'success': False,
                    'provider': self.get_provider_name(),
                    'error': "Authorization denied - Stanford SMTP restrictions",
                    'guidance': (
                        "\n\nStanford SMTP 554 5.7.1 Error - Most likely causes:\n"
                        "1. SMTP AUTH is disabled in Microsoft 365 (most common)\n"
                        "2. Account needs app-specific password with 2FA\n"
                        "3. From address doesn't match authenticated account\n\n"
                        "To fix:\n"
                        "1. Contact Stanford IT to enable SMTP AUTH for your account\n"
                        "2. Use Microsoft 365 Admin Center � Users � Mail settings � Enable 'Authenticated SMTP'\n"
                        "3. Consider using OAuth 2.0 instead of basic auth (future-proof)\n"
                        f"4. Verify from address is exactly: {self.config['from_email']}"
                    )
                }
            return {
                'success': False,
                'provider': self.get_provider_name(),
                'error': f"Recipient refused: {error_detail}"
            }
        except smtplib.SMTPAuthenticationError as e:
            self.consecutive_failures += 1
            return {
                'success': False,
                'provider': self.get_provider_name(),
                'error': f"SMTP authentication failed: {e}",
                'guidance': (
                    "\n\nAuthentication failed - Check:\n"
                    f"1. Username should be: {self.config['username']}\n"
                    f"2. From email should be: {self.config['username']}@stanford.edu\n"
                    "3. Password is correct and not expired\n"
                    "4. Account is not locked or disabled"
                )
            }
        except smtplib.SMTPException as e:
            self.consecutive_failures += 1
            return {
                'success': False,
                'provider': self.get_provider_name(),
                'error': f"SMTP error: {e}"
            }
        except Exception as e:
            self.consecutive_failures += 1
            return {
                'success': False,
                'provider': self.get_provider_name(),
                'error': f"Unexpected error: {e}"
            }
    
    def is_healthy(self) -> bool:
        return self.consecutive_failures < 3
    
    def get_provider_name(self) -> str:
        return 'stanford_smtp'


class GenericSMTPHandler(EmailProvider):
    """Generic SMTP handler for non-Stanford servers"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.consecutive_failures = 0
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test SMTP connection"""
        try:
            if self.config.get('use_ssl'):
                server = smtplib.SMTP_SSL(self.config['server'], self.config['port'])
            else:
                server = smtplib.SMTP(self.config['server'], self.config['port'])
                if self.config.get('use_tls', True):
                    server.starttls()
            
            server.login(self.config['username'], self.config['password'])
            server.quit()
            
            return True, "Connection successful"
            
        except Exception as e:
            return False, f"Connection error: {e}"
    
    def send_email(self, email_data: Dict) -> Dict:
        """Send email via generic SMTP"""
        try:
            msg = MIMEMultipart()
            msg['From'] = self.config['from_email']
            msg['To'] = email_data['to_email']
            msg['Subject'] = email_data['subject']
            msg['Reply-To'] = email_data.get('reply_to', self.config.get('reply_to', self.config['from_email']))
            
            # Add body
            body = email_data.get('body') or email_data.get('text_content', '')
            msg.attach(MIMEText(body, 'plain'))
            
            # Connect to SMTP server
            if self.config.get('use_ssl'):
                server = smtplib.SMTP_SSL(self.config['server'], self.config['port'])
            else:
                server = smtplib.SMTP(self.config['server'], self.config['port'])
                if self.config.get('use_tls', True):
                    server.starttls()
            
            server.login(self.config['username'], self.config['password'])
            server.send_message(msg)
            server.quit()
            
            self.consecutive_failures = 0
            return {
                'success': True,
                'provider': self.get_provider_name(),
                'message': "Email sent successfully"
            }
            
        except Exception as e:
            self.consecutive_failures += 1
            return {
                'success': False,
                'provider': self.get_provider_name(),
                'error': str(e)
            }
    
    def is_healthy(self) -> bool:
        return self.consecutive_failures < 3
    
    def get_provider_name(self) -> str:
        return f"smtp_{self.config['server']}"


# === MULTI-PROVIDER EMAIL SENDER ===

class MultiProviderEmailSender:
    """Email sender supporting multiple providers with circuit breaker"""
    
    def __init__(self, providers: List[EmailProvider], max_retries: int = 3):
        self.providers = providers
        self.max_retries = max_retries
        self.circuit_breakers = {
            provider.get_provider_name(): CircuitBreaker() 
            for provider in providers
        }
        self.metrics = EmailMetrics()
    
    def send_email(self, to_email: str, subject: str, body: str, **kwargs) -> bool:
        """Send email using available providers with fallback"""
        email_data = {
            'to_email': to_email,
            'subject': subject,
            'body': body,
            **kwargs
        }
        
        result = self._send_with_providers(email_data)
        self.metrics.record_send(result)
        
        if result['success']:
            logger.info(f" Email sent to {to_email} via {result['provider']}")
            return True
        else:
            logger.error(f" Failed to send email to {to_email}: {result.get('error')}")
            # Show guidance if available
            if 'guidance' in result:
                logger.info(result['guidance'])
            return False
    
    def _send_with_providers(self, email_data: Dict) -> Dict:
        """Try to send email using available providers"""
        last_error = None
        attempts = []
        
        # Determine provider order based on recipient
        is_stanford_recipient = email_data['to_email'].lower().endswith('@stanford.edu')
        
        # Sort providers by priority
        if is_stanford_recipient:
            # For Stanford recipients, prefer SendGrid then Stanford SMTP
            provider_order = sorted(self.providers, 
                key=lambda p: (0 if p.get_provider_name() == 'sendgrid' else 
                              1 if p.get_provider_name() == 'stanford_smtp' else 2))
        else:
            # For external recipients, prefer SendGrid then other SMTP
            provider_order = sorted(self.providers,
                key=lambda p: (0 if p.get_provider_name() == 'sendgrid' else 
                              2 if p.get_provider_name() == 'stanford_smtp' else 1))
        
        for provider in provider_order:
            provider_name = provider.get_provider_name()
            circuit_breaker = self.circuit_breakers[provider_name]
            
            # Skip if circuit breaker is open
            if not circuit_breaker.can_attempt():
                logger.info(f"Skipping {provider_name} - circuit breaker open")
                continue
                
            # Skip if provider is unhealthy
            if not provider.is_healthy():
                logger.warning(f"Skipping {provider_name} - provider unhealthy")
                continue
                
            # Attempt to send with retries
            for attempt in range(self.max_retries):
                try:
                    logger.info(f"Attempting to send via {provider_name} (attempt {attempt + 1}/{self.max_retries})")
                    result = provider.send_email(email_data)
                    
                    attempts.append({
                        'provider': provider_name,
                        'attempt': attempt + 1,
                        'result': result
                    })
                    
                    if result['success']:
                        circuit_breaker.record_success()
                        return {
                            'success': True,
                            'provider': provider_name,
                            'attempts': attempts,
                            **result
                        }
                    else:
                        last_error = result.get('error', 'Unknown error')
                        
                        # Log guidance if available
                        if 'guidance' in result:
                            logger.info(result['guidance'])
                        
                        # Check if we should retry based on error type
                        if result.get('retry', False) or result.get('status_code') in [429, 500, 502, 503, 504]:
                            # Transient error - retry with backoff
                            if attempt < self.max_retries - 1:
                                delay = min(2 ** attempt + random.uniform(0, 1), 30)
                                logger.info(f"Retrying after {delay:.1f} seconds...")
                                time.sleep(delay)
                        else:
                            # Permanent error - don't retry
                            logger.error(f"Permanent error from {provider_name}: {last_error}")
                            break
                            
                except Exception as e:
                    last_error = str(e)
                    logger.error(f"Exception from {provider_name}: {e}")
                    attempts.append({
                        'provider': provider_name,
                        'attempt': attempt + 1,
                        'error': str(e)
                    })
                    
                    if attempt < self.max_retries - 1:
                        delay = min(2 ** attempt + random.uniform(0, 1), 30)
                        time.sleep(delay)
                        
            # Record failure for this provider
            circuit_breaker.record_failure()
            
        # All providers failed
        return {
            'success': False,
            'error': f"All providers failed. Last error: {last_error}",
            'attempts': attempts,
            'guidance': attempts[-1]['result'].get('guidance') if attempts and 'result' in attempts[-1] else None
        }
    
    def test_all_providers(self):
        """Test all configured providers"""
        logger.info("\n=== Testing Email Providers ===")
        for provider in self.providers:
            success, message = provider.test_connection()
            if success:
                logger.info(f" {provider.get_provider_name()}: {message}")
            else:
                logger.error(f" {provider.get_provider_name()}: {message}")
    
    def get_metrics(self) -> Dict:
        """Get email sending metrics"""
        return self.metrics.get_summary()


# === EMAIL METRICS ===

class EmailMetrics:
    """Track email sending metrics"""
    
    def __init__(self):
        self.sent_count = 0
        self.failed_count = 0
        self.provider_stats = {}
        
    def record_send(self, result: Dict):
        if result['success']:
            self.sent_count += 1
        else:
            self.failed_count += 1
            
        provider = result.get('provider', 'unknown')
        if provider not in self.provider_stats:
            self.provider_stats[provider] = {'success': 0, 'failure': 0}
            
        if result['success']:
            self.provider_stats[provider]['success'] += 1
        else:
            self.provider_stats[provider]['failure'] += 1
            
    def get_summary(self) -> Dict:
        total = self.sent_count + self.failed_count
        return {
            'total_sent': self.sent_count,
            'total_failed': self.failed_count,
            'success_rate': self.sent_count / total if total > 0 else 0,
            'provider_stats': self.provider_stats
        }


# === REDCAP PROCESSOR ===

class REDCapEligibilityProcessor:
    def __init__(self, api_url: str, api_token: str, email_sender: MultiProviderEmailSender):
        self.api_url = api_url
        self.api_token = api_token
        self.email_sender = email_sender
        self.rate_limit_delay = 2  # Delay between REDCap API calls
        
    def get_next_ids(self):
        """Get the next available IDs for HC and MDD"""
        data = {
            'token': self.api_token,
            'content': 'record',
            'format': 'json',
            'fields': 'study_id'
        }
        
        response = requests.post(self.api_url, data=data)
        records = json.loads(response.text)
        
        hc_max = 3465
        mdd_max = 10925
        
        for r in records:
            sid = r.get('study_id', '')
            if sid.startswith('HC-'):
                try:
                    num = int(sid.split('-')[1])
                    hc_max = max(hc_max, num)
                except: pass
            elif sid.startswith('MDD-'):
                try:
                    num = int(sid.split('-')[1])
                    mdd_max = max(mdd_max, num)
                except: pass
        
        return hc_max + 1, mdd_max + 1
    
    def get_next_id(self, is_hc: bool):
        """Get the next available numeric ID for HC or MDD"""
        data = {
            'token': self.api_token,
            'content': 'record',
            'format': 'json',
            'fields': 'study_id'
        }
        
        response = requests.post(self.api_url, data=data)
        records = json.loads(response.text)
        
        if is_hc:
            # HC IDs are < 10000, starting from 3466
            max_hc = 3465
            for r in records:
                sid = r.get('study_id', '')
                if sid:
                    try:
                        # Extract numeric ID
                        if '-' in sid and sid.startswith('HC-'):
                            num = int(sid.split('-')[1])
                        else:
                            num = int(sid)
                        # Only consider IDs < 10000 for HC
                        if num < 10000:
                            max_hc = max(max_hc, num)
                    except:
                        pass
            return max_hc + 1
        else:
            # MDD IDs are >= 10000, starting from 10926
            max_mdd = 10925
            for r in records:
                sid = r.get('study_id', '')
                if sid:
                    try:
                        # Extract numeric ID
                        if '-' in sid and sid.startswith('MDD-'):
                            num = int(sid.split('-')[1])
                        else:
                            num = int(sid)
                        # Only consider IDs >= 10000 for MDD
                        if num >= 10000:
                            max_mdd = max(max_mdd, num)
                    except:
                        pass
            return max_mdd + 1
    
    def send_eligibility_email(self, email: str, study_id: str, record_id: str) -> bool:
        """Send eligibility notification email"""
        subject = f"Stanford Neuroscience Study Eligibility - Participant {study_id}"
        
        # Generate personalized scheduling link
        scheduling_link = self.generate_scheduling_link(study_id, record_id)
        
        body = f"""Hello from the Stanford Neuroscience Institute!

I am reaching out from the Precision Neurotherapeutics Lab at Stanford University because you recently filled out the screening survey for one of our studies. Based on your responses, you may be eligible to participate in the study!

Your Study ID is: {study_id}

Measuring brain activity in humans is critical to better understand important cognitive processes (memory, language, vision) and gain insight into brain diseases. We have developed a new and improved way to quantify how the brain is connected using EEG brain recordings after applying Transcranial Magnetic Stimulation (TMS), a non-invasive and safe method that has been around for 30+ years. There are some signals in this methodology that we need to better understand before this tool can be helpful. That's where we could use your help!

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

{scheduling_link}

This secure link is unique to you and will allow you to:
- View available appointment times
- Select a convenient time for your consent session
- Receive an immediate confirmation email with Zoom details

**Important Notes:**
- The consent session will be conducted via Zoom
- Please ensure you have a quiet, private space available
- The session typically takes about one hour

If you have any questions or need assistance with scheduling, please don't hesitate to contact us by replying to this email.

Thank you so much for your interest in our study!

Best,
Stanford Precision Neurotherapeutics Lab
Department of Psychiatry and Behavioral Sciences
Stanford University Medical Center"""
        
        # Apply rate limiting
        time.sleep(self.rate_limit_delay)
        
        # Add custom tracking data
        kwargs = {
            'categories': ['redcap', 'eligibility', 'neuroscience'],
            'custom_args': {
                'study_id': study_id,
                'record_id': record_id,
                'email_type': 'eligibility_notification',
                'department': 'psychiatry',
                'scheduler_type': 'stanford_scheduler'
            },
            'reply_to': os.environ.get('STUDY_COORDINATOR_EMAIL', 'noreply@stanford.edu')
        }
        
        return self.email_sender.send_email(email, subject, body, **kwargs)
    
    
    def generate_scheduling_link(self, study_id: str, record_id: str) -> str:
        """Generate a personalized scheduling link for a participant"""
        try:
            import requests
            
            # Use the internal scheduler API
            response = requests.post(
                'http://localhost:8081/api/generate-scheduling-link',
                json={'study_id': study_id, 'record_id': record_id},
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                link = data.get('link', '')
                
                # Use configured public URL
                try:
                    from common.scheduler_config import SCHEDULER_BASE_URL
                    # Extract just the token part
                    if '/schedule/' in link:
                        token = link.split('/schedule/')[-1]
                        link = f"{SCHEDULER_BASE_URL}/schedule/{token}"
                except ImportError:
                    # Fallback if config not found
                    pass
                    
                return link
            else:
                logger.error(f"Failed to generate scheduling link: {response.status_code}")
                return "[Scheduling link temporarily unavailable - please contact study coordinator]"
        except Exception as e:
            logger.error(f"Error generating scheduling link: {e}")
            return "[Scheduling link temporarily unavailable - please contact study coordinator]"

    def mark_email_sent(self, record_id: str):
        """Mark that email has been sent for this record"""
        data = {
            'token': self.api_token,
            'content': 'record',
            'format': 'json',
            'data': json.dumps([{
                'record_id': record_id,
                'eligibility_email_sent': '1',
                'email_sent_timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }]),
            'overwriteBehavior': 'overwrite'
        }
        response = requests.post(self.api_url, data=data)
        if response.status_code != 200:
            logger.error(f"Failed to mark email sent for record {record_id}")
    
    def test_configuration(self) -> bool:
        """Test REDCap API and email configuration"""
        logger.info("\n=== Testing Configuration ===")
        
        # Test REDCap API
        logger.info("Testing REDCap API connection...")
        try:
            data = {
                'token': self.api_token,
                'content': 'record',
                'format': 'json',
                'fields': 'record_id',
                'records': '1'
            }
            response = requests.post(self.api_url, data=data)
            if response.status_code == 200:
                logger.info(" REDCap API connection successful")
            else:
                logger.error(f" REDCap API error: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f" REDCap API connection failed: {e}")
            return False
        
        # Test email providers
        self.email_sender.test_all_providers()
        
        return True
    
    def process_eligible_records(self, dry_run: bool = False) -> List[Dict]:
        """Main processing function"""
        try:
            # Get all records
            data = {
                'token': self.api_token,
                'content': 'record',
                'format': 'json',
                'fields': 'record_id,qids_score,study_id,is_eligible_basic,participant_email,eligibility_email_sent'
            }
            
            response = requests.post(self.api_url, data=data)
            if response.status_code != 200:
                logger.error(f"Failed to fetch records: {response.text}")
                return []
                
            records = json.loads(response.text)
            
            # Process eligible records
            processed = []
            failed = []
            
            for r in records:
                # Skip if already processed
                if r.get('eligibility_email_sent') == '1':
                    continue
                    
                # Only process eligible participants
                if r.get('is_eligible_basic') == '1' and r.get('participant_email'):
                    record_id = r['record_id']
                    email = r['participant_email']
                    qids = int(r.get('qids_score', 0))
                    
                    if dry_run:
                        logger.info(f"[DRY RUN] Would process record {record_id} -> {email}")
                        continue
                    
                    # Assign Study ID if not already assigned
                    if not r.get('study_id'):
                        # Determine if HC or MDD based on QIDS score
                        is_hc = qids < 11
                        
                        # Get next ID for the appropriate category
                        study_id = str(self.get_next_id(is_hc))
                        
                        # Update REDCap with Study ID
                        update_data = {
                            'token': self.api_token,
                            'content': 'record',
                            'format': 'json',
                            'data': json.dumps([{
                                'record_id': record_id,
                                'study_id': study_id
                            }]),
                            'overwriteBehavior': 'overwrite'
                        }
                        response = requests.post(self.api_url, data=update_data)
                        if response.status_code == 200:
                            logger.info(f" Assigned {study_id} to record {record_id} (Category: {'HC' if is_hc else 'MDD'})")
                        else:
                            logger.error(f"Failed to assign Study ID to record {record_id}")
                            continue
                    else:
                        study_id = r['study_id']
                    
                    # Send email
                    if self.send_eligibility_email(email, study_id, record_id):
                        self.mark_email_sent(record_id)
                        # Determine category from study ID for logging
                        try:
                            id_num = int(study_id) if study_id.isdigit() else int(study_id.split('-')[-1])
                            category = 'HC' if id_num < 10000 else 'MDD'
                        except:
                            category = 'Unknown'
                        
                        processed.append({
                            'record_id': record_id,
                            'study_id': study_id,
                            'email': email,
                            'category': category
                        })
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
            return []


# === PROVIDER FACTORY ===

def create_providers() -> List[EmailProvider]:
    """Create and configure email providers"""
    providers = []
    
    # Add SendGrid as primary provider (if available and configured)
    if SENDGRID_AVAILABLE and os.getenv('SENDGRID_API_KEY'):
        try:
            sendgrid_provider = SendGridProvider(
                api_key=os.getenv('SENDGRID_API_KEY'),
                from_email=os.getenv('SENDGRID_FROM_EMAIL', 'noreply@stanford.edu'),
                from_name=os.getenv('SENDGRID_FROM_NAME', 'Stanford Precision Neurotherapeutics Lab')
            )
            providers.append(sendgrid_provider)
            logger.info(" SendGrid provider configured as primary")
        except Exception as e:
            logger.error(f" Failed to configure SendGrid: {e}")
    else:
        logger.warning("SendGrid not configured - using SMTP only")
    
    # Stanford SMTP Configuration
    STANFORD_SMTP = {
        'server': os.getenv('SMTP_SERVER', 'smtp.stanford.edu'),
        'port': int(os.getenv('SMTP_PORT', '587')),
        'use_ssl': os.getenv('SMTP_USE_SSL', 'False').lower() == 'true',
        'use_tls': os.getenv('SMTP_USE_TLS', 'True').lower() == 'true',
        'username': os.getenv('SMTP_USERNAME', '').replace('@stanford.edu', ''),  # Ensure SUNet ID only
        'password': os.getenv('SMTP_PASSWORD'),
        'from_email': os.getenv('SMTP_FROM_EMAIL')
    }
    
    if all([STANFORD_SMTP['username'], STANFORD_SMTP['password'], STANFORD_SMTP['from_email']]):
        providers.append(StanfordSMTPHandler(STANFORD_SMTP))
        logger.info(" Stanford SMTP configured")
    
    # Alternative SMTP Configuration (for external emails)
    if os.getenv('ALT_SMTP_SERVER'):
        ALTERNATIVE_SMTP = {
            'server': os.getenv('ALT_SMTP_SERVER'),
            'port': int(os.getenv('ALT_SMTP_PORT', '587')),
            'use_ssl': os.getenv('ALT_SMTP_USE_SSL', 'False').lower() == 'true',
            'use_tls': os.getenv('ALT_SMTP_USE_TLS', 'True').lower() == 'true',
            'username': os.getenv('ALT_SMTP_USERNAME'),
            'password': os.getenv('ALT_SMTP_PASSWORD'),
            'from_email': os.getenv('ALT_SMTP_FROM_EMAIL'),
            'reply_to': STANFORD_SMTP.get('from_email', 'noreply@stanford.edu')
        }
        
        if all([ALTERNATIVE_SMTP['username'], ALTERNATIVE_SMTP['password'], ALTERNATIVE_SMTP['from_email']]):
            providers.append(GenericSMTPHandler(ALTERNATIVE_SMTP))
            logger.info(" Alternative SMTP configured")
    
    # Fallback SMTP for Stanford (unencrypted - use with caution)
    if os.getenv('USE_STANFORD_UNENCRYPTED', 'False').lower() == 'true':
        FALLBACK_SMTP = {
            'server': 'smtp-unencrypted.stanford.edu',
            'port': 25,
            'use_ssl': False,
            'use_tls': False,
            'username': STANFORD_SMTP['username'],
            'password': STANFORD_SMTP['password'],
            'from_email': STANFORD_SMTP['from_email']
        }
        providers.append(StanfordSMTPHandler(FALLBACK_SMTP))
        logger.warning("� Fallback unencrypted SMTP enabled - use with caution")
    
    return providers


# === MAIN EXECUTION ===

if __name__ == "__main__":
    # Load configuration from environment variables
    API_URL = "https://redcap.stanford.edu/api/"
    API_TOKEN = os.getenv('REDCAP_API_TOKEN')
    
    if not API_TOKEN:
        logger.error("REDCAP_API_TOKEN not found in environment variables")
        exit(1)
    
    # Create providers
    providers = create_providers()
    
    if not providers:
        logger.error("No email providers configured")
        exit(1)
    
    # Create email sender with all configured providers
    email_sender = MultiProviderEmailSender(providers)
    
    # Create processor
    processor = REDCapEligibilityProcessor(API_URL, API_TOKEN, email_sender)
    
    logger.info("Starting REDCap Eligibility Processor...")
    logger.info(f"Configured providers: {[p.get_provider_name() for p in providers]}")
    logger.info("-" * 60)
    
    # Show important warnings
    for provider in providers:
        if provider.get_provider_name() == 'sendgrid':
            from_email = getattr(provider, 'from_email', 'unknown')
            logger.warning("\n�  SendGrid configured - ensure sender is verified!")
            logger.warning(f"   Sender address: {from_email}")
            logger.warning("   Run option 6 (diagnostics) to check verification status")
            break
    
    # Test configuration
    if not processor.test_configuration():
        logger.error("Configuration test failed. Please check settings.")
        logger.error("Run option 6 (diagnostics) for more details.")
        # Don't exit - let user run diagnostics
    else:
        logger.info("\n Basic configuration tests passed")
    
    # Interactive menu
    logger.info("\n=== Options ===")
    logger.info("1. Send test email")
    logger.info("2. Check eligible records (dry run)")
    logger.info("3. Process records (send emails)")
    logger.info("4. Run continuously")
    logger.info("5. Show email metrics")
    logger.info("6. Run diagnostics")
    logger.info("7. Exit")
    
    try:
        choice = input("\nEnter choice (1-7): ").strip()
        
        if choice == '1':
            test_email = input("Enter test email address: ").strip()
            test_subject = "REDCap Processor Test"
            test_body = "This is a test email from the REDCap processor. If you receive this, email sending is configured correctly."
            
            logger.info(f"\nSending test email to {test_email}...")
            if email_sender.send_email(test_email, test_subject, test_body):
                logger.info(" Test email sent successfully")
            else:
                logger.error(" Test email failed")
                logger.info("\n=� Troubleshooting tips:")
                logger.info("   - Run option 6 (diagnostics) to check sender verification")
                logger.info("   - Check the error messages above for specific guidance")
                logger.info("   - For SendGrid 403: Verify sender in SendGrid dashboard")
                logger.info("   - For Stanford 554: Contact IT to enable SMTP AUTH")
                
        elif choice == '2':
            logger.info("\n=== Dry Run Mode ===")
            processor.process_eligible_records(dry_run=True)
            
        elif choice == '3':
            logger.info("\n=== Processing Records ===")
            confirm = input("Send emails to eligible participants? (yes/no): ").strip()
            if confirm.lower() == 'yes':
                processed = processor.process_eligible_records()
                if processed:
                    logger.info(f"\n Successfully processed {len(processed)} participants")
                    
                    # Show metrics
                    metrics = email_sender.get_metrics()
                    logger.info(f"\nEmail Metrics:")
                    logger.info(f"  Total sent: {metrics['total_sent']}")
                    logger.info(f"  Total failed: {metrics['total_failed']}")
                    logger.info(f"  Success rate: {metrics['success_rate']:.1%}")
                    
        elif choice == '4':
            logger.info("\n=== Running Continuously ===")
            logger.info("Press Ctrl+C to stop")
            
            while True:
                try:
                    processed = processor.process_eligible_records()
                    
                    if processed:
                        logger.info(f"\n{'='*60}")
                        logger.info(f"Processed {len(processed)} eligible participants:")
                        for p in processed:
                            logger.info(f"  - Record {p['record_id']}: {p['study_id']} � {p['email']}")
                        logger.info(f"{'='*60}")
                    
                    time.sleep(60)  # Check every minute
                    
                except KeyboardInterrupt:
                    logger.info("\nStopping processor...")
                    break
                except Exception as e:
                    logger.error(f"\nUnexpected error: {e}")
                    time.sleep(60)
                    
        elif choice == '5':
            metrics = email_sender.get_metrics()
            logger.info(f"\n=== Email Metrics ===")
            logger.info(f"Total sent: {metrics['total_sent']}")
            logger.info(f"Total failed: {metrics['total_failed']}")
            logger.info(f"Success rate: {metrics['success_rate']:.1%}")
            logger.info(f"\nProvider breakdown:")
            for provider, stats in metrics['provider_stats'].items():
                logger.info(f"  {provider}: {stats['success']} sent, {stats['failure']} failed")
                
        elif choice == '6':
            logger.info("\n=== Running Diagnostics ===")
            
            # Check SendGrid sender verification if configured
            for provider in providers:
                if provider.get_provider_name() == 'sendgrid':
                    logger.info("\nChecking SendGrid sender verification...")
                    if hasattr(provider, 'check_sender_verification'):
                        verification = provider.check_sender_verification()
                        if 'error' in verification:
                            logger.error(f"  Error: {verification['error']}")
                        else:
                            current_from = getattr(provider, 'from_email', 'unknown')
                            logger.info(f"  Current sender: {current_from}")
                            logger.info(f"  Verification status: {' Verified' if verification['is_verified'] else 'L NOT Verified'}")
                            logger.info(f"  Verified senders: {', '.join(verification['verified_senders']) or 'None'}")
                            
                            if not verification['is_verified']:
                                logger.warning("\n  �  ACTION REQUIRED:")
                                logger.warning("  1. Go to https://app.sendgrid.com/settings/sender_auth")
                                logger.warning("  2. Click 'Verify a Single Sender'")
                                logger.warning(f"  3. Add and verify: {current_from}")
            
            logger.info("\nChecking email configuration...")
            logger.info(f"  SendGrid configured: {'Yes' if any(p.get_provider_name() == 'sendgrid' for p in providers) else 'No'}")
            logger.info(f"  SMTP configured: {'Yes' if any('smtp' in p.get_provider_name() for p in providers) else 'No'}")
            
            logger.info("\nFor Stanford SMTP issues:")
            logger.info("  If you're getting 554 5.7.1 errors:")
            logger.info("  1. SMTP AUTH may be disabled (most common)")
            logger.info("  2. Contact Stanford IT to enable it")
            logger.info("  3. Or use https://accounts.stanford.edu to check 2FA settings")
            
        else:
            logger.info("Exiting...")
            
    except KeyboardInterrupt:
        logger.info("\nExiting...")