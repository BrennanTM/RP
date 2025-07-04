#!/bin/bash
# Deploy script for fixing _v2 field issue in Stanford REDCap
# This script helps implement the fix in multiple ways

set -e  # Exit on error

PROJECT_ROOT="$HOME/stanford_redcap"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "========================================"
echo "Stanford REDCap _v2 Field Fix Deployment"
echo "========================================"
echo

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Function to check if service is running
check_service() {
    if tmux has-session -t "$1" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} $1 is running"
        return 0
    else
        echo -e "${RED}✗${NC} $1 is not running"
        return 1
    fi
}

# Function to backup current files
backup_files() {
    echo "Creating backups..."
    BACKUP_DIR="$PROJECT_ROOT/backups/v2_fix_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BACKUP_DIR"
    
    # Backup current processor
    if [ -f "$PROJECT_ROOT/eligibility/adaptive_processor.py" ]; then
        cp "$PROJECT_ROOT/eligibility/adaptive_processor.py" "$BACKUP_DIR/"
        echo -e "${GREEN}✓${NC} Backed up adaptive_processor.py"
    fi
    
    # Backup field detector
    if [ -f "$PROJECT_ROOT/common/field_detector.py" ]; then
        cp "$PROJECT_ROOT/common/field_detector.py" "$BACKUP_DIR/"
        echo -e "${GREEN}✓${NC} Backed up field_detector.py"
    fi
    
    echo "Backups saved to: $BACKUP_DIR"
}

# Main menu
echo "=== Choose Implementation Method ==="
echo "1. Update Data Dictionary only (recommended)"
echo "2. Deploy Enhanced Processor only"
echo "3. Both: Update Data Dictionary AND Deploy Enhanced Processor"
echo "4. Check current status"
echo "5. Test with Record 25"
echo "6. Exit"
echo

read -p "Enter choice (1-6): " choice

case $choice in
    1)
        echo -e "\n${YELLOW}=== Updating Data Dictionary ===${NC}"
        echo "This will create a fixed data dictionary that checks both regular and _v2 fields"
        echo
        
        # Check if we have the data dictionary
        if [ -f "TristansMyCapTest_DataDictionary_20250629.csv" ]; then
            echo "Found data dictionary in current directory"
        else
            echo -e "${RED}Error:${NC} Data dictionary not found!"
            echo "Please download it from REDCap:"
            echo "1. Go to Project Setup → Data Dictionary"
            echo "2. Click 'Download the current Data Dictionary'"
            echo "3. Save as: TristansMyCapTest_DataDictionary_20250629.csv"
            exit 1
        fi
        
        # Run the fix script
        echo -e "\nRunning data dictionary fix..."
        python3 fix_data_dictionary.py
        
        if [ -f "TristansMyCapTest_DataDictionary_20250629_FIXED.csv" ]; then
            echo -e "\n${GREEN}✓ Fixed data dictionary created!${NC}"
            echo
            echo "Next steps:"
            echo "1. Go to REDCap Project Setup → Data Dictionary"
            echo "2. Click 'Upload a Data Dictionary file'"
            echo "3. Choose: TristansMyCapTest_DataDictionary_20250629_FIXED.csv"
            echo "4. Review the changes REDCap shows"
            echo "5. Click 'Commit Changes'"
            echo
            echo -e "${YELLOW}Important:${NC} After upload, test with Record 25!"
        fi
        ;;
        
    2)
        echo -e "\n${YELLOW}=== Deploying Enhanced Processor ===${NC}"
        echo "This will update the processor to handle _v2 fields automatically"
        echo
        
        # Check current status
        echo "Current service status:"
        check_service "eligibility"
        echo
        
        read -p "Continue with deployment? (y/n): " confirm
        if [ "$confirm" != "y" ]; then
            echo "Deployment cancelled"
            exit 0
        fi
        
        # Backup current files
        backup_files
        
        # Deploy enhanced processor
        echo -e "\nDeploying enhanced processor..."
        cp adaptive_processor_v2.py "$PROJECT_ROOT/eligibility/"
        
        # Update the run_continuous.py to use new processor
        cat > "$PROJECT_ROOT/eligibility/run_continuous_v2.py" << 'EOF'
#!/usr/bin/env python3
"""Run the V2 enhanced processor in continuous mode"""
import sys
import time
from adaptive_processor_v2 import main

# Simulate selecting option 4 (continuous mode)
class ContinuousInput:
    def __init__(self):
        self.returned_4 = False
        
    def __call__(self, prompt):
        if not self.returned_4:
            self.returned_4 = True
            return '4'
        time.sleep(60)
        return ''

# Replace input with our automated version
import builtins
builtins.input = ContinuousInput()

if __name__ == "__main__":
    main()
EOF
        chmod +x "$PROJECT_ROOT/eligibility/run_continuous_v2.py"
        
        # Restart the service
        echo -e "\nRestarting eligibility service..."
        tmux kill-session -t eligibility 2>/dev/null || true
        sleep 2
        
        tmux new-session -d -s eligibility -c "$PROJECT_ROOT/eligibility" \
            "source ../venv/bin/activate && python run_continuous_v2.py 2>&1 | tee -a logs/eligibility.log"
        
        echo -e "${GREEN}✓ Enhanced processor deployed and running!${NC}"
        echo
        echo "The processor will now:"
        echo "- Check both regular and _v2 fields automatically"
        echo "- Process participants like Record 25 who only have _v2 data"
        echo "- Log when it uses _v2 fields for eligibility"
        echo
        echo "Monitor logs with: tmux attach -t eligibility"
        ;;
        
    3)
        echo -e "\n${YELLOW}=== Full Implementation ===${NC}"
        echo "This will update both the data dictionary AND deploy the enhanced processor"
        echo
        
        # Run both options
        $0 1  # Run option 1
        echo -e "\n${YELLOW}Press Enter to continue with processor deployment...${NC}"
        read
        $0 2  # Run option 2
        ;;
        
    4)
        echo -e "\n${YELLOW}=== Current Status ===${NC}"
        
        # Check services
        echo -e "\nServices:"
        for service in eligibility scheduler dashboard confirmations; do
            check_service "$service"
        done
        
        # Check for _v2 processor
        echo -e "\nProcessor version:"
        if [ -f "$PROJECT_ROOT/eligibility/adaptive_processor_v2.py" ]; then
            echo -e "${GREEN}✓${NC} Enhanced V2 processor is installed"
        else
            echo -e "${YELLOW}⚠${NC} Enhanced V2 processor not installed"
        fi
        
        # Check recent logs
        echo -e "\nRecent eligibility log entries:"
        if [ -f "$PROJECT_ROOT/eligibility/logs/eligibility.log" ]; then
            tail -5 "$PROJECT_ROOT/eligibility/logs/eligibility.log" | grep -E "(eligible|_v2|Record 25)" || echo "No relevant entries"
        fi
        ;;
        
    5)
        echo -e "\n${YELLOW}=== Testing Record 25 ===${NC}"
        
        # Create test script
        cat > test_record_25.py << 'EOF'
import os
import sys
import requests
import json
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://redcap.stanford.edu/api/"
API_TOKEN = os.getenv('REDCAP_API_TOKEN')

# Fetch Record 25
data = {
    'token': API_TOKEN,
    'content': 'record',
    'format': 'json',
    'records': '25'
}

response = requests.post(API_URL, data=data)
if response.status_code == 200:
    records = json.loads(response.text)
    if records:
        record = records[0]
        print("=== Record 25 Analysis ===")
        print(f"Overall Eligibility: {record.get('overall_eligibility', 'Not set')}")
        print(f"Email sent: {record.get('eligibility_email_sent', '0')}")
        
        # Check _v2 fields
        v2_fields = {k: v for k, v in record.items() if k.endswith('_v2') and v}
        print(f"\nPopulated _v2 fields: {len(v2_fields)}")
        
        # Key fields
        print(f"\nKey values:")
        print(f"  Email: {record.get('participant_email_ee6446_a52d9a_v2', 'Not found')}")
        print(f"  Age: {record.get('age_c4982e_ee0b48_0fa205_450721_v2', 'Not found')}")
        print(f"  QIDS: {record.get('qids_score_screening_42b0d5_714930_v2', 'Not found')}")
        print(f"  Travel: {record.get('travel_e4c69a_ec4b4a_09fbe2_0f2753_v2', 'Not found')}")
        print(f"  English: {record.get('english_5c066f_a95c48_a35a95_85e413_v2', 'Not found')}")
        print(f"  TMS Contra: {record.get('tms_contra_d3aef1_4917df_ffe8d8_441f15_v2', 'Not found')}")
        
        # Manual eligibility check
        age = int(record.get('age_c4982e_ee0b48_0fa205_450721_v2', 0))
        qids = int(record.get('qids_score_screening_42b0d5_714930_v2', 0))
        travel = record.get('travel_e4c69a_ec4b4a_09fbe2_0f2753_v2', '0')
        english = record.get('english_5c066f_a95c48_a35a95_85e413_v2', '0')
        tms = record.get('tms_contra_d3aef1_4917df_ffe8d8_441f15_v2', '1')
        
        manually_eligible = (18 <= age <= 65 and travel == '1' and 
                           english == '1' and tms == '0')
        
        print(f"\nManual eligibility check: {'ELIGIBLE' if manually_eligible else 'NOT ELIGIBLE'}")
        print(f"  Age OK: {18 <= age <= 65} (age={age})")
        print(f"  Travel OK: {travel == '1'}")
        print(f"  English OK: {english == '1'}")
        print(f"  TMS OK: {tms == '0'}")
        print(f"  QIDS for category: {qids} ({'HC' if qids < 11 else 'MDD'})")
        
        if manually_eligible and record.get('overall_eligibility', '0') == '0':
            print(f"\n⚠️  ISSUE DETECTED: Participant is eligible but overall_eligibility = 0")
            print("   This confirms the _v2 field issue needs fixing!")
EOF
        
        echo "Running test..."
        cd "$PROJECT_ROOT"
        source venv/bin/activate
        python test_record_25.py
        rm test_record_25.py
        ;;
        
    6)
        echo "Exiting..."
        exit 0
        ;;
        
    *)
        echo -e "${RED}Invalid choice${NC}"
        exit 1
        ;;
esac

echo -e "\n${GREEN}Done!${NC}"
