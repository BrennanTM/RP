#!/usr/bin/env python3

from typing import Dict, List, Tuple

class EligibilityChecker:
    """
    Check participant eligibility based on REDCap screening data
    """
    
    def __init__(self):
        self.eligibility_fields = {
            'age_eligibility_90e0b5_0319c0_11df7a_v2_4ef03e': 'Age',
            'travel_eligibility_7344fa_6d3250_608c28_v2_0ea59b': 'Travel to Palo Alto',
            'language_eligibility_ea8835_e870c4_07c569_86c7e2_5e070f': 'English fluency',
            'contra_eligibility_f0a36f_7a7a58_579239_v2_e98532': 'Medical contraindications'
        }
    
    def check_eligibility(self, record: Dict) -> Tuple[str, List[str]]:
        """
        Check if a participant is eligible based on all criteria

        Returns:
            Tuple of (status: str, reasons: List[str])
            Status: 'ELIGIBLE', 'INELIGIBLE', or 'REVIEW_REQUIRED'
            If not eligible, reasons will contain the list of failed criteria
        """
        ineligibility_reasons = []

        # Check if participant agreed to participate
        if record.get('agree_participate_2950df_d76555_d11eb3_v2_2c0e90') != '1':
            return 'INELIGIBLE', ['Did not agree to participate in screening']

        # Check eligibility based on direct field values
        # (not relying on calculated fields since they may be empty via API)

        # Check age directly (must be 18+)
        age = record.get('age_c4982e_ee0b48_0fa205_v2_fdabe5', '')
        if not age or not age.isdigit() or int(age) < 18:
            ineligibility_reasons.append('Age: Must be 18 or older')

        # Check travel ability
        if record.get('travel_e4c69a_ec4b4a_09fbe2_v2_1b9f19') != '1':
            ineligibility_reasons.append('Unable to travel to Palo Alto for study visits')

        # Check English fluency
        if record.get('english_5c066f_a95c48_a35a95_v2_f6426d') != '1':
            ineligibility_reasons.append('English fluency required for study participation')

        # Check TMS contraindications (0 = no contraindications = eligible)
        if record.get('tms_contra_d3aef1_4917df_ffe8d8_v2_3ff65f') == '1':
            ineligibility_reasons.append('Medical contraindications present for TMS treatment')

        # QIDS Score Validation (self-reported total score from interactive HTML QIDS)
        # NOTE: Individual item responses are not stored due to pre-consent PHI restrictions
        # Participants complete interactive HTML QIDS and self-report total score
        qids_status = 'VALID'
        qids_score = None

        # Get self-reported QIDS score
        qids_score_str = record.get('qids_score_screening_42b0d5_v2_1d2371', '').strip()

        if not qids_score_str:
            qids_status = 'REVIEW_REQUIRED'
            ineligibility_reasons.append("QIDS score is missing")
        else:
            try:
                qids_score = int(qids_score_str)

                # Validate QIDS score is in valid range (0-27)
                if not (0 <= qids_score <= 27):
                    qids_status = 'REVIEW_REQUIRED'
                    ineligibility_reasons.append(f"QIDS score out of valid range (0-27): {qids_score}")
                elif qids_score >= 21:
                    # Definitive ineligibility: QIDS score too high (severe depression)
                    ineligibility_reasons.append(f'QIDS score too high ({qids_score} ≥ 21)')

            except ValueError:
                qids_status = 'REVIEW_REQUIRED'
                ineligibility_reasons.append(f"QIDS score is not a valid integer: '{qids_score_str}'")

        # Check if email is provided
        email = record.get('participant_email_a29017_723fd8_6c173d_v2_98aab5', '').strip()
        if not email:
            ineligibility_reasons.append('No email address provided')

        # Determine final status
        # If QIDS requires review, the overall status requires review, regardless of other criteria.
        if qids_status == 'REVIEW_REQUIRED':
            return 'REVIEW_REQUIRED', ineligibility_reasons

        is_eligible = len(ineligibility_reasons) == 0
        status = 'ELIGIBLE' if is_eligible else 'INELIGIBLE'

        # Update the return statement
        return status, ineligibility_reasons
    
    def get_completion_status(self, record: Dict) -> Dict:
        """
        Check if all required fields are completed
        """
        required_fields = [
            'agree_participate_2950df_d76555_d11eb3_v2_2c0e90',
            'age_c4982e_ee0b48_0fa205_v2_fdabe5',
            'sex_634a04_a9a3bb_e901e8_v2_dde73f',
            'distance_9be230_fb24eb_648eba_v2_a26a45',
            'travel_e4c69a_ec4b4a_09fbe2_v2_1b9f19',
            'english_5c066f_a95c48_a35a95_v2_f6426d',
            'tms_contra_d3aef1_4917df_ffe8d8_v2_3ff65f',
            'med_yn_d3a1fe_53665b_605b05_v2_320ffa',
            'qids_score_screening_42b0d5_v2_1d2371',
            'participant_email_a29017_723fd8_6c173d_v2_98aab5'
        ]
        
        completed_fields = []
        missing_fields = []
        
        for field in required_fields:
            value = record.get(field, '').strip()
            if value:
                completed_fields.append(field)
            else:
                missing_fields.append(field)
        
        completion_percentage = (len(completed_fields) / len(required_fields)) * 100
        
        return {
            'is_complete': len(missing_fields) == 0,
            'completion_percentage': round(completion_percentage, 2),
            'completed_fields': completed_fields,
            'missing_fields': missing_fields,
            'total_required': len(required_fields)
        }
    
    def needs_processing(self, record: Dict) -> bool:
        """
        Check if a record needs to be processed for email notification
        """
        # Record needs processing if:
        # 1. Survey is complete
        # 2. Has an email address
        # 3. Has not been processed yet (we'll track this separately)
        
        completion = self.get_completion_status(record)
        has_email = bool(record.get('participant_email_a29017_723fd8_6c173d_v2_98aab5', '').strip())
        
        return completion['is_complete'] and has_email