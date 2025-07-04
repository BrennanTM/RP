#!/usr/bin/env python3
"""
Fix Stanford REDCap Setup Issues
This script fixes common issues after migration
"""

import os
import sys
import shutil
import secrets
import re
from pathlib import Path

# Colors for output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.BLUE}=== {text} ==={Colors.END}")

def print_success(text):
    print(f"{Colors.GREEN}✓{Colors.END} {text}")

def print_error(text):
    print(f"{Colors.RED}✗{Colors.END} {text}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠{Colors.END} {text}")

def print_info(text):
    print(f"  {text}")

# Project paths
HOME = os.path.expanduser("~")
PROJECT_ROOT = os.path.join(HOME, "stanford_redcap")
DESKTOP = os.path.join(HOME, "Desktop")

def find_source_file(filenames, search_dirs=None):
    """Find a file from a list of possible names in search directories"""
    if search_dirs is None:
        search_dirs = [DESKTOP, HOME, os.path.join(HOME, "stanford_scheduler")]
    
    if isinstance(filenames, str):
        filenames = [filenames]
    
    for directory in search_dirs:
        for filename in filenames:
            filepath = os.path.join(directory, filename)
            if os.path.exists(filepath):
                return filepath
    return None

def copy_missing_files():
    """Copy missing files from their source locations"""
    print_header("Copying Missing Files")
    
    files_to_copy = [
        {
            'names': ['email_sender.py', '2.py'],
            'destination': os.path.join(PROJECT_ROOT, 'common', 'email_sender.py'),
            'description': 'Email sender module'
        },
        {
            'names': ['confirm.py'],
            'destination': os.path.join(PROJECT_ROOT, 'confirmations', 'confirm.py'),
            'description': 'Confirmations module'
        },
        {
            'names': ['dashboard_calendar.py'],
            'destination': os.path.join(PROJECT_ROOT, 'dashboard', 'dashboard_calendar.py'),
            'description': 'Dashboard calendar module'
        }
    ]
    
    for file_info in files_to_copy:
        source = find_source_file(file_info['names'])
        dest = file_info['destination']
        
        if source:
            try:
                shutil.copy2(source, dest)
                print_success(f"Copied {file_info['description']} from {source}")
            except Exception as e:
                print_error(f"Failed to copy {file_info['description']}: {e}")
        else:
            print_warning(f"{file_info['description']} not found in any expected location")
            if 'dashboard_calendar' in dest:
                create_minimal_calendar()

def create_minimal_calendar():
    """Create a minimal dashboard_calendar.py if the real one is missing"""
    print_info("Creating minimal dashboard_calendar.py...")
    
    calendar_content = '''"""
Minimal calendar module for dashboard
Replace this with the full calendar implementation when available
"""

import streamlit as st

def render_calendar_tab():
    """Render a placeholder calendar tab"""
    st.header("📅 Appointment Calendar")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.info("""
        **Calendar Functionality Coming Soon**
        
        The in-house scheduling system is being configured. Once complete, you'll be able to:
        - View all scheduled appointments
        - Generate scheduling links for participants
        - Track appointment status
        - Export calendar data
        """)
    
    with col2:
        st.metric("Scheduled Appointments", "---")
        st.metric("This Week", "---")
        st.metric("This Month", "---")
    
    st.markdown("---")
    st.caption("To enable full calendar functionality, ensure scheduler.py is running on port 8081")
'''
    
    calendar_path = os.path.join(PROJECT_ROOT, 'dashboard', 'dashboard_calendar.py')
    with open(calendar_path, 'w') as f:
        f.write(calendar_content)
    print_success("Created minimal dashboard_calendar.py")

def fix_imports_in_file(filepath, fixes):
    """Fix import statements in a Python file"""
    if not os.path.exists(filepath):
        return False
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    original_content = content
    
    # Apply fixes
    for old_pattern, new_pattern in fixes.items():
        content = re.sub(old_pattern, new_pattern, content)
    
    # Add sys.path if needed and not already present
    if 'from common.' in content and 'sys.path.append' not in content:
        import_block = '''import sys
sys.path.append('/home/tristan8/stanford_redcap')

'''
        content = import_block + content
    
    # Write back if changed
    if content != original_content:
        with open(filepath, 'w') as f:
            f.write(content)
        return True
    return False

def fix_import_paths():
    """Fix import paths in all service files"""
    print_header("Fixing Import Paths")
    
    files_to_fix = [
        {
            'path': os.path.join(PROJECT_ROOT, 'scheduler', 'scheduler.py'),
            'name': 'scheduler.py',
            'fixes': {
                r'from email_sender import': 'from common.email_sender import',
                r'from 2 import': 'from common.email_sender import',
            }
        },
        {
            'path': os.path.join(PROJECT_ROOT, 'confirmations', 'confirm.py'),
            'name': 'confirm.py',
            'fixes': {
                r'from email_sender import': 'from common.email_sender import',
                r'from 2 import': 'from common.email_sender import',
            }
        },
        {
            'path': os.path.join(PROJECT_ROOT, 'dashboard', 'dashboard.py'),
            'name': 'dashboard.py',
            'fixes': {}  # Will handle specially if calendar is missing
        }
    ]
    
    for file_info in files_to_fix:
        if os.path.exists(file_info['path']):
            if file_info['name'] == 'dashboard.py':
                # Special handling for dashboard
                if not os.path.exists(os.path.join(PROJECT_ROOT, 'dashboard', 'dashboard_calendar.py')):
                    print_info(f"Disabling calendar imports in {file_info['name']} (calendar module missing)")
                    with open(file_info['path'], 'r') as f:
                        content = f.read()
                    
                    # Comment out calendar imports and usage
                    content = re.sub(r'^from dashboard_calendar import', '#from dashboard_calendar import', content, flags=re.MULTILINE)
                    content = re.sub(r'render_calendar_tab\(\)', '#render_calendar_tab()', content)
                    
                    with open(file_info['path'], 'w') as f:
                        f.write(content)
                    print_success(f"Disabled calendar functionality in {file_info['name']}")
            else:
                if fix_imports_in_file(file_info['path'], file_info['fixes']):
                    print_success(f"Fixed imports in {file_info['name']}")
                else:
                    print_info(f"No import changes needed in {file_info['name']}")
        else:
            print_warning(f"{file_info['name']} not found")

def fix_environment_variables():
    """Fix missing environment variables"""
    print_header("Fixing Environment Variables")
    
    env_path = os.path.join(PROJECT_ROOT, '.env')
    
    if not os.path.exists(env_path):
        print_error(".env file not found!")
        # Try to copy from example
        example_path = os.path.join(PROJECT_ROOT, '.env.example')
        if os.path.exists(example_path):
            shutil.copy2(example_path, env_path)
            print_success("Created .env from .env.example")
        else:
            print_error("No .env.example found either!")
            return
    
    # Read current env
    with open(env_path, 'r') as f:
        env_content = f.read()
    
    # Check and fix FLASK_SECRET_KEY
    if 'FLASK_SECRET_KEY=' not in env_content or re.search(r'^FLASK_SECRET_KEY=\s*$', env_content, re.MULTILINE):
        secret_key = secrets.token_hex(32)
        
        if 'FLASK_SECRET_KEY=' in env_content:
            # Replace empty value
            env_content = re.sub(r'^FLASK_SECRET_KEY=.*$', f'FLASK_SECRET_KEY={secret_key}', env_content, flags=re.MULTILINE)
        else:
            # Add new line
            env_content += f'\nFLASK_SECRET_KEY={secret_key}\n'
        
        with open(env_path, 'w') as f:
            f.write(env_content)
        
        print_success("Generated and set FLASK_SECRET_KEY")
    else:
        print_info("FLASK_SECRET_KEY already configured")

def test_imports():
    """Test if imports work correctly"""
    print_header("Testing Import Fixes")
    
    # Add project root to path for testing
    sys.path.insert(0, PROJECT_ROOT)
    
    tests = [
        {
            'name': 'Common email_sender module',
            'test': lambda: __import__('common.email_sender'),
        },
        {
            'name': 'Dashboard module',
            'test': lambda: __import__('dashboard.dashboard'),
        }
    ]
    
    for test in tests:
        try:
            test['test']()
            print_success(f"{test['name']} imports correctly")
        except ImportError as e:
            print_error(f"{test['name']} import failed: {e}")
        except Exception as e:
            print_warning(f"{test['name']} has other issues: {e}")

def create_missing_directories():
    """Ensure all required directories exist"""
    print_header("Checking Directory Structure")
    
    dirs = [
        os.path.join(PROJECT_ROOT, 'common'),
        os.path.join(PROJECT_ROOT, 'scheduler', 'logs'),
        os.path.join(PROJECT_ROOT, 'dashboard', 'logs'),
        os.path.join(PROJECT_ROOT, 'confirmations', 'logs'),
    ]
    
    for directory in dirs:
        os.makedirs(directory, exist_ok=True)
    
    print_success("All required directories exist")

def main():
    """Main execution"""
    print(f"{Colors.BOLD}{'='*50}{Colors.END}")
    print(f"{Colors.BOLD}Stanford REDCap Setup Fix Script{Colors.END}")
    print(f"{Colors.BOLD}{'='*50}{Colors.END}")
    
    # Ensure we're in the right place
    if not os.path.exists(PROJECT_ROOT):
        print_error(f"Project root not found at {PROJECT_ROOT}")
        sys.exit(1)
    
    os.chdir(PROJECT_ROOT)
    
    # Run fixes
    create_missing_directories()
    copy_missing_files()
    fix_import_paths()
    fix_environment_variables()
    test_imports()
    
    # Summary
    print_header("Fix Summary")
    print_info("All automated fixes have been applied.")
    print_info("")
    print_info("Next steps:")
    print_info("1. Run the debug script to verify: ./scripts/debug_services.sh")
    print_info("2. Start services: ./scripts/start_all_services.sh")
    print_info("3. Check tmux sessions: tmux ls")
    print_info("")
    print_info("If services fail, check logs:")
    print_info("  tail -f scheduler/logs/scheduler.log")
    print_info("  tail -f dashboard/logs/dashboard.log")
    print_info("  tail -f confirmations/logs/confirmations.log")

if __name__ == '__main__':
    main()
