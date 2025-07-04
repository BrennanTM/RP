#!/usr/bin/env python3
"""
Continuous eligibility processor for REDCap
"""

import os
import sys
import time
import logging
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.email_sender import REDCapEligibilityProcessor, MultiProviderEmailSender, create_providers

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/eligibility.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main():
    """Main continuous processing loop"""
    
    # Configuration
    API_URL = "https://redcap.stanford.edu/api/"
    API_TOKEN = os.getenv('REDCAP_API_TOKEN')
    CHECK_INTERVAL = 10  # Check every 10 seconds
    
    if not API_TOKEN:
        logger.error("REDCAP_API_TOKEN not found!")
        return
    
    logger.info("="*60)
    logger.info("Starting REDCap Eligibility Processor")
    logger.info(f"API URL: {API_URL}")
    logger.info(f"Check interval: {CHECK_INTERVAL} seconds")
    logger.info("="*60)
    
    # Create email sender
    logger.info("Initializing email providers...")
    providers = create_providers()
    logger.info(f"Found {len(providers)} email providers")
    
    email_sender = MultiProviderEmailSender(providers)
    
    # Create processor
    processor = REDCapEligibilityProcessor(API_URL, API_TOKEN, email_sender)
    
    # Test configuration first
    logger.info("Testing configuration...")
    if not processor.test_configuration():
        logger.error("Configuration test failed! Check settings.")
        logger.error("Make sure:")
        logger.error("1. REDCap API token is valid")
        logger.error("2. Email configuration is correct")
        return
    
    logger.info("Configuration test passed ✓")
    logger.info("Starting continuous monitoring...")
    
    processed_count = 0
    check_count = 0
    
    while True:
        try:
            check_count += 1
            logger.info(f"\n--- Check #{check_count} at {datetime.now().strftime('%H:%M:%S')} ---")
            
            # Process eligible records
            processed = processor.process_eligible_records(dry_run=False)
            
            if processed:
                processed_count += len(processed)
                logger.info(f"✓ Processed {len(processed)} new eligible participants:")
                for p in processed:
                    logger.info(f"  - {p['study_id']} → {p['email']}")
            else:
                logger.info("No new eligible participants found")
            
            logger.info(f"Total processed today: {processed_count}")
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            logger.info("\nStopping processor...")
            break
        except Exception as e:
            logger.error(f"Error during processing: {e}")
            logger.exception("Full traceback:")
            logger.info("Waiting 60 seconds before retry...")
            time.sleep(60)

if __name__ == "__main__":
    main()
