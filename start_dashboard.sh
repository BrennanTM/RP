#!/bin/bash
cd /home/tristan8/stanford_redcap/dashboard
source /home/tristan8/stanford_redcap/venv/bin/activate
exec streamlit run dashboard.py --server.port 8080 --server.address 0.0.0.0
