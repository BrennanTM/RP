#!/bin/bash

PROJECT_ROOT="$HOME/stanford_redcap"
BACKUP_DIR="$PROJECT_ROOT/data/backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "Creating backup in: $BACKUP_DIR"

# Backup databases
if [ -f "$PROJECT_ROOT/scheduler/scheduler.db" ]; then
    cp "$PROJECT_ROOT/scheduler/scheduler.db" "$BACKUP_DIR/"
    echo " Backed up scheduler database"
fi

# Backup JSON data files
if [ -f "$PROJECT_ROOT/confirmations/confirmed_appointments.json" ]; then
    cp "$PROJECT_ROOT/confirmations/confirmed_appointments.json" "$BACKUP_DIR/"
    echo " Backed up confirmations data"
fi

# Backup environment file (without sensitive data)
if [ -f "$PROJECT_ROOT/.env" ]; then
    grep -v "TOKEN\|PASSWORD\|KEY" "$PROJECT_ROOT/.env" > "$BACKUP_DIR/.env.sanitized"
    echo " Backed up sanitized environment"
fi

# Create logs archive
tar -czf "$BACKUP_DIR/logs_$(date +%Y%m%d).tar.gz" -C "$PROJECT_ROOT" \
    --exclude='*.log.*' \
    scheduler/logs dashboard/logs confirmations/logs 2>/dev/null || true

echo " Backup completed"
echo
echo "Backup location: $BACKUP_DIR"
echo "Total size: $(du -sh "$BACKUP_DIR" | cut -f1)"
