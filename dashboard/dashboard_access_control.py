#!/usr/bin/env python3
"""
Simple Approval-Based Access Control for Stanford REDCap Dashboard
Users request access, admin approves via email link
"""

import os
import sys
import json
import secrets
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, redirect, render_template_string, jsonify, make_response
import requests
import logging
from functools import wraps

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import email sender from common
from common.email_sender import MultiProviderEmailSender, create_providers

# Configuration
STREAMLIT_URL = "http://localhost:8080"
FLASK_PORT = 8082
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'tristan8@stanford.edu')
COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 days

# Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('access_control.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize email sender
email_providers = create_providers()
email_sender = MultiProviderEmailSender(email_providers)

# Database setup
DB_PATH = 'dashboard_access.db'

def init_db():
    """Initialize the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Access requests table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            sunet_id TEXT NOT NULL,
            reason TEXT,
            request_token TEXT UNIQUE NOT NULL,
            access_token TEXT UNIQUE,
            status TEXT DEFAULT 'pending',
            requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_at TIMESTAMP,
            approved_by TEXT,
            last_accessed TIMESTAMP,
            access_count INTEGER DEFAULT 0
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# Templates
REQUEST_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Request Dashboard Access - Stanford REDCap</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background-color: #f5f5f5;
            margin: 0;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }
        .container {
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            width: 100%;
            max-width: 500px;
        }
        .logo {
            text-align: center;
            margin-bottom: 30px;
        }
        .logo h1 {
            color: #8C1515;
            font-size: 24px;
            margin: 10px 0;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            color: #333;
            font-weight: 500;
        }
        input[type="text"], input[type="email"], textarea {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 16px;
            box-sizing: border-box;
        }
        textarea {
            resize: vertical;
            min-height: 100px;
        }
        .btn {
            width: 100%;
            padding: 12px;
            background-color: #8C1515;
            color: white;
            border: none;
            border-radius: 4px;
            font-size: 16px;
            cursor: pointer;
            transition: background-color 0.2s;
        }
        .btn:hover {
            background-color: #6d1010;
        }
        .info {
            background-color: #f8f9fa;
            border-left: 4px solid #8C1515;
            padding: 15px;
            margin-bottom: 20px;
        }
        .success {
            color: #28a745;
            text-align: center;
            margin-top: 20px;
        }
        .error {
            color: #dc3545;
            text-align: center;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <h1>Stanford REDCap Dashboard</h1>
            <p>Precision Neurotherapeutics Lab</p>
        </div>
        
        {% if not submitted %}
        <div class="info">
            <strong>Access Required</strong><br>
            Please submit your information to request access to the dashboard.
            You will be notified via email once your request is approved.
        </div>
        
        <form method="POST" action="/request-access">
            <div class="form-group">
                <label for="name">Full Name</label>
                <input type="text" id="name" name="name" required>
            </div>
            
            <div class="form-group">
                <label for="email">Stanford Email</label>
                <input type="email" id="email" name="email" required 
                       pattern=".*@stanford\.edu$"
                       placeholder="yourname@stanford.edu">
            </div>
            
            <div class="form-group">
                <label for="sunet_id">SUNet ID</label>
                <input type="text" id="sunet_id" name="sunet_id" required
                       placeholder="yoursunet">
            </div>
            
            <div class="form-group">
                <label for="reason">Reason for Access (Optional)</label>
                <textarea id="reason" name="reason" 
                          placeholder="e.g., Lab member, collaborator, etc."></textarea>
            </div>
            
            <button type="submit" class="btn">Request Access</button>
        </form>
        
        {% else %}
        <div class="success">
            <h2>✓ Access Request Submitted</h2>
            <p>Thank you! Your request has been sent to the administrator.</p>
            <p>You will receive an email at <strong>{{ email }}</strong> once your access is approved.</p>
            <p>This typically takes less than 24 hours.</p>
        </div>
        {% endif %}
    </div>
</body>
</html>
'''

APPROVAL_EMAIL_TEMPLATE = '''
New Dashboard Access Request

Name: {name}
Email: {email}
SUNet ID: {sunet_id}
Reason: {reason}
Time: {timestamp}

To approve this request, click here:
{approval_link}

To deny this request, click here:
{denial_link}

Or review all pending requests:
{review_link}
'''

ACCESS_GRANTED_EMAIL_TEMPLATE = '''
Hello {name},

Your request to access the Stanford REDCap Dashboard has been approved!

Click the following link to access the dashboard:
{access_link}

This link is unique to you and will remain valid for 7 days. After that, you may need to request access again.

If you have any questions, please contact the lab administrator.

Best regards,
Stanford Precision Neurotherapeutics Lab
'''

ADMIN_PANEL_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Access Control Admin - Stanford REDCap</title>
    <style>
        body { 
            font-family: Arial, sans-serif; 
            margin: 20px;
            background-color: #f5f5f5;
        }
        .container { 
            max-width: 1000px; 
            margin: 0 auto; 
            background: white;
            padding: 20px;
            border-radius: 8px;
        }
        table { 
            width: 100%; 
            border-collapse: collapse; 
            margin-top: 20px;
        }
        th, td { 
            padding: 10px; 
            text-align: left; 
            border-bottom: 1px solid #ddd;
        }
        th { 
            background-color: #8C1515; 
            color: white;
        }
        .status-pending { color: #ff9800; }
        .status-approved { color: #4caf50; }
        .status-denied { color: #f44336; }
        .btn {
            padding: 5px 10px;
            margin: 2px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
        }
        .btn-approve { background-color: #4caf50; color: white; }
        .btn-deny { background-color: #f44336; color: white; }
        .btn-revoke { background-color: #ff9800; color: white; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Dashboard Access Control</h1>
        
        <h2>Pending Requests</h2>
        <table>
            <tr>
                <th>Name</th>
                <th>Email</th>
                <th>SUNet ID</th>
                <th>Reason</th>
                <th>Requested</th>
                <th>Actions</th>
            </tr>
            {% for req in pending %}
            <tr>
                <td>{{ req.name }}</td>
                <td>{{ req.email }}</td>
                <td>{{ req.sunet_id }}</td>
                <td>{{ req.reason or '-' }}</td>
                <td>{{ req.requested_at }}</td>
                <td>
                    <a href="/admin/approve/{{ req.request_token }}" class="btn btn-approve">Approve</a>
                    <a href="/admin/deny/{{ req.request_token }}" class="btn btn-deny">Deny</a>
                </td>
            </tr>
            {% else %}
            <tr><td colspan="6">No pending requests</td></tr>
            {% endfor %}
        </table>
        
        <h2>Approved Users</h2>
        <table>
            <tr>
                <th>Name</th>
                <th>Email</th>
                <th>SUNet ID</th>
                <th>Approved</th>
                <th>Last Access</th>
                <th>Access Count</th>
                <th>Actions</th>
            </tr>
            {% for req in approved %}
            <tr>
                <td>{{ req.name }}</td>
                <td>{{ req.email }}</td>
                <td>{{ req.sunet_id }}</td>
                <td>{{ req.approved_at }}</td>
                <td>{{ req.last_accessed or 'Never' }}</td>
                <td>{{ req.access_count }}</td>
                <td>
                    <a href="/admin/revoke/{{ req.request_token }}" class="btn btn-revoke">Revoke</a>
                </td>
            </tr>
            {% else %}
            <tr><td colspan="7">No approved users</td></tr>
            {% endfor %}
        </table>
    </div>
</body>
</html>
'''

# Helper functions
def check_access_token(f):
    """Decorator to check if user has valid access token"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.cookies.get('dashboard_access_token')
        
        if not token:
            return redirect('/request-access')
        
        # Verify token in database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, email FROM access_requests 
            WHERE access_token = ? AND status = 'approved'
        ''', (token,))
        
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            return redirect('/request-access')
        
        # Update access stats
        cursor.execute('''
            UPDATE access_requests 
            SET last_accessed = CURRENT_TIMESTAMP,
                access_count = access_count + 1
            WHERE access_token = ?
        ''', (token,))
        conn.commit()
        conn.close()
        
        request.user = {'id': user[0], 'name': user[1], 'email': user[2]}
        return f(*args, **kwargs)
    
    return decorated_function

# Routes
@app.route('/')
def index():
    """Redirect to dashboard or access request"""
    token = request.cookies.get('dashboard_access_token')
    if token:
        # Check if token is valid
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id FROM access_requests 
            WHERE access_token = ? AND status = 'approved'
        ''', (token,))
        if cursor.fetchone():
            conn.close()
            return redirect('/dashboard/')
        conn.close()
    
    return redirect('/request-access')

@app.route('/request-access', methods=['GET', 'POST'])
def request_access():
    """Handle access requests"""
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        sunet_id = request.form.get('sunet_id')
        reason = request.form.get('reason', '')
        
        # Validate Stanford email
        if not email.endswith('@stanford.edu'):
            return render_template_string(REQUEST_TEMPLATE, 
                                        submitted=False,
                                        error="Please use your Stanford email address")
        
        # Generate request token
        request_token = secrets.token_urlsafe(32)
        
        # Save to database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if already requested
        cursor.execute('SELECT id FROM access_requests WHERE email = ?', (email,))
        if cursor.fetchone():
            conn.close()
            return render_template_string(REQUEST_TEMPLATE,
                                        submitted=True,
                                        email=email)
        
        cursor.execute('''
            INSERT INTO access_requests 
            (name, email, sunet_id, reason, request_token)
            VALUES (?, ?, ?, ?, ?)
        ''', (name, email, sunet_id, reason, request_token))
        conn.commit()
        conn.close()
        
        # Send notification email to admin
        base_url = request.host_url.rstrip('/')
        admin_key = os.environ.get('ADMIN_KEY', 'stanford-admin-2024')
        approval_link = f"{base_url}/admin/approve/{request_token}?key={admin_key}&quick=true"
        denial_link = f"{base_url}/admin/deny/{request_token}?key={admin_key}&quick=true"
        review_link = f"{base_url}/admin"
        
        email_body = APPROVAL_EMAIL_TEMPLATE.format(
            name=name,
            email=email,
            sunet_id=sunet_id,
            reason=reason or 'Not specified',
            timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            approval_link=approval_link,
            denial_link=denial_link,
            review_link=review_link
        )
        
        email_sender.send_email(
            ADMIN_EMAIL,
            f"Dashboard Access Request: {name} ({sunet_id})",
            email_body
        )
        
        logger.info(f"Access request submitted: {name} ({email})")
        
        return render_template_string(REQUEST_TEMPLATE,
                                    submitted=True,
                                    email=email)
    
    return render_template_string(REQUEST_TEMPLATE, submitted=False)

@app.route('/dashboard/')
@app.route('/dashboard/<path:path>')
@check_access_token
def proxy_dashboard(path=''):
    """Proxy requests to Streamlit dashboard"""
    # Special handling for Streamlit endpoints
    if path == '_stcore/health':
        return 'ok'
    
    # Construct URL
    streamlit_url = f"{STREAMLIT_URL}/{path}"
    if request.query_string:
        streamlit_url += f"?{request.query_string.decode()}"
    
    # For WebSocket upgrade requests, return error (can't proxy WebSockets easily)
    if request.headers.get('Upgrade') == 'websocket':
        return "WebSocket connections not supported through proxy", 400
    
    # Proxy the request
    try:
        headers = dict(request.headers)
        headers.pop('Host', None)
        headers['X-Dashboard-User'] = request.user['email']
        
        resp = requests.request(
            method=request.method,
            url=streamlit_url,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            stream=True,
            timeout=30
        )
        
        # Get response headers
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(name, value) for (name, value) in resp.headers.items()
                   if name.lower() not in excluded_headers]
        
        # Handle redirects
        if resp.status_code in [301, 302, 303, 307, 308]:
            return '', resp.status_code, headers
        
        # Stream the response
        return resp.content, resp.status_code, headers
        
    except Exception as e:
        logger.error(f"Proxy error for {path}: {e}")
        return f"Error connecting to dashboard: {str(e)}", 502

@app.route('/admin')
def admin_panel():
    """Admin panel - simple auth check via query param"""
    # Simple auth - in production, use proper authentication
    admin_key = request.args.get('key')
    if admin_key != os.environ.get('ADMIN_KEY', 'stanford-admin-2024'):
        return "Unauthorized", 403
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get pending requests
    cursor.execute('''
        SELECT * FROM access_requests 
        WHERE status = 'pending' 
        ORDER BY requested_at DESC
    ''')
    pending = cursor.fetchall()
    
    # Get approved users
    cursor.execute('''
        SELECT * FROM access_requests 
        WHERE status = 'approved' 
        ORDER BY approved_at DESC
    ''')
    approved = cursor.fetchall()
    
    conn.close()
    
    return render_template_string(ADMIN_PANEL_TEMPLATE,
                                pending=pending,
                                approved=approved)

@app.route('/admin/approve/<token>')
def approve_access(token):
    """Approve access request"""
    # Check admin auth
    admin_key = request.args.get('key')
    if admin_key != os.environ.get('ADMIN_KEY', 'stanford-admin-2024'):
        return "Unauthorized", 403
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get request details
    cursor.execute('''
        SELECT name, email FROM access_requests 
        WHERE request_token = ? AND status = 'pending'
    ''', (token,))
    
    user = cursor.fetchone()
    if not user:
        conn.close()
        return "Request not found or already processed", 404
    
    name, email = user
    
    # Generate access token
    access_token = secrets.token_urlsafe(32)
    
    # Update request
    cursor.execute('''
        UPDATE access_requests 
        SET status = 'approved',
            access_token = ?,
            approved_at = CURRENT_TIMESTAMP,
            approved_by = ?
        WHERE request_token = ?
    ''', (access_token, ADMIN_EMAIL, token))
    
    conn.commit()
    conn.close()
    
    # Send approval email
    base_url = request.host_url.rstrip('/')
    access_link = f"{base_url}/grant-access/{access_token}"
    
    email_body = ACCESS_GRANTED_EMAIL_TEMPLATE.format(
        name=name,
        access_link=access_link
    )
    
    email_sender.send_email(
        email,
        "Dashboard Access Approved - Stanford REDCap",
        email_body
    )
    
    logger.info(f"Access approved for: {name} ({email})")
    
    if request.args.get('quick'):
        return f"<h1>Access Approved</h1><p>Access granted to {name} ({email})</p><p><a href='/admin?key={admin_key}'>View all requests</a></p>"
    
    return redirect(f'/admin?key={admin_key}')

@app.route('/admin/deny/<token>')
def deny_access(token):
    """Deny access request"""
    admin_key = request.args.get('key')
    if admin_key != os.environ.get('ADMIN_KEY', 'stanford-admin-2024'):
        return "Unauthorized", 403
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE access_requests 
        SET status = 'denied'
        WHERE request_token = ? AND status = 'pending'
    ''', (token,))
    
    conn.commit()
    conn.close()
    
    if request.args.get('quick'):
        return f"<h1>Access Denied</h1><p>Request has been denied.</p><p><a href='/admin?key={admin_key}'>View all requests</a></p>"
    
    return redirect(f'/admin?key={admin_key}')

@app.route('/grant-access/<token>')
def grant_access(token):
    """Set access cookie when user clicks email link"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT name FROM access_requests 
        WHERE access_token = ? AND status = 'approved'
    ''', (token,))
    
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        return "Invalid or expired access link", 403
    
    # Set cookie and redirect to dashboard
    response = make_response(redirect('/dashboard/'))
    response.set_cookie('dashboard_access_token', token, 
                       max_age=COOKIE_MAX_AGE,
                       secure=True,
                       httponly=True,
                       samesite='Lax')
    
    return response

@app.route('/logout')
def logout():
    """Clear access cookie"""
    response = make_response(redirect('/request-access'))
    response.set_cookie('dashboard_access_token', '', max_age=0)
    return response

if __name__ == '__main__':
    print(f"Starting Dashboard Access Control on port {FLASK_PORT}")
    print(f"Admin panel: http://localhost:{FLASK_PORT}/admin?key={os.environ.get('ADMIN_KEY', 'stanford-admin-2024')}")
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False)
