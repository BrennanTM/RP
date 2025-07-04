# Stanford REDCap Automation System

Comprehensive automation system for Stanford Precision Neurotherapeutics Lab REDCap operations.

## Components

- **Dashboard**: Web-based monitoring and management interface
- **Scheduler**: In-house appointment scheduling system
- **Eligibility**: Automated eligibility email processing
- **Confirmations**: Appointment confirmation system
- **Tracker**: Calendly/appointment synchronization

## Quick Start

```bash
# Start all services
./scripts/start_all_services.sh

# Monitor status
./scripts/monitor_services.sh

# Stop all services
./scripts/stop_all_services.sh
```

## Access Points

- Dashboard: http://171.64.52.112:8080
- Scheduler: http://171.64.52.112:8081

## Directory Structure

```
stanford_redcap/
 common/          # Shared modules
 dashboard/       # Web dashboard
 scheduler/       # Appointment scheduler
 confirmations/   # Email confirmations
 tracker/         # Appointment tracker
 scripts/         # Management scripts
 data/           # Data storage
 docs/           # Documentation
```

## Configuration

Edit `.env` file with your credentials and settings.

## Support

See `docs/` directory for detailed documentation.
