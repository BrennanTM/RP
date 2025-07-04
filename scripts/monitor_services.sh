#!/bin/bash

PROJECT_ROOT="$HOME/stanford_redcap"

echo "=== Stanford REDCap Services Status ==="
echo "Time: $(date)"
echo

# Check each service
services=("scheduler:8081" "dashboard:8080" "eligibility" "confirmations")

for service in "${services[@]}"; do
    IFS=':' read -r name port <<< "$service"
    
    # Check tmux session
    if tmux has-session -t "$name" 2>/dev/null; then
        echo " $name: Running in tmux"
        
        # Check port if applicable
        if [[ ! -z "$port" ]]; then
            if netstat -tuln 2>/dev/null | grep -q ":$port"; then
                echo "   Port $port: Listening"
            else
                echo "   Port $port: Not listening"
            fi
        fi
        
        # Check process
        if pgrep -f "$name.py" > /dev/null; then
            pid=$(pgrep -f "$name.py" | head -1)
            echo "   PID: $pid"
        fi
    else
        echo " $name: Not running"
    fi
    echo
done

# Check databases
echo "=== Data Status ==="
if [[ -f "$PROJECT_ROOT/scheduler/scheduler.db" ]]; then
    size=$(du -h "$PROJECT_ROOT/scheduler/scheduler.db" | cut -f1)
    count=$(sqlite3 "$PROJECT_ROOT/scheduler/scheduler.db" "SELECT COUNT(*) FROM appointments;" 2>/dev/null || echo "?")
    echo " Scheduler DB: $size, $count appointments"
else
    echo " Scheduler DB: Not found"
fi

if [[ -f "$PROJECT_ROOT/confirmations/confirmed_appointments.json" ]]; then
    size=$(du -h "$PROJECT_ROOT/confirmations/confirmed_appointments.json" | cut -f1)
    echo " Confirmations: $size"
else
    echo " Confirmations: Not found"
fi

# Check logs
echo
echo "=== Recent Log Activity ==="
for service in scheduler dashboard confirmations eligibility; do
    logfile="$PROJECT_ROOT/$service/logs/$service.log"
    if [[ -f "$logfile" ]]; then
        last_line=$(tail -1 "$logfile" 2>/dev/null)
        if [[ ! -z "$last_line" ]]; then
            echo "$service: ${last_line:0:80}..."
        fi
    fi
done

# Disk usage
echo
echo "=== Disk Usage ==="
du -sh "$PROJECT_ROOT" 2>/dev/null || echo "Unable to calculate"
