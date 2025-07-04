"""
Scheduler configuration for public access
"""
import os

# Public URL accessible from anywhere
SCHEDULER_BASE_URL = os.environ.get('SCHEDULER_URL', 
    'https://influences-progressive-registrar-route.trycloudflare.com'
)
