# This is the updated send_eligibility_email method
# Copy this into email_sender.py replacing the existing method

def send_eligibility_email(self, email: str, study_id: str, record_id: str) -> bool:
    """Send eligibility notification email with personalized scheduling link"""
    
    # Generate personalized scheduling link using the new in-house scheduler
    scheduling_link = self.generate_scheduling_link(study_id, record_id)
    
    subject = f"Stanford Neuroscience Study Eligibility - Participant {study_id}"
    
    body = f"""Hello from the Stanford Neuroscience Institute!

I am reaching out from the Precision Neurotherapeutics Lab at Stanford University because you recently filled out the screening survey for one of our studies. Based on your responses, you may be eligible to participate in the study!

Your Study ID is: {study_id}

Measuring brain activity in humans is critical to better understand important cognitive processes (memory, language, vision) and gain insight into brain diseases. We have developed a new and improved way to quantify how the brain is connected using EEG brain recordings after applying Transcranial Magnetic Stimulation (TMS), a non-invasive and safe method that has been around for 30+ years. There are some signals in this methodology that we need to better understand before this tool can be helpful. That's where we could use your help!

**Study Details:**
Participation in the study would entail two separate visits to Stanford between 8am and 5pm during weekdays:
- One 45-minute MRI session (all ear piercings must be removed)
- One 6.5-hour TMS-EEG session

The MRI will be scheduled before the TMS to help us identify the stimulation target for the TMS session. In the TMS-EEG session, we will apply single and/or repetitive pulses of TMS and measure your brain activity using EEG.

**Compensation:**
You will be compensated hourly for your time.

**Next Steps:**
If you are still interested in participating, we would like to first meet with you via Zoom for a one-hour virtual session to review and sign the consent and additional forms together. We may also schedule your MRI and TMS sessions during this call.

**To Schedule Your Consent Session:**
Please use your personalized link below to schedule your consent session:

{scheduling_link}

This link is unique to you and will allow you to:
- View available appointment times
- Select a convenient time for your consent session
- Receive an immediate confirmation email

**Important Notes:**
- The consent session will be conducted via Zoom
- You will receive the Zoom link in your confirmation email
- Please have a quiet, private space available for the session

If you have any questions or need assistance with scheduling, please don't hesitate to contact us.

Thank you so much for your interest in our study!

Best,
Stanford Precision Neurotherapeutics Lab
Department of Psychiatry and Behavioral Sciences
Stanford University Medical Center"""
    
    # Apply rate limiting
    time.sleep(self.rate_limit_delay)
    
    # Add custom tracking data
    kwargs = {
        'categories': ['redcap', 'eligibility', 'neuroscience'],
        'custom_args': {
            'study_id': study_id,
            'record_id': record_id,
            'email_type': 'eligibility_notification',
            'department': 'psychiatry',
            'scheduler_type': 'stanford_scheduler'
        },
        'reply_to': os.environ.get('STUDY_COORDINATOR_EMAIL', 'noreply@stanford.edu')
    }
    
    return self.email_sender.send_email(email, subject, body, **kwargs)

def generate_scheduling_link(self, study_id: str, record_id: str) -> str:
    """Generate a personalized scheduling link for a participant"""
    try:
        import requests
        
        # Use the internal scheduler API
        response = requests.post(
            'http://localhost:8081/api/generate-scheduling-link',
            json={'study_id': study_id, 'record_id': record_id},
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            link = data.get('link', '')
            # For production, you might want to use the full hostname
            # link = link.replace('171.64.52.112', 'keller-14JY2L3.stanford.edu')
            return link
        else:
            logger.error(f"Failed to generate scheduling link: {response.status_code}")
            # Fallback to a generic message
            return "[Scheduling link generation failed - please contact study coordinator]"
    except Exception as e:
        logger.error(f"Error generating scheduling link: {e}")
        return "[Scheduling link generation failed - please contact study coordinator]"
