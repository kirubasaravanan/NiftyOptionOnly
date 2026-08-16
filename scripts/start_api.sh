#!/bin/bash
# Robust supervisor: restart FastAPI on crash
cd /home/z/my-project
ulimit -n 65536
while true; do
    echo "[$(date)] Starting FastAPI on port 8000..."
    PYTHONUNBUFFERED=1 python -u -m nifty_engine.api 2>&1
    EXIT_CODE=$?
    echo "[$(date)] Process exited (code=$EXIT_CODE), restarting in 2s..."
    sleep 2
done
