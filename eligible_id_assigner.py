#!/usr/bin/env python3

import os
import json
import requests
from datetime import datetime
from redcap_client import REDCapClient, RedcapApiError
from eligibility_checker import EligibilityChecker
from dotenv import load_dotenv
import logging
import time

load_dotenv()

class EligibleIDAssigner:
    """
    Assigns subject IDs ONLY to eligible participants based on:
    1. Meeting all eligibility criteria (age, travel, language, medical)
    2. QIDS score determines ID range:
       - QIDS 0-10: Healthy Control (ID 3000-10199)
       - QIDS 11-20: MDD Participant (ID 10200-20000)
       - QIDS 21+: Ineligible (no ID assigned)

    This version uses REDCap as Single Source of Truth (SSOT) with robust concurrency handling.
    """

    def __init__(self):
        self.client = REDCapClient()
        self.checker = EligibilityChecker()

        # ID ranges configuration
        self.ID_RANGES = {
            'healthy_control': {'min': 3000, 'max': 10199},
            'mdd_participant': {'min': 10200, 'max': 20000}
        }

        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def get_next_dynamic_id(self, group_type):
        """
        Get the next available ID for a group by checking existing IDs in REDCap.
        This ensures we always have the latest state from REDCap (SSOT).
        """
        try:
            # Export all records with assigned study IDs
            records = self.client.export_records(fields=['record_id', 'assigned_study_id_a690e9'])

            # Extract and filter IDs for this group
            id_range = self.ID_RANGES[group_type]
            existing_ids = []

            for record in records:
                id_str = record.get('assigned_study_id_a690e9', '').strip()
                if id_str:
                    try:
                        id_value = int(id_str)
                        # Only include IDs within this group's range
                        if id_range['min'] <= id_value <= id_range['max']:
                            existing_ids.append(id_value)
                    except (ValueError, TypeError):
                        # Skip non-integer values
                        self.logger.debug(f"Skipping non-integer ID value: {id_str}")

            # Find the next available ID
            if existing_ids:
                max_id = max(existing_ids)
                next_id = max_id + 1

                # Check if we're at the range limit
                if next_id > id_range['max']:
                    raise ValueError(f"ID range exhausted for {group_type}. Max ID {id_range['max']} reached.")
            else:
                # No IDs exist yet for this group, start at minimum
                next_id = id_range['min']

            self.logger.debug(f"Next ID for {group_type}: {next_id}")
            return next_id

        except RedcapApiError as e:
            self.logger.error(f"Error fetching existing IDs from REDCap: {e}")
            raise

    def determine_group(self, qids_score):
        """
        Determine participant group based on QIDS score
        HC: QIDS ≤10
        MDD: QIDS 11-20
        Ineligible: QIDS ≥21 (should not reach here due to eligibility check)
        """
        if qids_score <= 10:
            return 'healthy_control', 'Healthy Control'
        elif qids_score <= 20:
            return 'mdd_participant', 'MDD Participant'
        else:
            # This should not happen as QIDS ≥21 are filtered by eligibility check
            raise ValueError(f"QIDS score {qids_score} is ≥21 and should have been marked ineligible")

    def process_records(self, retroactive=False):
        """Process records and assign subject IDs ONLY to eligible participants"""

        # Get all records with relevant fields
        fields_to_fetch = [
            'record_id',
            'agree_participate_2950df_d76555_d11eb3_v2_2c0e90',
            'assigned_study_id_a690e9',
            'pipeline_processing_status',
            'pipeline_ineligibility_reasons',
            'qids_score_screening_42b0d5_v2_1d2371',  # Self-reported QIDS total score
            'participant_email_a29017_723fd8_6c173d_v2_98aab5',
            # Include all eligibility fields
            'age_c4982e_ee0b48_0fa205_v2_fdabe5',
            'travel_e4c69a_ec4b4a_09fbe2_v2_1b9f19',
            'english_5c066f_a95c48_a35a95_v2_f6426d',
            'tms_contra_d3aef1_4917df_ffe8d8_v2_3ff65f'
        ]

        try:
            if retroactive:
                # If retroactive, we must fetch all records
                all_records = self.client.export_records(fields=fields_to_fetch)
            else:
                # Efficient filtering for pending records (SSOT)
                # Logic: (Status is empty OR pending) AND (ID is empty) AND (Consent is '1')
                filter_logic = (
                    "([pipeline_processing_status] = '' or [pipeline_processing_status] = 'pending') and "
                    "[assigned_study_id_a690e9] = '' and "
                    "[agree_participate_2950df_d76555_d11eb3_v2_2c0e90] = '1'"
                )
                all_records = self.client.export_records(fields=fields_to_fetch, filter_logic=filter_logic)
        except RedcapApiError as e:
            self.logger.error(f"Error fetching records from REDCap: {e}")
            return 0

        self.logger.info(f"Found {len(all_records)} records to process")

        # Statistics
        processed = 0
        assigned_healthy = 0
        assigned_mdd = 0
        not_eligible = 0
        review_required = 0
        skipped = 0
        errors = 0

        for record in all_records:
            record_id = record.get('record_id')

            # CHECK ELIGIBILITY FIRST
            status, reasons = self.checker.check_eligibility(record)

            if status != 'ELIGIBLE':
                self.logger.info(f"Record {record_id}: {status} - {', '.join(reasons)}")

                # Map status to REDCap dropdown values
                status_map = {
                    'INELIGIBLE': 'ineligible',
                    'REVIEW_REQUIRED': 'manual_review_required'
                }

                # Update REDCap with status (SSOT)
                update_data = {
                    'record_id': record_id,
                    'pipeline_processing_status': status_map.get(status, 'pending'),
                    'pipeline_ineligibility_reasons': ', '.join(reasons)
                }

                try:
                    self.client.import_records([update_data])
                    if status == 'INELIGIBLE':
                        not_eligible += 1
                    else:  # REVIEW_REQUIRED
                        review_required += 1
                except RedcapApiError as e:
                    self.logger.error(f"Error updating status for {record_id}: {e}")
                    errors += 1

                continue

            # If eligible, get QIDS score
            qids_score_str = record.get('qids_score_screening_42b0d5_v2_1d2371', '').strip()

            if not qids_score_str:
                self.logger.warning(f"Record {record_id}: ELIGIBLE but no QIDS score")
                continue

            # Parse QIDS score
            try:
                qids_score = int(qids_score_str)
            except ValueError:
                self.logger.warning(f"Record {record_id}: Invalid QIDS score '{qids_score_str}'")
                errors += 1
                continue

            # Determine group
            try:
                group_type, group_label = self.determine_group(qids_score)
            except ValueError as e:
                self.logger.error(f"Record {record_id}: {e}")
                not_eligible += 1
                continue

            # Implement Constraint-Parse-and-Retry pattern for concurrency
            MAX_RETRIES = 3
            assignment_successful = False

            for attempt in range(MAX_RETRIES):
                try:
                    # Get the next available ID (refreshes from REDCap each time)
                    new_id = self.get_next_dynamic_id(group_type)

                    # Update record in REDCap with new ID
                    update_data = {
                        'record_id': record_id,
                        'assigned_study_id_a690e9': str(new_id),
                        'pipeline_processing_status': 'eligible_id_assigned'
                    }

                    # Try to set the flag field if it exists
                    update_data_with_flag = update_data.copy()
                    update_data_with_flag['id_assigned'] = '1'

                    try:
                        result = self.client.import_records([update_data_with_flag], overwrite='overwrite')
                        self.logger.debug(f"  Set id_assigned flag for Alert trigger")
                    except RedcapApiError:
                        # If flag field doesn't exist, just update without it
                        self.logger.debug(f"  Note: id_assigned field not found - updating ID only")
                        result = self.client.import_records([update_data], overwrite='overwrite')

                    # If we get here, the assignment was successful
                    self.logger.info(f"✓ Record {record_id}: ELIGIBLE - Assigned ID {new_id} ({group_label}, QIDS={qids_score})")
                    processed += 1
                    assignment_successful = True

                    if group_type == 'healthy_control':
                        assigned_healthy += 1
                    else:
                        assigned_mdd += 1

                    break  # Exit retry loop on success

                except RedcapApiError as e:
                    if e.is_unique_constraint_violation():
                        self.logger.warning(f"Race condition detected for ID {new_id} (Record {record_id}). Attempt {attempt+1}/{MAX_RETRIES}. Retrying...")
                        # Continue the loop to recalculate the next ID
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(0.5 * (2 ** attempt))  # Exponential backoff
                        continue
                    else:
                        # Fatal Error (e.g., permissions issue, invalid data format)
                        self.logger.error(f"Fatal API error assigning ID to {record_id}: {e}")
                        errors += 1
                        break
                except ValueError as e:
                    # ID range exhausted
                    self.logger.error(f"Cannot assign ID to {record_id}: {e}")
                    errors += 1
                    break

            if not assignment_successful and attempt == MAX_RETRIES - 1:
                self.logger.error(f"✗ Record {record_id}: Failed to assign ID after {MAX_RETRIES} attempts")
                errors += 1

        # Summary
        self.logger.info("\n" + "=" * 60)
        self.logger.info("ASSIGNMENT SUMMARY:")
        self.logger.info(f"  Total eligible & assigned: {processed}")
        self.logger.info(f"    - Healthy Controls (3000-10199): {assigned_healthy}")
        self.logger.info(f"    - MDD Participants (10200-20000): {assigned_mdd}")
        self.logger.info(f"  Not eligible (no ID assigned): {not_eligible}")
        self.logger.info(f"  Requiring manual review: {review_required}")
        self.logger.info(f"  Skipped (already processed): {skipped}")
        if errors > 0:
            self.logger.info(f"  Errors: {errors}")
        self.logger.info("=" * 60)

        return processed

    def get_statistics(self):
        """Get statistics about assignments from REDCap (SSOT)"""
        try:
            # Fetch all records with relevant fields
            records = self.client.export_records(fields=[
                'record_id',
                'assigned_study_id_a690e9',
                'pipeline_processing_status',
                'qids_score_screening_42b0d5_v2_1d2371'
            ])

            # Calculate statistics
            total_assigned = 0
            healthy_controls = 0
            mdd_participants = 0
            ineligible = 0
            review_required = 0
            pending = 0

            for record in records:
                status = record.get('pipeline_processing_status', '').strip()
                study_id = record.get('assigned_study_id_a690e9', '').strip()

                if study_id:
                    total_assigned += 1
                    try:
                        id_value = int(study_id)
                        if self.ID_RANGES['healthy_control']['min'] <= id_value <= self.ID_RANGES['healthy_control']['max']:
                            healthy_controls += 1
                        elif self.ID_RANGES['mdd_participant']['min'] <= id_value <= self.ID_RANGES['mdd_participant']['max']:
                            mdd_participants += 1
                    except ValueError:
                        pass

                if status == 'ineligible' or status == 'ineligible_notified':
                    ineligible += 1
                elif status == 'manual_review_required':
                    review_required += 1
                elif status == 'pending' or not status:
                    pending += 1

            # Find highest IDs for each group
            max_hc_id = self.ID_RANGES['healthy_control']['min'] - 1
            max_mdd_id = self.ID_RANGES['mdd_participant']['min'] - 1

            for record in records:
                id_str = record.get('assigned_study_id_a690e9', '').strip()
                if id_str:
                    try:
                        id_value = int(id_str)
                        if self.ID_RANGES['healthy_control']['min'] <= id_value <= self.ID_RANGES['healthy_control']['max']:
                            max_hc_id = max(max_hc_id, id_value)
                        elif self.ID_RANGES['mdd_participant']['min'] <= id_value <= self.ID_RANGES['mdd_participant']['max']:
                            max_mdd_id = max(max_mdd_id, id_value)
                    except ValueError:
                        pass

            print("\n" + "=" * 60)
            print("ELIGIBLE PARTICIPANT ID ASSIGNMENT STATISTICS (from REDCap)")
            print("=" * 60)
            print(f"Total records: {len(records)}")
            print(f"Total with assigned IDs: {total_assigned}")
            print(f"  - Healthy Controls (3000-10199): {healthy_controls}")
            print(f"  - MDD Participants (10200-20000): {mdd_participants}")
            print(f"\nStatus breakdown:")
            print(f"  - Ineligible: {ineligible}")
            print(f"  - Manual review required: {review_required}")
            print(f"  - Pending processing: {pending}")
            print(f"\nNext available IDs:")
            print(f"  - Healthy Control: {max_hc_id + 1 if max_hc_id >= self.ID_RANGES['healthy_control']['min'] else self.ID_RANGES['healthy_control']['min']}")
            print(f"  - MDD Participant: {max_mdd_id + 1 if max_mdd_id >= self.ID_RANGES['mdd_participant']['min'] else self.ID_RANGES['mdd_participant']['min']}")
            print("=" * 60)

        except RedcapApiError as e:
            self.logger.error(f"Error fetching statistics from REDCap: {e}")

    def run_continuous(self, interval_minutes=2):
        """Run continuously, checking for new records"""
        self.logger.info(f"Starting continuous monitoring (checking every {interval_minutes} minutes)")
        self.logger.info("Only ELIGIBLE participants will be assigned IDs")
        self.logger.info("Using REDCap as Single Source of Truth (SSOT)")

        while True:
            try:
                self.logger.info(f"\nChecking for new records at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                processed = self.process_records()

                if processed > 0:
                    self.logger.info(f"Processed {processed} eligible records")
                else:
                    self.logger.info("No new eligible records to process")

                self.logger.info(f"Sleeping for {interval_minutes} minutes...")
                time.sleep(interval_minutes * 60)

            except KeyboardInterrupt:
                self.logger.info("\nStopping continuous monitoring")
                break
            except (RedcapApiError, requests.exceptions.RequestException) as e:
                self.logger.error(f"API Error: {str(e)}")
                self.logger.info(f"Retrying in {interval_minutes} minutes...")
                time.sleep(interval_minutes * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Assign subject IDs to ELIGIBLE participants based on QIDS scores (SSOT Version)')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--retroactive', action='store_true', help='Reassign all IDs (careful!)')
    parser.add_argument('--stats', action='store_true', help='Show statistics and exit')
    parser.add_argument('--interval', type=int, default=1, help='Check interval in minutes (default: 1)')

    args = parser.parse_args()

    assigner = EligibleIDAssigner()

    if args.stats:
        assigner.get_statistics()
    elif args.once:
        assigner.process_records(retroactive=args.retroactive)
    else:
        assigner.run_continuous(interval_minutes=args.interval)


if __name__ == "__main__":
    main()