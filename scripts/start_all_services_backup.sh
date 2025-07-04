#!/bin/bash

PROJECT_ROOT="$HOME/stanford_redcap"
cd "$PROJECT_ROOT"

echo "Starting all Stanford REDCap services..."

# Kill existing sessions if any
tmux kill-session -t scheduler 2>/dev/null
tmux kill-session -t dashboard 2>/dev/null
tmux kill-session -t eligibility 2>/dev/null
tmux kill-session -t confirmations 2>/dev/null

# Start scheduler
tmux new-session -d -s scheduler -c "$PROJECT_ROOT/scheduler" \
    "source ../venv/bin/activate && python scheduler.py 2>&1 | tee -a logs/scheduler.log"

# Start dashboard  
tmux new-session -d -s dashboard -c "$PROJECT_ROOT/dashboard" \
    "source ../venv/bin/activate && streamlit run dashboard.py --server.port 8080 2>&1 | tee -a logs/dashboard.log"

# Start eligibility processor (if exists)
if [ -f "$PROJECT_ROOT/eligibility/processor.py" ]; then
    tmux new-session -d -s eligibility -c "$PROJECT_ROOT/eligibility" \
        "source ../venv/bin/activate && python processor.py 2>&1 | tee -a logs/eligibility.log"
fi

# Start confirmation sender
tmux new-session -d -s confirmations -c "$PROJECT_ROOT/confirmations" \
    "source ../venv/bin/activate && python confirm.py 2>&1 | tee -a logs/confirmations.log"

echo "All services started. Use 'tmux ls' to see sessions."
echo
echo "Attach to sessions:"
echo "  tmux attach -t scheduler"
echo "  tmux attach -t dashboard"
echo "  tmux attach -t confirmations"
