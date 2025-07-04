# Stanford REDCap Project Aliases
# Add to ~/.bashrc: source ~/stanford_redcap/scripts/aliases.sh

alias redcap-start='~/stanford_redcap/scripts/start_all_services.sh'
alias redcap-stop='~/stanford_redcap/scripts/stop_all_services.sh'
alias redcap-status='~/stanford_redcap/scripts/monitor_services.sh'
alias redcap-logs='tail -f ~/stanford_redcap/*/logs/*.log'
alias redcap-backup='~/stanford_redcap/scripts/backup_data.sh'

# Service-specific commands
alias scheduler-log='tail -f ~/stanford_redcap/scheduler/logs/scheduler.log'
alias dashboard-log='tail -f ~/stanford_redcap/dashboard/logs/dashboard.log'
alias confirmations-log='tail -f ~/stanford_redcap/confirmations/logs/confirmations.log'

# Tmux shortcuts
alias tm-scheduler='tmux attach -t scheduler'
alias tm-dashboard='tmux attach -t dashboard'
alias tm-confirmations='tmux attach -t confirmations'

# Change to project directory
alias cdredcap='cd ~/stanford_redcap'

echo "REDCap aliases loaded"
