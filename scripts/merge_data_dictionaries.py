#!/usr/bin/env python3
"""
Merge REDCap data dictionaries to include both research fields and tracking fields
This creates a combined data dictionary that can be imported into REDCap
"""

import pandas as pd
import sys
import os

def merge_data_dictionaries(screening_dict_path, tracking_dict_path, output_path):
    """
    Merge two REDCap data dictionaries, keeping all screening fields and adding tracking fields
    
    Args:
        screening_dict_path: Path to the new screening data dictionary (KellerLabOnlineScreeningProjec)
        tracking_dict_path: Path to the old tracking data dictionary (TristansMyCapTest)
        output_path: Where to save the merged dictionary
    """
    
    # Read both dictionaries
    print(f"Reading screening dictionary from: {screening_dict_path}")
    screening_df = pd.read_csv(screening_dict_path)
    
    print(f"Reading tracking dictionary from: {tracking_dict_path}")
    tracking_df = pd.read_csv(tracking_dict_path)
    
    # Identify tracking fields to add (exclude ones that might conflict)
    screening_fields = set(screening_df['Variable / Field Name'].dropna())
    tracking_fields = set(tracking_df['Variable / Field Name'].dropna())
    
    # Fields to definitely include from tracking
    essential_tracking_fields = [
        'study_id',
        'eligibility_email_sent',
        'email_sent_timestamp',
        'calendly_booked',
        'calendly_date',
        'calendly_time',
        'appointment_type',
        'consent_scheduled',
        'consent_date',
        'consent_completed',
        'mri_scheduled',
        'mri_date',
        'mri_completed',
        'tms_scheduled',
        'tms_date',
        'tms_completed',
        'appointment_confirmation_sent',
        'confirm_sent_timestamp',
        'appointment_reminder_sent',
        'reminder_sent_timestamp',
        'appointment_status',
        'appointments_scheduled',
        'appointments_completed',
        'last_calendly_sync'
    ]
    
    # Get rows for tracking fields
    tracking_rows_to_add = tracking_df[
        tracking_df['Variable / Field Name'].isin(essential_tracking_fields)
    ].copy()
    
    # Update form name for tracking fields
    tracking_rows_to_add['Form Name'] = 'participant_tracking'
    
    # Create section headers for organization
    section_header_row = pd.DataFrame({
        'Variable / Field Name': ['tracking_section_header'],
        'Form Name': ['participant_tracking'],
        'Section Header': ['Participant Tracking and Scheduling'],
        'Field Type': ['descriptive'],
        'Field Label': ['<h4 style="color:#8C1515;">Participant Tracking Information</h4><p>These fields are automatically managed by the recruitment system.</p>']
    })
    
    # Combine all dataframes
    merged_df = pd.concat([
        screening_df,
        section_header_row,
        tracking_rows_to_add
    ], ignore_index=True)
    
    # Fill NaN values with empty strings
    merged_df = merged_df.fillna('')
    
    # Save merged dictionary
    merged_df.to_csv(output_path, index=False)
    
    print(f"\n✓ Merged data dictionary saved to: {output_path}")
    print(f"  - Total fields: {len(merged_df)}")
    print(f"  - Screening fields: {len(screening_df)}")
    print(f"  - Tracking fields added: {len(tracking_rows_to_add)}")
    
    return merged_df

def create_tracking_fields_dictionary():
    """
    Create a standalone data dictionary with just the tracking fields
    This can be used to add tracking to any REDCap project
    """
    
    tracking_fields = [
        {
            'Variable / Field Name': 'study_id',
            'Form Name': 'participant_tracking',
            'Field Type': 'text',
            'Field Label': 'Study ID',
            'Field Note': 'Automatically assigned participant identifier (HC-#### or MDD-####)',
            'Text Validation Type OR Show Slider Number': '',
            'Required Field?': '',
            'Field Annotation': '@READONLY'
        },
        {
            'Variable / Field Name': 'eligibility_email_sent',
            'Form Name': 'participant_tracking',
            'Field Type': 'yesno',
            'Field Label': 'Eligibility Email Sent',
            'Field Note': 'Has the eligibility notification email been sent?',
            'Choices, Calculations, OR Slider Labels': '1, Yes | 0, No'
        },
        {
            'Variable / Field Name': 'email_sent_timestamp',
            'Form Name': 'participant_tracking',
            'Field Type': 'text',
            'Field Label': 'Email Sent Timestamp',
            'Field Note': 'When the eligibility email was sent',
            'Text Validation Type OR Show Slider Number': 'datetime_seconds_ymd',
            'Field Annotation': '@READONLY'
        },
        {
            'Variable / Field Name': 'calendly_booked',
            'Form Name': 'participant_tracking',
            'Field Type': 'yesno',
            'Field Label': 'Appointment Booked',
            'Field Note': 'Has participant booked an appointment?',
            'Choices, Calculations, OR Slider Labels': '1, Yes | 0, No'
        },
        {
            'Variable / Field Name': 'calendly_date',
            'Form Name': 'participant_tracking',
            'Field Type': 'text',
            'Field Label': 'Appointment Date',
            'Text Validation Type OR Show Slider Number': 'date_ymd'
        },
        {
            'Variable / Field Name': 'calendly_time',
            'Form Name': 'participant_tracking',
            'Field Type': 'text',
            'Field Label': 'Appointment Time',
            'Text Validation Type OR Show Slider Number': 'time'
        },
        {
            'Variable / Field Name': 'appointment_type',
            'Form Name': 'participant_tracking',
            'Field Type': 'dropdown',
            'Field Label': 'Appointment Type',
            'Choices, Calculations, OR Slider Labels': '1, Consent Session | 2, MRI Session | 3, TMS Session | 4, Other'
        },
        {
            'Variable / Field Name': 'consent_scheduled',
            'Form Name': 'participant_tracking',
            'Field Type': 'yesno',
            'Field Label': 'Consent Session Scheduled',
            'Choices, Calculations, OR Slider Labels': '1, Yes | 0, No'
        },
        {
            'Variable / Field Name': 'consent_date',
            'Form Name': 'participant_tracking',
            'Field Type': 'text',
            'Field Label': 'Consent Session Date',
            'Text Validation Type OR Show Slider Number': 'date_ymd'
        },
        {
            'Variable / Field Name': 'consent_completed',
            'Form Name': 'participant_tracking',
            'Field Type': 'yesno',
            'Field Label': 'Consent Session Completed',
            'Choices, Calculations, OR Slider Labels': '1, Yes | 0, No'
        },
        {
            'Variable / Field Name': 'mri_scheduled',
            'Form Name': 'participant_tracking',
            'Field Type': 'yesno',
            'Field Label': 'MRI Session Scheduled',
            'Choices, Calculations, OR Slider Labels': '1, Yes | 0, No'
        },
        {
            'Variable / Field Name': 'mri_date',
            'Form Name': 'participant_tracking',
            'Field Type': 'text',
            'Field Label': 'MRI Session Date',
            'Text Validation Type OR Show Slider Number': 'date_ymd'
        },
        {
            'Variable / Field Name': 'mri_completed',
            'Form Name': 'participant_tracking',
            'Field Type': 'yesno',
            'Field Label': 'MRI Session Completed',
            'Choices, Calculations, OR Slider Labels': '1, Yes | 0, No'
        },
        {
            'Variable / Field Name': 'tms_scheduled',
            'Form Name': 'participant_tracking',
            'Field Type': 'yesno',
            'Field Label': 'TMS Session Scheduled',
            'Choices, Calculations, OR Slider Labels': '1, Yes | 0, No'
        },
        {
            'Variable / Field Name': 'tms_date',
            'Form Name': 'participant_tracking',
            'Field Type': 'text',
            'Field Label': 'TMS Session Date',
            'Text Validation Type OR Show Slider Number': 'date_ymd'
        },
        {
            'Variable / Field Name': 'tms_completed',
            'Form Name': 'participant_tracking',
            'Field Type': 'yesno',
            'Field Label': 'TMS Session Completed',
            'Choices, Calculations, OR Slider Labels': '1, Yes | 0, No'
        },
        {
            'Variable / Field Name': 'appointment_confirmation_sent',
            'Form Name': 'participant_tracking',
            'Field Type': 'yesno',
            'Field Label': 'Appointment Confirmation Sent',
            'Choices, Calculations, OR Slider Labels': '1, Yes | 0, No'
        },
        {
            'Variable / Field Name': 'confirm_sent_timestamp',
            'Form Name': 'participant_tracking',
            'Field Type': 'text',
            'Field Label': 'Confirmation Sent Timestamp',
            'Text Validation Type OR Show Slider Number': 'datetime_seconds_ymd',
            'Field Annotation': '@READONLY'
        },
        {
            'Variable / Field Name': 'appointment_reminder_sent',
            'Form Name': 'participant_tracking',
            'Field Type': 'yesno',
            'Field Label': 'Appointment Reminder Sent',
            'Choices, Calculations, OR Slider Labels': '1, Yes | 0, No'
        },
        {
            'Variable / Field Name': 'reminder_sent_timestamp',
            'Form Name': 'participant_tracking',
            'Field Type': 'text',
            'Field Label': 'Reminder Sent Timestamp',
            'Text Validation Type OR Show Slider Number': 'datetime_seconds_ymd',
            'Field Annotation': '@READONLY'
        },
        {
            'Variable / Field Name': 'appointment_status',
            'Form Name': 'participant_tracking',
            'Field Type': 'dropdown',
            'Field Label': 'Overall Appointment Status',
            'Choices, Calculations, OR Slider Labels': '1, Not Started | 2, Consent Scheduled | 3, Consent Completed | 4, MRI Scheduled | 5, MRI Completed | 6, TMS Scheduled | 7, TMS Completed | 8, Study Completed | 9, Withdrawn | 10, Lost to Follow-up'
        },
        {
            'Variable / Field Name': 'appointment_scheduled_via',
            'Form Name': 'participant_tracking',
            'Field Type': 'text',
            'Field Label': 'Scheduling Method',
            'Field Note': 'How appointment was scheduled (e.g., stanford_scheduler, calendly, manual)'
        }
    ]
    
    # Convert to DataFrame
    tracking_df = pd.DataFrame(tracking_fields)
    
    # Fill empty columns
    all_columns = [
        'Variable / Field Name', 'Form Name', 'Section Header', 'Field Type',
        'Field Label', 'Choices, Calculations, OR Slider Labels', 'Field Note',
        'Text Validation Type OR Show Slider Number', 'Text Validation Min',
        'Text Validation Max', 'Identifier?', 'Branching Logic (Show field only if...)',
        'Required Field?', 'Custom Alignment', 'Question Number (surveys only)',
        'Matrix Group Name', 'Matrix Ranking?', 'Field Annotation'
    ]
    
    for col in all_columns:
        if col not in tracking_df.columns:
            tracking_df[col] = ''
    
    # Reorder columns
    tracking_df = tracking_df[all_columns]
    
    return tracking_df

def main():
    if len(sys.argv) < 3:
        print("Usage: python merge_data_dictionaries.py <screening_dict.csv> <tracking_dict.csv> [output.csv]")
        print("\nExample:")
        print("  python merge_data_dictionaries.py KellerLabOnlineScreeningProjec_DataDictionary_2025-06-29.csv TristansMyCapTest_DataDictionary_2025-06-27.csv merged_dictionary.csv")
        sys.exit(1)
    
    screening_dict = sys.argv[1]
    tracking_dict = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else 'merged_data_dictionary.csv'
    
    # Create standalone tracking fields dictionary
    print("Creating standalone tracking fields dictionary...")
    tracking_only_df = create_tracking_fields_dictionary()
    tracking_only_df.to_csv('tracking_fields_only.csv', index=False)
    print(f"✓ Tracking fields dictionary saved to: tracking_fields_only.csv")
    
    # Merge dictionaries
    print("\nMerging data dictionaries...")
    merge_data_dictionaries(screening_dict, tracking_dict, output_path)
    
    print("\n=== Next Steps ===")
    print("1. Upload the merged data dictionary to REDCap:")
    print("   - Go to Project Setup → Data Dictionary")
    print("   - Download current dictionary as backup")
    print(f"   - Upload: {output_path}")
    print("\n2. Or add just tracking fields to existing project:")
    print("   - Upload: tracking_fields_only.csv")
    print("\n3. Update your .env file with the new project's API token")

if __name__ == "__main__":
    main()