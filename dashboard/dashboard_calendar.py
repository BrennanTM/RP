"""
Calendar component for Stanford Neuro Lab Dashboard
Integrates with the in-house scheduling system
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta, date
import requests
import json
import calendar
from typing import Dict, List, Optional

# Configuration
SCHEDULER_API_URL = "http://localhost:8081/api"  # Adjust if scheduler runs on different port


def create_calendar_view(selected_date: date = None) -> go.Figure:
    """Create interactive calendar view with appointments"""
    
    if selected_date is None:
        selected_date = date.today()
    
    # Get month calendar
    cal = calendar.monthcalendar(selected_date.year, selected_date.month)
    month_name = calendar.month_name[selected_date.month]
    
    # Fetch appointments for the month
    try:
        response = requests.get(f"{SCHEDULER_API_URL}/appointments", timeout=5)
        appointments = response.json() if response.status_code == 200 else []
    except:
        appointments = []
    
    # Create appointment lookup by date
    appt_by_date = {}
    for appt in appointments:
        appt_date = appt['start'].split('T')[0]
        if appt_date not in appt_by_date:
            appt_by_date[appt_date] = []
        appt_by_date[appt_date].append(appt)
    
    # Create calendar figure
    fig = go.Figure()
    
    # Add day headers
    day_headers = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    for i, day in enumerate(day_headers):
        fig.add_annotation(
            x=i, y=len(cal),
            text=f"<b>{day}</b>",
            showarrow=False,
            font=dict(size=14)
        )
    
    # Add calendar days
    for week_num, week in enumerate(cal):
        for day_num, day in enumerate(week):
            if day == 0:
                continue
            
            # Check if this day has appointments
            day_str = f"{selected_date.year}-{selected_date.month:02d}-{day:02d}"
            day_appointments = appt_by_date.get(day_str, [])
            
            # Style based on appointments
            if day_appointments:
                bgcolor = '#e8f5e9'
                text_color = '#2e7d32'
                hover_text = f"{len(day_appointments)} appointment(s)"
            else:
                bgcolor = 'white'
                text_color = 'black'
                hover_text = "No appointments"
            
            # Highlight today
            if (day == date.today().day and 
                selected_date.month == date.today().month and 
                selected_date.year == date.today().year):
                bgcolor = '#ffecb3'
            
            # Add day cell
            fig.add_shape(
                type="rect",
                x0=day_num - 0.45, y0=len(cal) - week_num - 1.45,
                x1=day_num + 0.45, y1=len(cal) - week_num - 0.55,
                fillcolor=bgcolor,
                line=dict(color="lightgray", width=1)
            )
            
            # Add day number
            fig.add_annotation(
                x=day_num, y=len(cal) - week_num - 1,
                text=f"<b>{day}</b>",
                showarrow=False,
                font=dict(size=16, color=text_color),
                hovertext=hover_text
            )
            
            # Add appointment count if any
            if day_appointments:
                fig.add_annotation(
                    x=day_num, y=len(cal) - week_num - 1.3,
                    text=f"{len(day_appointments)}",
                    showarrow=False,
                    font=dict(size=10, color=text_color)
                )
    
    # Update layout
    fig.update_layout(
        title=f"{month_name} {selected_date.year}",
        showlegend=False,
        height=400,
        xaxis=dict(
            showgrid=False, zeroline=False, showticklabels=False,
            range=[-0.5, 6.5]
        ),
        yaxis=dict(
            showgrid=False, zeroline=False, showticklabels=False,
            range=[-0.5, len(cal) + 0.5]
        ),
        plot_bgcolor='white',
        margin=dict(l=20, r=20, t=60, b=20)
    )
    
    return fig


def create_appointment_list(selected_date: date = None) -> pd.DataFrame:
    """Create list of appointments for selected date/range"""
    try:
        response = requests.get(f"{SCHEDULER_API_URL}/appointments", timeout=5)
        if response.status_code != 200:
            return pd.DataFrame()
        
        appointments = response.json()
        
        # Convert to DataFrame
        if not appointments:
            return pd.DataFrame()
        
        df = pd.DataFrame(appointments)
        
        # Parse dates
        df['date'] = pd.to_datetime(df['start']).dt.date
        df['time'] = pd.to_datetime(df['start']).dt.strftime('%I:%M %p')
        
        # Filter by selected date if provided
        if selected_date:
            df = df[df['date'] == selected_date]
        
        # Select relevant columns
        display_df = df[['study_id', 'title', 'date', 'time', 'location', 'status']]
        display_df.columns = ['Study ID', 'Type', 'Date', 'Time', 'Location', 'Status']
        
        return display_df.sort_values(['Date', 'Time'])
        
    except Exception as e:
        st.error(f"Error fetching appointments: {e}")
        return pd.DataFrame()


def generate_scheduling_link(study_id: str, record_id: str) -> Optional[str]:
    """Generate a scheduling link for a participant"""
    try:
        response = requests.post(
            f"{SCHEDULER_API_URL}/generate-scheduling-link",
            json={'study_id': study_id, 'record_id': record_id},
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get('link')
        else:
            st.error(f"Failed to generate link: {response.text}")
            return None
            
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to scheduler. Make sure it's running on port 8081.")
        return None
    except Exception as e:
        st.error(f"Error generating link: {e}")
        return None


def render_calendar_tab():
    """Render the calendar tab in the dashboard"""
    st.header("📅 Appointment Calendar")
    
    # Check if scheduler is available
    scheduler_available = False
    try:
        response = requests.get(f"{SCHEDULER_API_URL}/appointments", timeout=2)
        scheduler_available = response.status_code == 200
    except:
        st.warning("⚠️ Scheduler service is not available. Start the scheduler to enable full functionality.")
    
    # Calendar controls
    col1, col2, col3 = st.columns([2, 2, 1])
    
    with col1:
        # Month/Year selector
        selected_month = st.date_input(
            "Select Month",
            value=date.today(),
            format="YYYY-MM-DD"
        )
    
    with col2:
        # View type
        view_type = st.selectbox(
            "View",
            ["Calendar", "List", "Week"]
        )
    
    with col3:
        # Refresh button
        if st.button("🔄 Refresh"):
            st.rerun()
    
    # Display based on view type
    if view_type == "Calendar":
        # Show calendar
        if scheduler_available:
            create_interactive_calendar_view(selected_month)
        else:
            st.info("Calendar view requires the scheduler service to be running.")
        
        # Show today's appointments
        st.subheader("Today's Appointments")
        if scheduler_available:
            today_appts = create_appointment_list(date.today())
            if not today_appts.empty:
                st.dataframe(today_appts, use_container_width=True, hide_index=True)
            else:
                st.info("No appointments scheduled for today")
        else:
            st.info("Appointment data unavailable - scheduler not connected")
    
    elif view_type == "List":
        # Date range selector
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start Date", value=date.today())
        with col2:
            end_date = st.date_input("End Date", value=date.today() + timedelta(days=30))
        
        # Fetch and display appointments
        if scheduler_available:
            all_appts = create_appointment_list()
            if not all_appts.empty:
                # Filter by date range
                mask = (all_appts['Date'] >= start_date) & (all_appts['Date'] <= end_date)
                filtered_appts = all_appts[mask]
                
                st.subheader(f"Appointments from {start_date} to {end_date}")
                st.dataframe(filtered_appts, use_container_width=True, hide_index=True)
                
                # Download option
                csv = filtered_appts.to_csv(index=False)
                st.download_button(
                    label="Download as CSV",
                    data=csv,
                    file_name=f"appointments_{start_date}_{end_date}.csv",
                    mime="text/csv"
                )
            else:
                st.info("No appointments found")
        else:
            st.info("Appointment data unavailable - scheduler not connected")
    
    elif view_type == "Week":
        # Week view
        week_start = date.today() - timedelta(days=date.today().weekday())
        week_end = week_start + timedelta(days=6)
        
        st.subheader(f"Week of {week_start.strftime('%B %d, %Y')}")
        
        if scheduler_available:
            # Create week grid
            week_data = []
            for i in range(7):
                day = week_start + timedelta(days=i)
                day_appts = create_appointment_list(day)
                
                week_data.append({
                    'Day': day.strftime('%A'),
                    'Date': day.strftime('%m/%d'),
                    'Appointments': len(day_appts),
                    'Details': day_appts['Study ID'].tolist() if not day_appts.empty else []
                })
            
            # Display week summary
            week_df = pd.DataFrame(week_data)
            st.dataframe(week_df[['Day', 'Date', 'Appointments']], use_container_width=True, hide_index=True)
            
            # Show details for selected day
            selected_day = st.selectbox(
                "Select day for details",
                options=range(7),
                format_func=lambda x: week_data[x]['Day']
            )
            
            if week_data[selected_day]['Details']:
                st.write(f"**{week_data[selected_day]['Day']} Appointments:**")
                day_date = week_start + timedelta(days=selected_day)
                day_detail = create_appointment_list(day_date)
                st.dataframe(day_detail, use_container_width=True, hide_index=True)
        else:
            st.info("Week view requires the scheduler service to be running.")
    
    # Scheduling link generator (admin feature)
    with st.expander("🔗 Generate Scheduling Link"):
        st.write("Generate a scheduling link for a participant")
        
        if not scheduler_available:
            st.error("This feature requires the scheduler service to be running on port 8081")
        else:
            col1, col2 = st.columns(2)
            with col1:
                study_id = st.text_input("Study ID", placeholder="e.g., 3466 or 10926")
            with col2:
                record_id = st.text_input("Record ID", placeholder="e.g., 123")
            
            if st.button("Generate Link"):
                if study_id and record_id:
                    link = generate_scheduling_link(study_id, record_id)
                    if link:
                        st.success("Link generated successfully!")
                        st.code(link)
                        st.info("Send this link to the participant to schedule their consent session.")
                else:
                    st.error("Please enter both Study ID and Record ID")
    
    # Statistics
    with st.expander("📊 Appointment Statistics"):
        if scheduler_available:
            all_appts = create_appointment_list()
            if not all_appts.empty:
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    total_scheduled = len(all_appts)
                    st.metric("Total Scheduled", total_scheduled)
                
                with col2:
                    this_week = len(all_appts[
                        (all_appts['Date'] >= date.today()) & 
                        (all_appts['Date'] < date.today() + timedelta(days=7))
                    ])
                    st.metric("This Week", this_week)
                
                with col3:
                    this_month = len(all_appts[
                        all_appts['Date'].apply(lambda x: x.month == date.today().month and x.year == date.today().year)
                    ])
                    st.metric("This Month", this_month)
                
                # Appointments by day of week
                all_appts['Day of Week'] = pd.to_datetime(all_appts['Date']).dt.day_name()
                day_counts = all_appts['Day of Week'].value_counts().reindex(
                    ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'], 
                    fill_value=0
                )
                
                fig = px.bar(
                    x=day_counts.index, 
                    y=day_counts.values,
                    title="Appointments by Day of Week",
                    labels={'x': 'Day', 'y': 'Count'}
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No appointment data available")
        else:
            st.info("Statistics require the scheduler service to be running.")


# Integration function for main dashboard
def add_calendar_to_dashboard(dashboard_app):
    """Add calendar tab to existing dashboard"""
    # This would be called from your main dashboard.py
    # Add a new tab for calendar
    tabs = st.tabs(["Overview", "Calendar", "Reports"])
    
    with tabs[1]:
        render_calendar_tab()


if __name__ == "__main__":
    # Standalone testing
    st.set_page_config(
        page_title="Calendar Test",
        page_icon="📅",
        layout="wide"
    )
    
    render_calendar_tab()


def create_interactive_calendar_view(selected_date: date = None) -> None:
    """Create interactive calendar with clickable days"""
    
    if selected_date is None:
        selected_date = date.today()
    
    # Store selected day in session state
    if 'selected_day' not in st.session_state:
        st.session_state.selected_day = None
    
    # Get month calendar
    cal = calendar.monthcalendar(selected_date.year, selected_date.month)
    month_name = calendar.month_name[selected_date.month]
    
    # Fetch appointments
    try:
        response = requests.get(f"{SCHEDULER_API_URL}/appointments", timeout=5)
        appointments = response.json() if response.status_code == 200 else []
    except:
        appointments = []
    
    # Create appointment lookup
    appt_by_date = {}
    for appt in appointments:
        appt_date = appt['start'].split('T')[0]
        if appt_date not in appt_by_date:
            appt_by_date[appt_date] = []
        appt_by_date[appt_date].append(appt)
    
    # Display calendar header
    st.subheader(f"{month_name} {selected_date.year}")
    
    # Create calendar grid using columns
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    
    # Day headers
    cols = st.columns(7)
    for i, day in enumerate(days):
        cols[i].markdown(f"**{day}**", unsafe_allow_html=True)
    
    # Calendar days
    for week in cal:
        cols = st.columns(7)
        for i, day in enumerate(week):
            if day == 0:
                cols[i].write("")  # Empty cell
            else:
                day_str = f"{selected_date.year}-{selected_date.month:02d}-{day:02d}"
                day_appointments = appt_by_date.get(day_str, [])
                
                # Style based on appointments
                if day_appointments:
                    button_label = f"{day} ({len(day_appointments)})"
                    button_type = "primary"
                else:
                    button_label = str(day)
                    button_type = "secondary"
                
                # Highlight today
                if (day == date.today().day and 
                    selected_date.month == date.today().month and 
                    selected_date.year == date.today().year):
                    button_label = f"📍 {button_label}"
                
                # Create clickable button for each day
                if cols[i].button(button_label, key=f"day_{day_str}", type=button_type):
                    st.session_state.selected_day = day_str
    
    # Show appointments for selected day
    if st.session_state.selected_day:
        st.markdown("---")
        selected_appointments = appt_by_date.get(st.session_state.selected_day, [])
        
        if selected_appointments:
            st.subheader(f"📅 Appointments for {st.session_state.selected_day}")
            
            for appt in selected_appointments:
                appt_time = appt['start'].split('T')[1].split(':')
                time_str = f"{appt_time[0]}:{appt_time[1]}"
                
                with st.expander(f"🕐 {time_str} - {appt.get('title', 'Appointment')}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**Study ID:** {appt.get('study_id', 'N/A')}")
                        st.write(f"**Time:** {time_str}")
                    with col2:
                        st.write(f"**Location:** {appt.get('location', 'TBD')}")
                        st.write(f"**Status:** {appt.get('status', 'Scheduled')}")
        else:
            st.info(f"No appointments scheduled for {st.session_state.selected_day}")
            
        if st.button("← Back to Calendar"):
            st.session_state.selected_day = None
            st.rerun()
