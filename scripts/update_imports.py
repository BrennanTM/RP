#!/usr/bin/env python3
"""Update import paths in migrated files"""

import os
import re

PROJECT_ROOT = os.path.expanduser("~/stanford_redcap")

# Files to update
files_to_update = [
    "dashboard/dashboard.py",
    "confirmations/confirm.py",
    "tracker/tracker.py",
    "scheduler/scheduler.py"
]

# Import replacements
replacements = [
    (r'from email_sender import', 'from common.email_sender import'),
    (r'import email_sender', 'import common.email_sender as email_sender'),
    (r'from 2 import', 'from common.email_sender import'),  # If using 2.py
]

for filepath in files_to_update:
    full_path = os.path.join(PROJECT_ROOT, filepath)
    if os.path.exists(full_path):
        print(f"Updating imports in {filepath}...")
        
        with open(full_path, 'r') as f:
            content = f.read()
        
        # Add sys.path if needed
        if 'sys.path.append' not in content and 'from common' in content:
            import_section = '''import sys
sys.path.append('/home/tristan8/stanford_redcap')

'''
            content = import_section + content
        
        # Apply replacements
        for old, new in replacements:
            content = re.sub(old, new, content)
        
        with open(full_path, 'w') as f:
            f.write(content)
        
        print(f"   Updated {filepath}")
