#!/bin/bash

echo "Stopping all Stanford REDCap services..."

services=("scheduler" "dashboard" "eligibility" "confirmations" "tracker")

for service in "${services[@]}"; do
    if tmux has-session -t "$service" 2>/dev/null; then
        tmux kill-session -t "$service"
        echo " Stopped $service"
    fi
done

echo "All services stopped."
