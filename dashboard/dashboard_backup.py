import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import requests
import json
import os
from dotenv import load_dotenv
import time
import sqlite3

# Import the calendar module
from dashboard_calendar import render_calendar_tab

# Load environment variables
load_dotenv()

# Configure Streamlit page
st.set_page_config(
    page_title="Stanford Neuro Lab Dashboard",
    page_icon=">�",
    layout="wide",
    initial_sidebar_state="expanded"
)

class REDCapDashboard:
    def __init__(self, api_url, api_token):
        self.api_url = api_url
        self.api_token = api_token
        
    @st.cache_data(ttl=300)  # Cache for 5 minutes
    def fetch_all_records(_self):
        """Fetch all records from REDCap with caching"""
        data = {
            'token': _self.api_token,
            'content': 'record',
            'format': 'json',
            'rawOrLabel': 'raw',
            'rawOrLabelHeaders': 'raw',
            'exportCheckboxLabel': 'false',
            'exportSurveyFields': 'false',
            'exportDataAccessGroups': 'false',
            'returnFormat': 'json'
        }
        
        response = requests.post(_self.api_url, data=data)
        if response.status_code == 200:
            return pd.DataFrame(json.loads(response.text))
        else:
            st.error(f"Failed to fetch data: {response.status_code}")
            return pd.DataFrame()
    
    def categorize_participants(self, df):
        """Categorize participants as HC or MDD based on study ID ranges"""
        def get_category(row):
            study_id = row.get('study_id', '')
            if not study_id:
                # Fallback to QIDS score if no study ID assigned yet
                return 'HC' if pd.notna(row.get('qids_score')) and int(row.get('qids_score', 0)) < 11 else 'MDD'
            
            try:
                # Extract numeric ID
                if '-' in study_id:
                    # Old format with prefix - use the prefix
                    if study_id.startswith('HC-'):
                        return 'HC'
                    elif study_id.startswith('MDD-'):
                        return 'MDD'
                else:
                    # New format - use numeric ranges
                    id_num = int(study_id)
                    if id_num < 10000:
                        return 'HC'
                    else:
                        return 'MDD'
            except:
                # If we can't parse, fall back to QIDS
                return 'HC' if pd.notna(row.get('qids_score')) and int(row.get('qids_score', 0)) < 11 else 'MDD'
        
        df['participant_category'] = df.apply(get_category, axis=1)
        return df
    
    def strip_study_id_prefix(self, study_id):
        """Remove HC- or MDD- prefix from study ID, return only numbers"""
        if pd.isna(study_id) or study_id == '':
            return ''
        
        study_id = str(study_id)
        
        # Remove HC- or MDD- prefix if present
        if study_id.startswith('HC-') or study_id.startswith('MDD-'):
            return study_id.split('-', 1)[1]
        
        # Already numeric
        return study_id
    
    def determine_ineligibility_reason(self, row):
        """Determine why a participant is ineligible based on major_exclusion and other fields"""
        # First check if they're actually eligible
        if row.get('is_eligible_basic') == '1':
            return 'Eligible'
        
        reasons = []
        
        # Check major_exclusion field
        exclusion_map = {
            '1': 'History of brain injury or neurological disorder',
            '2': 'Current psychotic symptoms',
            '3': 'Active substance abuse',
            '4': 'Pregnancy or planning pregnancy',
            '5': 'Metal implants incompatible with MRI/TMS',
            '6': 'None of the above'
        }
        
        major_exclusion = str(row.get('major_exclusion', '')).strip()
        if major_exclusion and major_exclusion in exclusion_map and major_exclusion != '6':
            reasons.append(exclusion_map[major_exclusion])
        
        # Check other eligibility fields that might exist
        # Age criteria (if there's an age field)
        if 'age' in row and pd.notna(row['age']):
            try:
                age = float(row['age'])
                if age < 18:
                    reasons.append('Under 18 years old')
                elif age > 65:
                    reasons.append('Over 65 years old')
            except:
                pass
        
        # Check if there are other exclusion fields (adjust based on your REDCap)
        # For example, if you have separate yes/no fields for each criterion:
        field_mappings = {
            'exclude_neurological': 'History of brain injury or neurological disorder',
            'exclude_psychotic': 'Current psychotic symptoms',
            'exclude_substance': 'Active substance abuse',
            'exclude_pregnant': 'Pregnancy or planning pregnancy',
            'exclude_metal': 'Metal implants incompatible with MRI/TMS',
            'exclude_age': 'Age outside eligible range',
            'exclude_medications': 'Excluded medications',
            'exclude_claustrophobia': 'Claustrophobia'
        }
        
        for field, reason in field_mappings.items():
            if row.get(field) == '1':  # Assuming '1' means yes/excluded
                if reason not in reasons:  # Avoid duplicates
                    reasons.append(reason)
        
        # If no specific reason found but marked ineligible
        if not reasons:
            return 'Did not meet inclusion criteria'
        
        # Return the first reason if only one, or combine if multiple
        return reasons[0] if len(reasons) == 1 else '; '.join(reasons)
    
    def calculate_metrics(self, df):
        """Calculate key metrics for dashboard"""
        # Total screened - all records
        total_screened = len(df)
        
        # Eligible participants
        eligible_df = df[df['is_eligible_basic'] == '1']
        total_eligible = len(eligible_df)
        
        # Categories within eligible
        hc_eligible = len(eligible_df[eligible_df['participant_category'] == 'HC'])
        mdd_eligible = len(eligible_df[eligible_df['participant_category'] == 'MDD'])
        
        # Email metrics
        emails_sent_total = len(eligible_df[eligible_df['eligibility_email_sent'] == '1'])
        emails_sent_hc = len(eligible_df[(eligible_df['participant_category'] == 'HC') & 
                                        (eligible_df['eligibility_email_sent'] == '1')])
        emails_sent_mdd = len(eligible_df[(eligible_df['participant_category'] == 'MDD') & 
                                         (eligible_df['eligibility_email_sent'] == '1')])
        
        # Calculate percentages
        hc_email_pct = (emails_sent_hc / hc_eligible * 100) if hc_eligible > 0 else 0
        mdd_email_pct = (emails_sent_mdd / mdd_eligible * 100) if mdd_eligible > 0 else 0
        
        # Ineligible analysis
        ineligible_df = df[df['is_eligible_basic'] != '1']
        total_ineligible = len(ineligible_df)
        
        # Scheduler metrics
        if 'scheduler_booked' in df.columns:
            scheduler_appointments = len(df[df['scheduler_booked'] == 1])
            calendly_appointments = len(df[df.get('calendly_booked', '0') == '1'])
        else:
            scheduler_appointments = 0
            calendly_appointments = len(eligible_df[eligible_df.get('calendly_booked', '0') == '1'])
        
        metrics = {
            'total_screened': total_screened,
            'total_eligible': total_eligible,
            'total_ineligible': total_ineligible,
            'eligible_percentage': (total_eligible / total_screened * 100) if total_screened > 0 else 0,
            'hc_count': hc_eligible,
            'mdd_count': mdd_eligible,
            'emails_sent': emails_sent_total,
            'emails_sent_hc': emails_sent_hc,
            'emails_sent_mdd': emails_sent_mdd,
            'hc_email_percentage': hc_email_pct,
            'mdd_email_percentage': mdd_email_pct,
            'pending_emails': total_eligible - emails_sent_total,
            'scheduled_count': calendly_appointments,
            'scheduler_appointments': scheduler_appointments,
            'calendly_appointments': calendly_appointments,
            'completed_count': 0  # Add session completion tracking if available
        }
        
        return metrics
    
    def analyze_ineligibility_reasons(self, df):
        """Analyze reasons for ineligibility"""
        ineligible_df = df[df['is_eligible_basic'] != '1'].copy()
        
        if len(ineligible_df) == 0:
            return pd.DataFrame()
        
        # Get reasons for each ineligible participant
        ineligible_df['reason'] = ineligible_df.apply(self.determine_ineligibility_reason, axis=1)
        
        # Count reasons (handle multiple reasons per participant)
        all_reasons = []
        for reasons_str in ineligible_df['reason']:
            if reasons_str and reasons_str != 'Eligible':
                # Split by semicolon in case of multiple reasons
                reasons = [r.strip() for r in reasons_str.split(';')]
                all_reasons.extend(reasons)
        
        # Create summary
        if all_reasons:
            reason_counts = pd.Series(all_reasons).value_counts()
            total_ineligible = len(ineligible_df)
            reason_df = pd.DataFrame({
                'Reason': reason_counts.index,
                'Count': reason_counts.values,
                'Percentage': (reason_counts.values / total_ineligible * 100).round(1)
            })
            return reason_df
        else:
            return pd.DataFrame()
    
    def create_enrollment_timeline(self, df):
        """Create enrollment timeline chart"""
        # Filter eligible participants with timestamps
        eligible_df = df[df['is_eligible_basic'] == '1'].copy()
        
        # Parse timestamps (adjust field name as needed)
        if 'timestamp' in eligible_df.columns:
            eligible_df['date'] = pd.to_datetime(eligible_df['timestamp']).dt.date
            
            # Group by date and category
            timeline = eligible_df.groupby(['date', 'participant_category']).size().reset_index(name='count')
            
            # Create cumulative sum
            timeline['cumulative'] = timeline.groupby('participant_category')['count'].cumsum()
            
            fig = px.line(timeline, x='date', y='cumulative', color='participant_category',
                         title='Enrollment Timeline',
                         labels={'cumulative': 'Total Enrolled', 'date': 'Date'},
                         color_discrete_map={'HC': '#2ecc71', 'MDD': '#e74c3c'})
            
            return fig
        return None
    
    def create_status_distribution(self, df):
        """Create participant status distribution chart"""
        eligible_df = df[df['is_eligible_basic'] == '1'].copy()
        
        # Define status based on your workflow
        def get_status(row):
            if row.get('session_completed') == '1':
                return 'Completed'
            elif row.get('scheduler_booked', 0) == 1 or row.get('calendly_booked') == '1':
                return 'Scheduled'
            elif row.get('eligibility_email_sent') == '1':
                return 'Email Sent'
            else:
                return 'Pending Email'
        
        eligible_df['status'] = eligible_df.apply(get_status, axis=1)
        
        # Create grouped bar chart
        status_counts = eligible_df.groupby(['participant_category', 'status']).size().reset_index(name='count')
        
        fig = px.bar(status_counts, x='participant_category', y='count', color='status',
                    title='Participant Status by Category',
                    labels={'count': 'Number of Participants'},
                    color_discrete_sequence=px.colors.qualitative.Set3)
        
        return fig
    
    def create_ineligibility_breakdown(self, df):
        """Create pie chart of ineligibility reasons"""
        reason_df = self.analyze_ineligibility_reasons(df)
        
        if not reason_df.empty:
            fig = px.pie(reason_df, values='Count', names='Reason',
                        title='Ineligibility Reasons Breakdown',
                        hover_data=['Percentage'])
            fig.update_traces(textposition='inside', textinfo='percent+label')
            return fig
        return None
    
    def create_weekly_summary(self, df):
        """Create weekly enrollment summary"""
        eligible_df = df[df['is_eligible_basic'] == '1'].copy()
        
        if 'timestamp' in eligible_df.columns:
            eligible_df['week'] = pd.to_datetime(eligible_df['timestamp']).dt.to_period('W')
            weekly = eligible_df.groupby(['week', 'participant_category']).size().reset_index(name='count')
            
            # Convert period to timestamp for plotting
            weekly['week_start'] = weekly['week'].dt.to_timestamp()
            
            fig = px.bar(weekly, x='week_start', y='count', color='participant_category',
                        title='Weekly Enrollment Summary',
                        labels={'count': 'New Enrollments', 'week_start': 'Week Starting'},
                        color_discrete_map={'HC': '#2ecc71', 'MDD': '#e74c3c'})
            
            return fig
        return None


# Scheduler Integration Functions
def get_scheduler_appointments():
    """Get appointments from the in-house scheduler database"""
    scheduler_db_path = os.path.expanduser('~/stanford_redcap/scheduler/scheduler.db')
    
    try:
        conn = sqlite3.connect(scheduler_db_path)
        
        # Get appointment data from scheduler
        query = """
        SELECT 
            study_id,
            record_id,
            appointment_type,
            appointment_date,
            appointment_time,
            status,
            confirmation_token,
            created_at,
            reminder_sent,
            confirmation_sent
        FROM appointments
        WHERE status = 'scheduled'
        ORDER BY appointment_date DESC, appointment_time DESC
        """
        
        scheduler_df = pd.read_sql_query(query, conn)
        conn.close()
        
        # Map appointment types
        appointment_type_map = {
            'consent': 'Consent Session',
            'mri': 'MRI Session',
            'tms': 'TMS Session'
        }
        
        scheduler_df['appointment_type_name'] = scheduler_df['appointment_type'].map(appointment_type_map)
        
        return scheduler_df
        
    except Exception as e:
        print(f"Error reading scheduler database: {e}")
        return pd.DataFrame()


def enhance_participant_data(df):
    """Enhance REDCap data with in-house scheduler data"""
    # Get scheduler appointments
    scheduler_data = get_scheduler_appointments()
    
    if scheduler_data.empty:
        # If no scheduler data, return original dataframe with new columns
        df['scheduler_booked'] = 0
        df['scheduler_appointment_type'] = ''
        df['scheduler_appointment_date'] = ''
        df['scheduler_appointment_time'] = ''
        df['scheduler_status'] = ''
        df['uses_inhouse_scheduler'] = 0
    else:
        # Merge scheduler data with REDCap data
        # First, ensure study_id columns are strings and match format
        df['study_id_str'] = df['study_id'].astype(str)
        scheduler_data['study_id_str'] = scheduler_data['study_id'].astype(str)
        
        # Merge on study_id
        merged = df.merge(
            scheduler_data[['study_id_str', 'appointment_type_name', 'appointment_date', 
                          'appointment_time', 'status', 'confirmation_sent']],
            left_on='study_id_str',
            right_on='study_id_str',
            how='left'
        )
        
        # Fill in scheduler columns
        merged['scheduler_booked'] = merged['appointment_date'].notna().astype(int)
        merged['scheduler_appointment_type'] = merged['appointment_type_name'].fillna('')
        merged['scheduler_appointment_date'] = merged['appointment_date'].fillna('')
        merged['scheduler_appointment_time'] = merged['appointment_time'].fillna('')
        merged['scheduler_status'] = merged['status'].fillna('')
        merged['uses_inhouse_scheduler'] = merged['scheduler_booked']
        
        # Drop temporary columns
        df = merged.drop(['study_id_str', 'appointment_type_name', 'appointment_date', 
                         'appointment_time', 'status'], axis=1)
    
    # Create display columns that prefer scheduler data over Calendly data
    df['display_booked'] = df.apply(
        lambda row: row['scheduler_booked'] if row['scheduler_booked'] else row.get('calendly_booked', 0),
        axis=1
    )
    
    df['display_appointment_type'] = df.apply(
        lambda row: row['scheduler_appointment_type'] if row['scheduler_appointment_type'] 
                    else ('Calendly' if row.get('calendly_booked') == '1' else ''),
        axis=1
    )
    
    df['display_appointment_date'] = df.apply(
        lambda row: row['scheduler_appointment_date'] if row['scheduler_appointment_date']
                    else (row.get('calendly_date', '') if row.get('calendly_booked') == '1' else ''),
        axis=1
    )
    
    df['display_appointment_time'] = df.apply(
        lambda row: row['scheduler_appointment_time'] if row['scheduler_appointment_time']
                    else (row.get('calendly_time', '') if row.get('calendly_booked') == '1' else ''),
        axis=1
    )
    
    df['scheduling_system'] = df.apply(
        lambda row: 'In-House' if row['uses_inhouse_scheduler'] 
                    else ('Calendly' if row.get('calendly_booked') == '1' else 'None'),
        axis=1
    )
    
    return df


def create_recent_eligible_table(df):
    """Create the recent eligible participants table with scheduler integration"""
    # Show recent eligible participants
    recent_eligible = df[df['is_eligible_basic'] == '1'].copy()
    
    if not recent_eligible.empty:
        # Strip prefixes from study IDs for display
        recent_eligible['study_id_display'] = recent_eligible['study_id'].apply(
            lambda x: x.split('-')[1] if pd.notna(x) and '-' in str(x) else str(x) if pd.notna(x) else ''
        )
        
        # Select relevant columns for display
        display_cols = [
            'record_id', 
            'study_id_display', 
            'participant_category',
            'eligibility_email_sent', 
            'email_sent_timestamp',
            'display_booked',  # Uses scheduler or Calendly
            'display_appointment_type',
            'display_appointment_date',
            'display_appointment_time',
            'scheduling_system'  # Shows which system was used
        ]
        
        # Filter columns that exist
        display_cols = [col for col in display_cols if col in recent_eligible.columns]
        
        # Sort by record_id descending (most recent first)
        recent_eligible = recent_eligible[display_cols].sort_values('record_id', ascending=False).head(10)
        
        # Rename columns for display
        column_renames = {
            'record_id': 'Record ID',
            'study_id_display': 'Study ID',
            'participant_category': 'Category',
            'eligibility_email_sent': 'Email Sent',
            'email_sent_timestamp': 'Sent Time',
            'display_booked': 'Booked',
            'display_appointment_type': 'Appt Type',
            'display_appointment_date': 'Appt Date',
            'display_appointment_time': 'Appt Time',
            'scheduling_system': 'System'
        }
        
        recent_eligible = recent_eligible.rename(columns=column_renames)
        
        return recent_eligible
    else:
        return pd.DataFrame()


def render_overview_tab(dashboard, df, metrics):
    """Render the overview tab content"""
    # Display top-level metrics
    st.subheader("Overview Metrics")
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="Total Screened",
            value=metrics['total_screened'],
            delta=f"{metrics['eligible_percentage']:.1f}% eligible"
        )
    
    with col2:
        st.metric(
            label="Total Eligible",
            value=metrics['total_eligible'],
            delta=f"{metrics['pending_emails']} pending emails"
        )
    
    with col3:
        st.metric(
            label="Total Ineligible",
            value=metrics['total_ineligible'],
            delta=f"{100 - metrics['eligible_percentage']:.1f}% of screened"
        )
    
    with col4:
        st.metric(
            label="Appointments Scheduled",
            value=metrics['scheduler_appointments'] + metrics['calendly_appointments'],
            delta=f"{metrics['scheduler_appointments']} via new system"
        )
    
    # Category breakdown
    st.subheader("Participant Categories")
    cat_col1, cat_col2, cat_col3, cat_col4 = st.columns(4)
    
    with cat_col1:
        st.metric(
            label="Healthy Controls",
            value=metrics['hc_count'],
            delta=f"{(metrics['hc_count']/metrics['total_eligible']*100):.1f}%" if metrics['total_eligible'] > 0 else "0%"
        )
    
    with cat_col2:
        st.metric(
            label="MDD Participants",
            value=metrics['mdd_count'],
            delta=f"{(metrics['mdd_count']/metrics['total_eligible']*100):.1f}%" if metrics['total_eligible'] > 0 else "0%"
        )
    
    with cat_col3:
        st.metric(
            label="HC Emails Sent",
            value=f"{metrics['emails_sent_hc']}/{metrics['hc_count']}",
            delta=f"{metrics['hc_email_percentage']:.1f}%"
        )
    
    with cat_col4:
        st.metric(
            label="MDD Emails Sent",
            value=f"{metrics['emails_sent_mdd']}/{metrics['mdd_count']}",
            delta=f"{metrics['mdd_email_percentage']:.1f}%"
        )
    
    st.markdown("---")
    
    # Charts in two columns
    chart_col1, chart_col2 = st.columns(2)
    
    with chart_col1:
        # Enrollment timeline
        timeline_fig = dashboard.create_enrollment_timeline(df)
        if timeline_fig:
            st.plotly_chart(timeline_fig, use_container_width=True)
        else:
            st.info("No timestamp data available for timeline")
    
    with chart_col2:
        # Status distribution
        status_fig = dashboard.create_status_distribution(df)
        st.plotly_chart(status_fig, use_container_width=True)
    
    # Ineligibility Analysis Section
    st.markdown("---")
    st.subheader("Ineligibility Analysis")
    
    inelig_col1, inelig_col2 = st.columns([1, 2])
    
    with inelig_col1:
        # Ineligibility reasons table
        reason_df = dashboard.analyze_ineligibility_reasons(df)
        if not reason_df.empty:
            st.dataframe(reason_df, use_container_width=True, hide_index=True)
        else:
            st.info("No ineligible participants found")
    
    with inelig_col2:
        # Ineligibility pie chart
        inelig_fig = dashboard.create_ineligibility_breakdown(df)
        if inelig_fig:
            st.plotly_chart(inelig_fig, use_container_width=True)
    
    # Weekly summary (full width)
    weekly_fig = dashboard.create_weekly_summary(df)
    if weekly_fig:
        st.plotly_chart(weekly_fig, use_container_width=True)
    
    # Recent activity table with scheduler integration
    st.subheader("Recent Eligible Participants")
    
    # Enhance data with scheduler information
    df = enhance_participant_data(df)
    
    # Show recent eligible participants with scheduler data
    recent_eligible = create_recent_eligible_table(df)
    
    if not recent_eligible.empty:
        st.dataframe(recent_eligible, use_container_width=True, hide_index=True)
    else:
        st.info("No eligible participants found")


def main():
    st.title(">� Stanford Precision Neurotherapeutics Lab Dashboard")
    st.markdown("---")
    
    # Initialize dashboard
    API_URL = "https://redcap.stanford.edu/api/"
    API_TOKEN = os.getenv('REDCAP_API_TOKEN')
    
    if not API_TOKEN:
        st.error("REDCap API token not found in environment variables!")
        return
    
    dashboard = REDCapDashboard(API_URL, API_TOKEN)
    
    # Sidebar controls
    with st.sidebar:
        st.header("Dashboard Controls")
        
        # Auto-refresh toggle
        auto_refresh = st.checkbox("Auto-refresh (every 60s)", value=True)
        if auto_refresh:
            st.info("Dashboard will refresh every 60 seconds")
        
        # Manual refresh button
        if st.button("= Refresh Now"):
            st.cache_data.clear()
            st.rerun()
        
        # Date range filter (optional)
        st.subheader("Filters")
        date_range = st.date_input(
            "Date Range",
            value=(datetime.now() - timedelta(days=30), datetime.now()),
            max_value=datetime.now()
        )
    
    # Create tabs
    tab1, tab2 = st.tabs(["=� Overview", "=� Calendar"])
    
    with tab1:
        # Fetch and process data
        with st.spinner("Loading data from REDCap..."):
            df = dashboard.fetch_all_records()
            df = dashboard.categorize_participants(df)
            metrics = dashboard.calculate_metrics(df)
        
        # Render overview tab
        render_overview_tab(dashboard, df, metrics)
    
    with tab2:
        # Render calendar tab
        render_calendar_tab()
    
    # Auto-refresh logic
    if auto_refresh:
        time.sleep(60)
        st.rerun()
    
    # Footer
    st.markdown("---")
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    st.caption("Dashboard for Stanford Precision Neurotherapeutics Lab - REDCap Integration")


if __name__ == "__main__":
    main()