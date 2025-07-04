# List of record IDs to treat as eligible regardless of overall_eligibility
OVERRIDE_ELIGIBLE_RECORDS = ['23', '24', '25']

def is_eligible_override(record_id, overall_eligibility):
    """Check if this record should be treated as eligible"""
    if record_id in OVERRIDE_ELIGIBLE_RECORDS:
        return True
    return overall_eligibility == '1'
