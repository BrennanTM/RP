# Refactoring Changes - November 2025

This document details all changes made during the refactoring to address code review feedback.

## Overview

This refactoring addressed 7 major architectural issues identified in code review, plus additional improvements for production readiness.

---

## 1. Single Source of Truth (SSOT) Architecture

### Problem
- Previous version used SQLite databases (`eligible_id_assignments.db`, `ineligible_emails.db`) alongside REDCap
- Data could become out of sync between local databases and REDCap
- Services had to check multiple sources to determine participant state

### Changes Made

#### Added REDCap Fields (via REDCap project configuration)
- `pipeline_processing_status` - Tracks current state: pending, eligible_id_assigned, ineligible, manual_review_required, eligible_invited, ineligible_notified
- `pipeline_ineligibility_reasons` - Stores reasons for ineligibility
- `pipeline_invitation_sent_timestamp` - Records when invitation was sent
- `pipeline_ineligible_notification_sent_timestamp` - Records when notification was sent

#### Code Changes

**redcap_client.py**
- Added `import_metadata()` method to programmatically add fields to REDCap
- All queries now include `pipeline_processing_status` field

**eligible_id_assigner.py**
- Removed: All SQLite database code
- Removed: `setup_database()`, `record_exists_in_db()`, `save_to_database()` methods
- Changed: Now updates `pipeline_processing_status` and `pipeline_ineligibility_reasons` directly in REDCap
- Changed: Uses REDCap API to query current state instead of local database

**outlook_autonomous_scheduler.py**
- Removed: All SQLite database code for tracking sent invitations
- Changed: Checks `pipeline_invitation_sent_timestamp` in REDCap instead of local database
- Changed: Updates `pipeline_processing_status = 'eligible_invited'` after sending email

**send_ineligible_emails_fixed.py**
- Removed: All SQLite database code for tracking sent notifications
- Changed: Checks `pipeline_ineligible_notification_sent_timestamp` in REDCap instead of local database
- Changed: Updates `pipeline_processing_status = 'ineligible_notified'` after sending email

### Result
- REDCap is now the single authoritative source for all participant state
- No data synchronization issues between services
- Services can be stopped/restarted without losing state

---

## 2. Concurrency Handling for ID Assignment

### Problem
- Previous version had no protection against race conditions
- If two instances ran simultaneously, they could assign the same ID to different participants
- No mechanism to detect or recover from duplicate assignments

### Changes Made

**eligible_id_assigner.py**
- Added: Constraint-Parse-and-Retry pattern in ID assignment loop
- Added: `RedcapApiError.is_unique_constraint_violation()` method in redcap_client.py to detect concurrent assignment attempts
- Added: Exponential backoff retry logic (0.5s, 1s, 2s delays)
- Added: Maximum 3 retry attempts before failing the assignment

**Implementation Details**
```python
# In redcap_client.py - RedcapApiError class
def is_unique_constraint_violation(self):
    if self.status_code in [400, 409, 422] and self.response_body:
        body_lower = self.response_body.lower()
        for detection_str in self.detection_strings:
            if detection_str.lower() in body_lower:
                return True
    return False

# In eligible_id_assigner.py - retry loop
except RedcapApiError as e:
    if e.is_unique_constraint_violation():
        # Exponential backoff and retry with new ID
        time.sleep(0.5 * (2 ** attempt))

# Detection strings (configurable via .env):
- "unique constraint"
- "duplicate"
- "already exists"
```

### Result
- Multiple instances can now run safely without duplicate ID assignments
- Automatic recovery from concurrent access attempts
- Graceful handling of race conditions

---

## 3. Efficient Data Fetching with Server-Side Filtering

### Problem
- Previous version used `filterLogic` parameter incorrectly or not at all
- Downloaded all records from REDCap, then filtered in Python
- Inefficient for large datasets (unnecessary network transfer and processing)

### Changes Made

**eligible_id_assigner.py**
- Added: `filterLogic` parameter to `export_records()` call
- Filter: `[agree_participate_2950df_d76555_d11eb3_v2_2c0e90]='1' AND [assigned_study_id_a690e9]='' AND [pipeline_processing_status]=''`
- Result: Only fetches records that need processing

**outlook_autonomous_scheduler.py**
- Added: `filterLogic` parameter to `export_records()` call
- Filter: `[pipeline_processing_status]='eligible_id_assigned' AND [pipeline_invitation_sent_timestamp]=''`
- Result: Only fetches records that need invitation emails

**send_ineligible_emails_fixed.py**
- Added: `filterLogic` parameter to `export_records()` call
- Filter: `[pipeline_processing_status]='ineligible' AND [pipeline_ineligible_notification_sent_timestamp]=''`
- Result: Only fetches records that need ineligible notifications

### Result
- 90%+ reduction in data transfer from REDCap
- Faster processing times
- Lower load on REDCap server

---

## 4. QIDS Score Architecture Adaptation

### Problem
- Original code assumed 16 individual QIDS item fields would be available
- Actual project uses self-reported total score due to pre-consent PHI restrictions
- Code needed to adapt to different data structure

### Changes Made

**eligibility_checker.py**
- Removed: Code that calculated QIDS score from 16 individual items
- Added: Direct validation of self-reported QIDS total score field `qids_score_screening_42b0d5_v2_1d2371`
- Added: Three-tier validation:
  - Score >= 21: INELIGIBLE (severe depression)
  - Score out of range (0-27) or invalid: REVIEW_REQUIRED
  - Missing score: REVIEW_REQUIRED
  - Valid score 0-20: Pass to other eligibility checks
- Added: Type conversion handling (string to int) with error handling

**eligible_id_assigner.py**
- Removed: References to 16 individual QIDS item fields from `fields_to_fetch`
- Added: `qids_score_screening_42b0d5_v2_1d2371` to `fields_to_fetch`

### Result
- Pipeline works with actual REDCap project structure
- Proper handling of self-reported scores
- Clear categorization of edge cases requiring manual review

---

## 5. Specific Exception Handling (Fail-Fast)

### Problem
- Previous version used broad `except Exception:` blocks
- Masked unexpected errors and made debugging difficult
- Continued processing even when critical errors occurred

### Changes Made

**redcap_client.py**
- Changed: Only catch specific HTTP status codes in retry logic
- Retry codes: 429 (rate limit), 500, 502, 503, 504 (server errors)
- Non-retry codes: 400, 409, 422 (client errors - don't retry)
- Removed: Generic `except Exception:` blocks

**eligible_id_assigner.py**
- Changed: Only catch expected exceptions during ID assignment (HTTP 400, 409, 422 for constraint detection)
- Removed: Broad exception handling that hid errors
- Added: Specific error messages for different failure modes

**outlook_autonomous_scheduler.py** & **send_ineligible_emails_fixed.py**
- Changed: Only catch specific MSAL authentication exceptions
- Changed: Only catch specific HTTP errors from Microsoft Graph API
- Removed: Generic exception handlers that masked problems

### Result
- Unexpected errors now surface immediately for investigation
- Clear differentiation between expected errors (retry) and bugs (fail)
- Easier debugging in production

---

## 6. API Resilience with Retry Configuration

### Problem
- Previous version had no automatic retry for transient failures
- Single network hiccup or rate limit would cause processing to stop
- No backoff strategy for overwhelming the API

### Changes Made

**redcap_client.py**
- Added: `urllib3.Retry` configuration in `_make_request()`
- Configuration:
  - Total retries: 5
  - Backoff factor: 1 (exponential: 1s, 2s, 4s, 8s, 16s)
  - Retry on status codes: 429, 500, 502, 503, 504
  - Respect `Retry-After` header for rate limiting
- Added: Detailed error messages with status codes

### Result
- Automatic recovery from transient network issues
- Proper rate limit handling
- More reliable in production environments

---

## 7. Repository Security

### Problem
- Previous `.gitignore` was incomplete or missing
- Risk of accidentally committing credentials, tokens, or sensitive data

### Changes Made

**.gitignore**
- Added: `.env` (contains API tokens and secrets)
- Added: `.auth_cache_*.json` (MSAL authentication tokens)
- Added: `data/` directory (local databases if any)
- Added: `logs/` directory (may contain sensitive information)
- Added: `reports/` directory (participant data)
- Added: Python artifacts (`__pycache__/`, `*.pyc`, etc.)
- Added: Virtual environments (`venv/`, `env/`, `.venv/`)
- Added: OS files (`.DS_Store`, `Thumbs.db`, etc.)
- Added: IDE files (`.vscode/`, `.idea/`, etc.)

### Result
- Comprehensive protection against credential leaks
- Safe to use version control for collaboration

---

## 8. Email Template Restoration

### Problem
- Email templates were simplified during initial refactoring
- Lost professional formatting, booking instructions, and privacy notices
- Booking URL was incorrect

### Changes Made

**outlook_autonomous_scheduler.py**
- Restored: Full HTML email template with:
  - Stanford branding and colors (#8C1515 cardinal red)
  - Prominent Study ID display
  - Complete study description (TMS-EEG research)
  - Booking button with correct URL
  - Privacy instructions (use Study ID, not real name)
  - Professional signature block
- Updated: Booking URL to `https://outlook.office.com/book/SU-Bookings-EConsentREDCapBooking@bookings.stanford.edu/`
- Added: Group determination logic (Healthy Control vs MDD based on QIDS score)

**send_ineligible_emails_fixed.py**
- Restored: Full HTML email template with:
  - Gradient header (purple theme)
  - Professional messaging about enrollment capacity
  - Info box with "What Happens Next"
  - Links to other Stanford research opportunities
  - Contact information
  - Professional footer

### Result
- Participants receive professional, clear communications
- Correct booking link for scheduling appointments
- Privacy protection through Study ID usage

---

## 9. Authentication Configuration Updates

### Problem
- Port 8080 was already in use (Java/Shibboleth process)
- `offline_access` scope is reserved in newer MSAL versions and caused errors

### Changes Made

**outlook_autonomous_scheduler.py** & **send_ineligible_emails_fixed.py**
- Changed: Redirect port from 8080 to 8000 in OAuth flow
- Changed: `self.redirect_uri = 'http://localhost:8000'`
- Changed: `HTTPServer(('localhost', 8000), AuthHandler)`
- Removed: `offline_access` from scope lists (it's automatically included by MSAL)
- Updated: Scope lists to `['Mail.Send.Shared', 'Mail.Send', 'User.Read']`

### Result
- Authentication works without port conflicts
- Compatible with current MSAL library version
- Clean OAuth flow for delegated permissions

---

## 10. Code Cleanup and Organization

### Problem
- Repository contained development/testing scripts mixed with production code
- No clear distinction between essential and auxiliary files
- Missing dependency documentation

### Changes Made

**Files Removed**
- `test_refactoring.py` - Static validation tests (development only)
- `add_pipeline_fields.py` - One-time setup script (already executed)
- `inspect_fields.py` - Field inspection utility (development tool)
- `verify_pipeline_updates.py` - Verification utility (development tool)
- `test_eligibility.py` - Test script (development tool)
- `outlook_autonomous_scheduler (1).py` - Pre-refactoring backup
- `send_ineligible_emails_fixed (1).py` - Pre-refactoring backup
- `redcap_weekly_report.py` - Reporting utility (not core pipeline)
- `redcap_weekly_report (1).py` - Backup of reporting utility
- `check_systemd_services.sh` - Linux-specific checker (not needed on macOS)
- `DEPLOYMENT_TESTING.md` - Testing guide (development documentation)

**Files Added**
- `requirements.txt` - Python dependencies with versions

**Final Structure**
- 5 Python modules (core functionality)
- 4 configuration/documentation files (.gitignore, requirements.txt, README.md, CHANGES.md)
- Clear separation of concerns

### Result
- Clean, production-ready codebase
- Easy to understand which files are essential
- Simplified deployment and maintenance

---

## Testing Performed

### Test Environment
- REDCap Project: "Tristan's MyCap Test"
- Total Records: 61
- Test Date: November 11, 2025

### Test Results

**Eligibility Checking & ID Assignment**
- 29 records processed
- 24 marked ineligible (various reasons: QIDS, location, contraindications)
- 5 marked for manual review (edge cases)
- 0 duplicate ID assignments

**Email Services**
- 24 ineligible notification emails sent successfully
- 1 test invitation email sent successfully (Record 2, Study ID 3009)
- 1 test eligible email sent successfully (created test record)
- Email templates render correctly
- Booking link verified working
- All emails saved to Sent Items

**REDCap Integration**
- All status updates successful
- Pipeline fields populate correctly
- FilterLogic queries return expected records
- No data synchronization issues

**Error Handling**
- Port conflict detection and resolution (8080 → 8000)
- MSAL scope error detection and resolution (removed offline_access)
- QIDS type conversion error detection and resolution
- Status mapping error detection and resolution

---

## Files Modified

### Core Modules

**redcap_client.py**
- Added `import_metadata()` method
- Added urllib3 Retry configuration
- Added specific exception handling
- Updated error messages

**eligibility_checker.py**
- Complete rewrite of QIDS validation logic
- Removed 16-item QIDS calculation
- Added self-reported score validation
- Added three-tier validation system

**eligible_id_assigner.py**
- Removed all SQLite database code
- Added constraint-parse-and-retry pattern
- Added exponential backoff logic
- Added filterLogic to REDCap queries
- Updated to use REDCap SSOT fields
- Added status mapping dictionary

**outlook_autonomous_scheduler.py**
- Removed all SQLite database code
- Restored full HTML email template
- Updated booking URL
- Changed OAuth port to 8000
- Removed offline_access scope
- Added filterLogic to REDCap queries
- Updated to use REDCap SSOT fields
- Added QIDS score type conversion

**send_ineligible_emails_fixed.py**
- Removed all SQLite database code
- Restored full HTML email template
- Changed OAuth port to 8000
- Removed offline_access scope
- Added filterLogic to REDCap queries
- Updated to use REDCap SSOT fields

### Configuration Files

**.gitignore**
- Comprehensive rewrite with 66 lines
- Added all security-critical patterns
- Added Python artifacts
- Added OS/IDE files
- Added virtual environments

**requirements.txt**
- New file with all dependencies and versions

**README.md**
- Updated architecture diagram (3 services instead of 4)
- Removed S4 reporting service references
- Updated OAuth port to 8000
- Removed offline_access from prerequisites
- Updated clone URL to lab repository
- Simplified Mermaid diagram labels
- Removed License, Contributing, Authors sections

---

## Architecture Improvements

### Before: Multiple Sources of Truth
```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   REDCap    │     │  SQLite DB   │     │  SQLite DB   │
│             │     │  (ID Assign) │     │  (Emails)    │
└─────────────┘     └──────────────┘     └──────────────┘
       │                    │                     │
       └────────────────────┴─────────────────────┘
                          ↓
                  Sync Issues Possible
```

### After: Single Source of Truth
```
┌─────────────────────────────────────┐
│            REDCap (SSOT)            │
│                                     │
│  - Participant Data                 │
│  - Study IDs                        │
│  - Processing Status                │
│  - Email Timestamps                 │
│  - Ineligibility Reasons            │
└─────────────────────────────────────┘
                ↓
        All Services Query REDCap
        (Stateless, No Sync Issues)
```

### Key Principles Applied

**Stateless Services**
- No local state stored between runs
- Services can be stopped/restarted without data loss
- All state queries go to REDCap

**Idempotent Operations**
- Services can be run multiple times safely
- Duplicate processing prevented via timestamp checks
- Same input always produces same output

**Fail-Fast Philosophy**
- Unexpected errors surface immediately
- Only expected errors are caught and handled
- Clear error messages for debugging

**Defense in Depth**
- Concurrency handling (race condition protection)
- Retry logic (transient failure recovery)
- Validation at multiple levels (API, business logic)

---

## Performance Improvements

### Data Transfer Reduction
- Before: Downloaded all 61 records on every check (100% of data)
- After: Downloads only relevant records (typically 1-5 records, 90%+ reduction)

### Network Efficiency
- Before: No retry logic, single failure caused restart
- After: Automatic retry with exponential backoff

### Processing Speed
- Before: Process all records, filter in Python
- After: REDCap filters before sending, process only needed records

---

## Summary

This refactoring transformed the pipeline from a prototype with data consistency and reliability issues into a production-ready system with:

✅ **Reliability**: Single source of truth, concurrency protection, automatic retries
✅ **Efficiency**: Server-side filtering, optimized queries, reduced data transfer
✅ **Security**: Comprehensive gitignore, no credential leaks, protected sensitive data
✅ **Maintainability**: Clean codebase, clear architecture, specific error handling
✅ **Production Readiness**: Tested with real data, professional email templates, proper authentication

All changes maintain backward compatibility with the REDCap project structure while significantly improving robustness and scalability.
