#!/bin/bash

# Stanford REDCap Services Debug Script
# This script diagnoses why services aren't starting

echo "========================================"
echo "Stanford REDCap Services Debug Report"
echo "========================================"
echo "Time: $(date)"
echo "User: $(whoami)"
echo "Hostname: $(hostname)"
echo

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PROJECT_ROOT="$HOME/stanford_redcap"
ISSUES_FOUND=0

# Function to check if file exists
check_file() {
    local file=$1
    local desc=$2
    if [ -f "$file" ]; then
        echo -e "${GREEN}✓${NC} $desc exists"
        return 0
    else
        echo -e "${RED}✗${NC} $desc MISSING: $file"
        ((ISSUES_FOUND++))
        return 1
    fi
}

# Function to check Python import
check_import() {
    local module=$1
    local desc=$2
    if $PROJECT_ROOT/venv/bin/python -c "import $module" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} Python module '$module' OK"
        return 0
    else
        echo -e "${RED}✗${NC} Python module '$module' MISSING"
        ((ISSUES_FOUND++))
        return 1
    fi
}

# Function to test service startup
test_service() {
    local service_name=$1
    local service_dir=$2
    local service_file=$3
    local test_cmd=$4
    
    echo -e "\n${YELLOW}Testing $service_name...${NC}"
    
    if [ ! -f "$service_dir/$service_file" ]; then
        echo -e "${RED}✗${NC} $service_file not found in $service_dir"
        ((ISSUES_FOUND++))
        return 1
    fi
    
    cd "$service_dir"
    
    # Try to run the service for 3 seconds to capture startup errors
    timeout 3s $PROJECT_ROOT/venv/bin/python $service_file 2>&1 | head -20 > /tmp/${service_name}_test.log 2>&1
    
    # Check for common error patterns
    if grep -q "ModuleNotFoundError\|ImportError" /tmp/${service_name}_test.log; then
        echo -e "${RED}✗${NC} Import error detected:"
        grep -A2 -B2 "Error" /tmp/${service_name}_test.log | head -10
        ((ISSUES_FOUND++))
    elif grep -q "Address already in use\|Permission denied.*bind" /tmp/${service_name}_test.log; then
        echo -e "${YELLOW}⚠${NC} Port conflict detected"
        grep -i "address\|port" /tmp/${service_name}_test.log
    elif grep -q "No such file or directory" /tmp/${service_name}_test.log; then
        echo -e "${RED}✗${NC} Missing file detected:"
        grep "No such file" /tmp/${service_name}_test.log
        ((ISSUES_FOUND++))
    elif [ -s /tmp/${service_name}_test.log ]; then
        # Show first few lines of output
        echo -e "${GREEN}✓${NC} Service started (showing first lines):"
        head -5 /tmp/${service_name}_test.log
    else
        echo -e "${YELLOW}⚠${NC} No output captured - service may have issues"
    fi
    
    rm -f /tmp/${service_name}_test.log
}

echo "=== Checking Project Structure ==="
check_file "$PROJECT_ROOT/venv/bin/python" "Virtual environment"
check_file "$PROJECT_ROOT/.env" "Environment configuration"
check_file "$PROJECT_ROOT/common/email_sender.py" "Email sender module"

echo -e "\n=== Checking Service Files ==="
check_file "$PROJECT_ROOT/scheduler/scheduler.py" "Scheduler service"
check_file "$PROJECT_ROOT/dashboard/dashboard.py" "Dashboard service"
check_file "$PROJECT_ROOT/confirmations/confirm.py" "Confirmations service"
check_file "$PROJECT_ROOT/dashboard/dashboard_calendar.py" "Dashboard calendar module"

echo -e "\n=== Checking Python Environment ==="
source $PROJECT_ROOT/venv/bin/activate 2>/dev/null

# Check core dependencies
check_import "flask" "Flask web framework"
check_import "flask_cors" "Flask CORS"
check_import "streamlit" "Streamlit dashboard"
check_import "pandas" "Pandas data processing"
check_import "requests" "Requests HTTP library"
check_import "dotenv" "Python dotenv"

echo -e "\n=== Checking Ports ==="
for port in 8080 8081; do
    if netstat -tuln 2>/dev/null | grep -q ":$port "; then
        echo -e "${YELLOW}⚠${NC} Port $port is already in use:"
        netstat -tuln 2>/dev/null | grep ":$port "
    else
        echo -e "${GREEN}✓${NC} Port $port is available"
    fi
done

echo -e "\n=== Checking Environment Variables ==="
if [ -f "$PROJECT_ROOT/.env" ]; then
    # Check for required variables (without showing values)
    for var in REDCAP_API_TOKEN FLASK_SECRET_KEY; do
        if grep -q "^$var=" "$PROJECT_ROOT/.env" && ! grep -q "^$var=$\|^$var=your" "$PROJECT_ROOT/.env"; then
            echo -e "${GREEN}✓${NC} $var is set"
        else
            echo -e "${YELLOW}⚠${NC} $var might not be configured"
        fi
    done
else
    echo -e "${RED}✗${NC} .env file not found!"
    ((ISSUES_FOUND++))
fi

echo -e "\n=== Testing Service Startups ==="

# Test each service if the main file exists
if [ -f "$PROJECT_ROOT/dashboard/dashboard.py" ]; then
    test_service "dashboard" "$PROJECT_ROOT/dashboard" "dashboard.py" "streamlit run"
fi

if [ -f "$PROJECT_ROOT/scheduler/scheduler.py" ]; then
    test_service "scheduler" "$PROJECT_ROOT/scheduler" "scheduler.py" "python"
fi

if [ -f "$PROJECT_ROOT/confirmations/confirm.py" ]; then
    test_service "confirmations" "$PROJECT_ROOT/confirmations" "confirm.py" "python"
fi

echo -e "\n=== Checking Import Paths ==="
cd "$PROJECT_ROOT"
for service in dashboard/dashboard.py confirmations/confirm.py scheduler/scheduler.py; do
    if [ -f "$service" ]; then
        echo -e "\nChecking $service:"
        if grep -q "from common" "$service"; then
            if grep -q "sys.path.append" "$service"; then
                echo -e "${GREEN}✓${NC} Has sys.path.append for common imports"
            else
                echo -e "${YELLOW}⚠${NC} Uses common imports but might need sys.path.append"
            fi
        fi
        
        # Check for old import patterns
        if grep -q "from email_sender import\|from 2 import" "$service"; then
            echo -e "${YELLOW}⚠${NC} Has old import pattern that needs updating"
            grep -n "from email_sender import\|from 2 import" "$service" | head -3
        fi
    fi
done

echo -e "\n=== Quick Fixes Available ==="

if ! $PROJECT_ROOT/venv/bin/python -c "import flask" 2>/dev/null; then
    echo -e "${YELLOW}Fix:${NC} pip install flask"
fi

if [ ! -f "$PROJECT_ROOT/scheduler/scheduler.py" ]; then
    echo -e "${YELLOW}Fix:${NC} Create scheduler.py or copy from artifact"
fi

if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${YELLOW}Fix:${NC} cp $PROJECT_ROOT/.env.example $PROJECT_ROOT/.env && nano $PROJECT_ROOT/.env"
fi

echo -e "\n=== Summary ==="
if [ $ISSUES_FOUND -eq 0 ]; then
    echo -e "${GREEN}✓ No major issues found!${NC}"
    echo "Services should be able to start. Try:"
    echo "  $PROJECT_ROOT/scripts/start_all_services.sh"
else
    echo -e "${RED}✗ Found $ISSUES_FOUND issues that need fixing${NC}"
    echo -e "\nRecommended actions:"
    echo "1. Fix missing files (especially scheduler.py)"
    echo "2. Install missing Python packages"
    echo "3. Update import paths if needed"
    echo "4. Configure .env file"
fi

echo -e "\n=== Debug Log Files ==="
echo "Check these locations for more details:"
for log in scheduler dashboard confirmations; do
    logfile="$PROJECT_ROOT/$log/logs/$log.log"
    if [ -f "$logfile" ]; then
        echo "  $logfile ($(wc -l < $logfile) lines)"
    else
        echo "  $logfile (not created yet)"
    fi
done

echo -e "\nDebug report complete."