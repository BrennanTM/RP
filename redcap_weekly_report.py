#!/usr/bin/env python3
"""
REDCap Weekly Report Generator - SSOT Compatible Version
Generates accurate enrollment funnel metrics using REDCap as Single Source of Truth
"""

import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from datetime import datetime, timedelta
from redcap_client import REDCapClient
import argparse
import logging

# Set up logging
os.makedirs('./logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('./logs/weekly_report.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class WeeklyReportGenerator:
    def __init__(self, include_test_records=False):
        """
        Initialize the report generator

        Args:
            include_test_records: If True, includes test records in the report
        """
        self.include_test_records = include_test_records
        self.client = REDCapClient()

        # Create reports directory
        os.makedirs('reports', exist_ok=True)
        os.makedirs('reports/charts', exist_ok=True)

    def is_test_record(self, record_id):
        """
        Determine if a record is a test record

        Test records have IDs containing:
        - test, demo, pipeline, quicktest, verify, fresh, no_mac, final
        - Or any variation with underscores/numbers
        """
        test_patterns = [
            'test', 'demo', 'pipeline', 'quicktest',
            'verify', 'fresh', 'no_mac', 'final', 'example',
            'sample', 'trial', 'temp', 'tmp'
        ]

        record_lower = record_id.lower()
        return any(pattern in record_lower for pattern in test_patterns)

    def fetch_and_analyze_data(self):
        """Fetch data from REDCap and analyze enrollment metrics"""

        logger.info("Fetching data from REDCap...")
        records = self.client.export_records()
        logger.info(f"Fetched {len(records)} total records")

        # Initialize metrics
        metrics = {
            'total_records': len(records),
            'test_records': 0,
            'real_records': 0,
            'total_screened': 0,
            'total_eligible_hc': 0,
            'total_eligible_mdd': 0,
            'total_ineligible': 0,
            'manual_review': 0,
            'pending_processing': 0,
            'reasons': {
                'age': 0,
                'travel': 0,
                'english': 0,
                'contraindications': 0,
                'qids_high': 0,
                'no_email': 0,
                'qids_missing': 0,
                'qids_invalid': 0
            },
            'hc_list': [],
            'mdd_list': [],
            'ineligible_list': [],
            'manual_review_list': [],
            'incomplete_surveys': 0,
            'invitations_sent': 0,
            'ineligible_notified': 0
        }

        # Analyze each record
        for record in records:
            record_id = record.get('record_id', '')

            # Skip empty record IDs
            if not record_id:
                continue

            # Check if test record
            is_test = self.is_test_record(record_id)

            if is_test:
                metrics['test_records'] += 1
                if not self.include_test_records:
                    continue
            else:
                metrics['real_records'] += 1

            # Check if survey is complete
            if record.get('online_screening_survey_complete') != '2':
                metrics['incomplete_surveys'] += 1
                continue

            metrics['total_screened'] += 1

            # Get key fields
            study_id = record.get('assigned_study_id_a690e9', '')
            email = record.get('participant_email_a29017_723fd8_6c173d_v2_98aab5', '')
            qids = record.get('qids_score_screening_42b0d5_v2_1d2371', '')
            age = record.get('age_c4982e_ee0b48_0fa205_v2_fdabe5', '')
            travel = record.get('travel_e4c69a_ec4b4a_09fbe2_v2_1b9f19')
            english = record.get('english_5c066f_a95c48_a35a95_v2_f6426d')
            contra = record.get('tms_contra_d3aef1_4917df_ffe8d8_v2_3ff65f')

            # SSOT: Get pipeline processing status
            pipeline_status = record.get('pipeline_processing_status', '')
            pipeline_reasons = record.get('pipeline_ineligibility_reasons', '')
            invitation_sent = record.get('pipeline_invitation_sent_timestamp', '')
            ineligible_notified = record.get('pipeline_ineligible_notification_sent_timestamp', '')

            # Count emails sent (SSOT approach)
            if invitation_sent:
                metrics['invitations_sent'] += 1
            if ineligible_notified:
                metrics['ineligible_notified'] += 1

            # Categorize based on pipeline status and study ID
            if pipeline_status == 'manual_review_required':
                metrics['manual_review'] += 1
                metrics['manual_review_list'].append({
                    'record_id': record_id,
                    'reasons': pipeline_reasons or 'Requires manual review',
                    'qids': qids,
                    'email': email,
                    'is_test': is_test
                })

            elif pipeline_status in ['', 'pending']:
                metrics['pending_processing'] += 1

            elif study_id and pipeline_status in ['eligible_id_assigned', 'eligible_invited']:
                # Participant has been assigned a study ID and is eligible
                study_id_num = int(study_id) if study_id.isdigit() else 0

                if 3000 <= study_id_num < 10000:
                    metrics['total_eligible_hc'] += 1
                    metrics['hc_list'].append({
                        'record_id': record_id,
                        'study_id': study_id,
                        'qids': qids,
                        'email': email,
                        'status': pipeline_status,
                        'invited': 'Yes' if invitation_sent else 'No',
                        'is_test': is_test
                    })
                elif 10200 <= study_id_num < 20000:
                    metrics['total_eligible_mdd'] += 1
                    metrics['mdd_list'].append({
                        'record_id': record_id,
                        'study_id': study_id,
                        'qids': qids,
                        'email': email,
                        'status': pipeline_status,
                        'invited': 'Yes' if invitation_sent else 'No',
                        'is_test': is_test
                    })

            elif pipeline_status in ['ineligible', 'ineligible_notified']:
                # Participant is ineligible
                metrics['total_ineligible'] += 1

                # Parse ineligibility reasons from pipeline
                reasons = []
                if pipeline_reasons:
                    reasons_lower = pipeline_reasons.lower()

                    if 'age' in reasons_lower:
                        metrics['reasons']['age'] += 1
                        reasons.append('Age < 18')

                    if 'travel' in reasons_lower:
                        metrics['reasons']['travel'] += 1
                        reasons.append('Cannot travel')

                    if 'english' in reasons_lower:
                        metrics['reasons']['english'] += 1
                        reasons.append('No English')

                    if 'contraindication' in reasons_lower or 'tms' in reasons_lower:
                        metrics['reasons']['contraindications'] += 1
                        reasons.append('TMS contraindications')

                    if 'qids score too high' in reasons_lower or '≥ 21' in reasons_lower:
                        metrics['reasons']['qids_high'] += 1
                        reasons.append(f'QIDS ≥21')

                    if 'email' in reasons_lower:
                        metrics['reasons']['no_email'] += 1
                        reasons.append('No email')

                    if 'qids score is missing' in reasons_lower:
                        metrics['reasons']['qids_missing'] += 1
                        reasons.append('QIDS missing')

                    if 'qids score' in reasons_lower and 'not a valid integer' in reasons_lower:
                        metrics['reasons']['qids_invalid'] += 1
                        reasons.append('QIDS invalid')

                metrics['ineligible_list'].append({
                    'record_id': record_id,
                    'reasons': ', '.join(reasons) if reasons else pipeline_reasons or 'Unknown',
                    'email': email,
                    'notified': 'Yes' if ineligible_notified else 'No',
                    'is_test': is_test
                })

        return metrics

    def generate_charts(self, metrics):
        """Generate visualization charts"""

        # Set style
        plt.style.use('seaborn-v0_8-whitegrid')

        # 1. Enrollment Funnel Chart
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

        # Funnel data
        funnel_stages = ['Screened', 'Eligible', 'HC', 'MDD', 'Ineligible', 'Review']
        funnel_values = [
            metrics['total_screened'],
            metrics['total_eligible_hc'] + metrics['total_eligible_mdd'],
            metrics['total_eligible_hc'],
            metrics['total_eligible_mdd'],
            metrics['total_ineligible'],
            metrics['manual_review']
        ]

        # Create funnel chart
        colors = ['#3498db', '#2ecc71', '#9b59b6', '#f39c12', '#e74c3c', '#95a5a6']
        bars = ax1.barh(funnel_stages, funnel_values, color=colors)
        ax1.set_xlabel('Number of Participants')
        ax1.set_title('Enrollment Funnel', fontsize=14, fontweight='bold')

        # Add value labels
        for bar, value in zip(bars, funnel_values):
            if value > 0:
                ax1.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height()/2,
                        f'{value}', ha='left', va='center')

        # 2. Ineligibility Reasons Pie Chart
        reasons_labels = []
        reasons_values = []

        for reason, count in metrics['reasons'].items():
            if count > 0:
                label_map = {
                    'age': 'Age < 18',
                    'travel': 'Cannot Travel',
                    'english': 'No English',
                    'contraindications': 'TMS Contraindications',
                    'qids_high': 'QIDS ≥ 21',
                    'no_email': 'No Email',
                    'qids_missing': 'QIDS Missing',
                    'qids_invalid': 'QIDS Invalid'
                }
                reasons_labels.append(label_map.get(reason, reason))
                reasons_values.append(count)

        if reasons_values:
            ax2.pie(reasons_values, labels=reasons_labels, autopct='%1.0f%%', startangle=90)
            ax2.set_title('Ineligibility Reasons', fontsize=14, fontweight='bold')
        else:
            ax2.text(0.5, 0.5, 'No Ineligible Participants', ha='center', va='center', fontsize=12)
            ax2.set_xlim(0, 1)
            ax2.set_ylim(0, 1)

        plt.tight_layout()

        # Save chart
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        chart_path = f'reports/charts/enrollment_funnel_{timestamp}.png'
        plt.savefig(chart_path, dpi=100, bbox_inches='tight')
        plt.close()

        return chart_path

    def generate_html_report(self, metrics):
        """Generate HTML report"""

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_date = datetime.now().strftime('%B %d, %Y at %I:%M %p')

        # Generate charts
        chart_path = self.generate_charts(metrics)
        chart_filename = os.path.basename(chart_path)

        # Calculate percentages
        eligible_pct = 0
        if metrics['total_screened'] > 0:
            eligible_pct = ((metrics['total_eligible_hc'] + metrics['total_eligible_mdd']) /
                           metrics['total_screened'] * 100)

        # Determine report type label
        report_type = "All Records" if self.include_test_records else "Real Participants Only"

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>REDCap Weekly Report - {report_date}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 15px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #8B0000 0%, #DC143C 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        h1 {{
            margin: 0;
            font-size: 32px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }}
        .subtitle {{
            margin-top: 10px;
            opacity: 0.9;
            font-size: 18px;
        }}
        .report-type {{
            display: inline-block;
            background: rgba(255,255,255,0.2);
            padding: 5px 15px;
            border-radius: 20px;
            margin-top: 10px;
            font-weight: bold;
        }}
        .content {{
            padding: 30px;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin: 30px 0;
        }}
        .stat-card {{
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            padding: 20px;
            border-radius: 10px;
            text-align: center;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            transition: transform 0.3s;
        }}
        .stat-card:hover {{
            transform: translateY(-5px);
        }}
        .stat-value {{
            font-size: 36px;
            font-weight: bold;
            color: #2c3e50;
            margin: 10px 0;
        }}
        .stat-label {{
            color: #7f8c8d;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        th {{
            background: #8B0000;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 600;
        }}
        td {{
            padding: 12px;
            border-bottom: 1px solid #ecf0f1;
        }}
        tr:nth-child(even) {{
            background: #f8f9fa;
        }}
        tr:hover {{
            background: #e8f4ff;
        }}
        .chart-container {{
            margin: 30px 0;
            text-align: center;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 10px;
        }}
        .chart-container img {{
            max-width: 100%;
            border-radius: 10px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }}
        .section {{
            margin: 40px 0;
            padding: 25px;
            background: white;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }}
        .section h2 {{
            color: #8B0000;
            border-bottom: 3px solid #8B0000;
            padding-bottom: 10px;
            margin-bottom: 20px;
        }}
        .success {{ color: #27ae60; font-weight: bold; }}
        .warning {{ color: #f39c12; font-weight: bold; }}
        .danger {{ color: #e74c3c; font-weight: bold; }}
        .info {{ color: #3498db; font-weight: bold; }}
        .footer {{
            background: #2c3e50;
            color: white;
            text-align: center;
            padding: 20px;
            margin-top: 40px;
        }}
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: bold;
            margin-left: 5px;
        }}
        .badge-test {{ background: #f39c12; color: white; }}
        .badge-real {{ background: #27ae60; color: white; }}
        .badge-yes {{ background: #2ecc71; color: white; }}
        .badge-no {{ background: #95a5a6; color: white; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 REDCap Weekly Enrollment Report</h1>
            <div class="subtitle">Generated on {report_date}</div>
            <div class="report-type">{report_type}</div>
        </div>

        <div class="content">
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">Total Records</div>
                    <div class="stat-value">{metrics['total_records']}</div>
                    <div style="font-size: 12px; margin-top: 5px;">
                        Real: {metrics['real_records']} | Test: {metrics['test_records']}
                    </div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Screened</div>
                    <div class="stat-value">{metrics['total_screened']}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Eligible</div>
                    <div class="stat-value" class="success">{metrics['total_eligible_hc'] + metrics['total_eligible_mdd']}</div>
                    <div style="font-size: 12px; margin-top: 5px;">
                        {eligible_pct:.1f}% eligibility rate
                    </div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Ineligible</div>
                    <div class="stat-value" class="danger">{metrics['total_ineligible']}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Manual Review</div>
                    <div class="stat-value" class="warning">{metrics['manual_review']}</div>
                </div>
            </div>

            <div class="section">
                <h2>🔬 Group Assignment Breakdown</h2>
                <table>
                    <tr>
                        <th>Group</th>
                        <th>Count</th>
                        <th>QIDS Range</th>
                        <th>Study ID Range</th>
                        <th>Percentage</th>
                    </tr>
                    <tr>
                        <td><strong>Healthy Controls (HC)</strong></td>
                        <td class="success">{metrics['total_eligible_hc']}</td>
                        <td>≤ 10</td>
                        <td>3000 - 9999</td>
                        <td>{metrics['total_eligible_hc'] / max(metrics['total_screened'], 1) * 100:.1f}%</td>
                    </tr>
                    <tr>
                        <td><strong>MDD Participants</strong></td>
                        <td class="success">{metrics['total_eligible_mdd']}</td>
                        <td>11 - 20</td>
                        <td>10200 - 19999</td>
                        <td>{metrics['total_eligible_mdd'] / max(metrics['total_screened'], 1) * 100:.1f}%</td>
                    </tr>
                    <tr>
                        <td><strong>Ineligible</strong></td>
                        <td class="danger">{metrics['total_ineligible']}</td>
                        <td>≥ 21 or other criteria</td>
                        <td>No ID assigned</td>
                        <td>{metrics['total_ineligible'] / max(metrics['total_screened'], 1) * 100:.1f}%</td>
                    </tr>
                    <tr>
                        <td><strong>Manual Review Required</strong></td>
                        <td class="warning">{metrics['manual_review']}</td>
                        <td>Edge cases</td>
                        <td>Pending review</td>
                        <td>{metrics['manual_review'] / max(metrics['total_screened'], 1) * 100:.1f}%</td>
                    </tr>
                </table>
            </div>

            <div class="section">
                <h2>❌ Ineligibility Reasons</h2>
                <table>
                    <tr>
                        <th>Reason</th>
                        <th>Count</th>
                        <th>Percentage of Ineligible</th>
                    </tr>
                    <tr>
                        <td>Age < 18</td>
                        <td>{metrics['reasons']['age']}</td>
                        <td>{metrics['reasons']['age'] / max(metrics['total_ineligible'], 1) * 100:.1f}%</td>
                    </tr>
                    <tr>
                        <td>Cannot Travel to Palo Alto</td>
                        <td>{metrics['reasons']['travel']}</td>
                        <td>{metrics['reasons']['travel'] / max(metrics['total_ineligible'], 1) * 100:.1f}%</td>
                    </tr>
                    <tr>
                        <td>Not Fluent in English</td>
                        <td>{metrics['reasons']['english']}</td>
                        <td>{metrics['reasons']['english'] / max(metrics['total_ineligible'], 1) * 100:.1f}%</td>
                    </tr>
                    <tr>
                        <td>TMS Contraindications</td>
                        <td>{metrics['reasons']['contraindications']}</td>
                        <td>{metrics['reasons']['contraindications'] / max(metrics['total_ineligible'], 1) * 100:.1f}%</td>
                    </tr>
                    <tr>
                        <td>QIDS Score ≥ 21</td>
                        <td>{metrics['reasons']['qids_high']}</td>
                        <td>{metrics['reasons']['qids_high'] / max(metrics['total_ineligible'], 1) * 100:.1f}%</td>
                    </tr>
                    <tr>
                        <td>No Email Address</td>
                        <td>{metrics['reasons']['no_email']}</td>
                        <td>{metrics['reasons']['no_email'] / max(metrics['total_ineligible'], 1) * 100:.1f}%</td>
                    </tr>
                    <tr>
                        <td>QIDS Score Missing</td>
                        <td>{metrics['reasons']['qids_missing']}</td>
                        <td>{metrics['reasons']['qids_missing'] / max(metrics['total_ineligible'], 1) * 100:.1f}%</td>
                    </tr>
                    <tr>
                        <td>QIDS Score Invalid</td>
                        <td>{metrics['reasons']['qids_invalid']}</td>
                        <td>{metrics['reasons']['qids_invalid'] / max(metrics['total_ineligible'], 1) * 100:.1f}%</td>
                    </tr>
                </table>
            </div>

            <div class="chart-container">
                <h2>📈 Enrollment Visualizations</h2>
                <img src="charts/{chart_filename}" alt="Enrollment Charts">
            </div>

            <div class="section">
                <h2>📧 Communication Status (SSOT)</h2>
                <p><span class="info">Invitation Emails Sent:</span> {metrics['invitations_sent']}</p>
                <p><span class="info">Ineligible Notifications Sent:</span> {metrics['ineligible_notified']}</p>
                <p>All eligible participants receive invitation emails with scheduling links.</p>
                <p>All ineligible participants receive neutral "thank you" emails.</p>
                <p><em>Email counts are tracked in REDCap pipeline fields (Single Source of Truth).</em></p>
            </div>

            <div class="section">
                <h2>📋 Recent Enrollments</h2>
                <h3>Healthy Controls (Most Recent 5)</h3>
                <table>
                    <tr><th>Study ID</th><th>Record ID</th><th>QIDS Score</th><th>Invited</th><th>Type</th></tr>
"""

        # Add HC records
        for rec in metrics['hc_list'][-5:]:
            test_badge = '<span class="badge badge-test">TEST</span>' if rec['is_test'] else '<span class="badge badge-real">REAL</span>'
            invite_badge = '<span class="badge badge-yes">Yes</span>' if rec['invited'] == 'Yes' else '<span class="badge badge-no">No</span>'
            html_content += f"""
                    <tr>
                        <td>{rec['study_id']}</td>
                        <td>{rec['record_id']}</td>
                        <td>{rec['qids']}</td>
                        <td>{invite_badge}</td>
                        <td>{test_badge}</td>
                    </tr>
"""

        html_content += """
                </table>

                <h3>MDD Participants (Most Recent 5)</h3>
                <table>
                    <tr><th>Study ID</th><th>Record ID</th><th>QIDS Score</th><th>Invited</th><th>Type</th></tr>
"""

        # Add MDD records
        for rec in metrics['mdd_list'][-5:]:
            test_badge = '<span class="badge badge-test">TEST</span>' if rec['is_test'] else '<span class="badge badge-real">REAL</span>'
            invite_badge = '<span class="badge badge-yes">Yes</span>' if rec['invited'] == 'Yes' else '<span class="badge badge-no">No</span>'
            html_content += f"""
                    <tr>
                        <td>{rec['study_id']}</td>
                        <td>{rec['record_id']}</td>
                        <td>{rec['qids']}</td>
                        <td>{invite_badge}</td>
                        <td>{test_badge}</td>
                    </tr>
"""

        html_content += """
                </table>
            </div>
        </div>

        <div class="footer">
            <p>Stanford Precision Neurotherapeutics Lab</p>
            <p style="font-size: 12px; opacity: 0.8;">
                This report was automatically generated by the REDCap Weekly Report System (SSOT Compatible)
            </p>
        </div>
    </div>
</body>
</html>
"""

        # Save report
        report_path = f'reports/report_{timestamp}.html'
        with open(report_path, 'w') as f:
            f.write(html_content)

        return report_path

    def generate_report(self):
        """Main method to generate the complete report"""

        logger.info("="*60)
        logger.info("Starting Weekly Report Generation")
        logger.info(f"Mode: {'Including' if self.include_test_records else 'Excluding'} test records")
        logger.info("="*60)

        # Fetch and analyze data
        metrics = self.fetch_and_analyze_data()

        # Generate HTML report
        report_path = self.generate_html_report(metrics)

        # Print summary to console
        print("\n" + "="*60)
        print("WEEKLY REPORT SUMMARY")
        print("="*60)
        print(f"\n📊 Overall Statistics:")
        print(f"   Total Records: {metrics['total_records']}")
        print(f"   Real Participants: {metrics['real_records']}")
        print(f"   Test Records: {metrics['test_records']}")
        print(f"   Incomplete Surveys: {metrics['incomplete_surveys']}")

        print(f"\n🎯 Enrollment Metrics:")
        print(f"   Total Screened: {metrics['total_screened']}")
        print(f"   Total Eligible: {metrics['total_eligible_hc'] + metrics['total_eligible_mdd']}")
        print(f"   Total Ineligible: {metrics['total_ineligible']}")
        print(f"   Manual Review Required: {metrics['manual_review']}")
        print(f"   Pending Processing: {metrics['pending_processing']}")

        print(f"\n🔬 Group Distribution:")
        print(f"   Healthy Controls: {metrics['total_eligible_hc']}")
        print(f"   MDD Participants: {metrics['total_eligible_mdd']}")

        print(f"\n📧 Communications (SSOT):")
        print(f"   Invitation Emails Sent: {metrics['invitations_sent']}")
        print(f"   Ineligible Notifications Sent: {metrics['ineligible_notified']}")

        print(f"\n✅ Report Generated Successfully!")
        print(f"   Location: {report_path}")
        print("="*60)

        logger.info(f"Report saved to: {report_path}")

        return report_path, metrics

def main():
    parser = argparse.ArgumentParser(description='Generate REDCap Weekly Report (SSOT Compatible)')
    parser.add_argument('--include-test', action='store_true',
                       help='Include test records in the report')
    parser.add_argument('--test', action='store_true',
                       help='Generate report immediately (test mode)')

    args = parser.parse_args()

    # Create report generator
    generator = WeeklyReportGenerator(include_test_records=args.include_test)

    # Generate report
    report_path, metrics = generator.generate_report()

    return report_path

if __name__ == "__main__":
    main()
