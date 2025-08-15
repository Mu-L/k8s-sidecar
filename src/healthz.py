from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from datetime import datetime, timedelta, timezone
import logging
import threading
import uvicorn
import os
from logger import get_log_config
from typing import List
from multiprocessing import Process

# Create FastAPI app
app = FastAPI()

# Create a logging filter to exclude health check endpoint logs
class HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Filter out logs for the /healthz endpoint to reduce noise
        return record.getMessage().find("/healthz") == -1

# Health state variables
is_ready = False
last_k8s_contact = datetime.now(timezone.utc)
watcher_processes: List[Process] = []

# Settings
K8S_CONTACT_THRESHOLD_SECONDS = 60  # tolerated delay before declaring not live

@app.get("/healthz")
def healthz():
    """
    Health endpoint for readiness and liveness probes.
    """
    now = datetime.now(timezone.utc)

    # Check readiness
    if not is_ready:
        return PlainTextResponse("NOT READY", status_code=503)

    # Check liveness
    if (now - last_k8s_contact) > timedelta(seconds=K8S_CONTACT_THRESHOLD_SECONDS):
        return PlainTextResponse("NOT LIVE (K8s contact lost)", status_code=503)
 
    # Check liveness of watcher processes
    if watcher_processes and not all(p.is_alive() for p in watcher_processes):
        return PlainTextResponse("NOT LIVE (watcher process died)", status_code=503)
 
    return PlainTextResponse("OK", status_code=200)

# Public helper functions

def mark_ready():
    """
    Mark the sidecar as ready (initial sync done).
    """
    global is_ready
    is_ready = True

def update_k8s_contact():
    """
    Update the timestamp of the last successful Kubernetes contact.
    """
    global last_k8s_contact
    last_k8s_contact = datetime.now(timezone.utc)

def register_watcher_processes(processes: List[Process]):
    """
    Register the list of watcher processes to be monitored for liveness.
    """
    global watcher_processes
    watcher_processes = processes

def start_health_server():
    """
    Start the FastAPI health server in a background thread.
    """
    def run():
        log_config = get_log_config()
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()

        # Define the filter in the config to be callable
        log_config.setdefault('filters', {})
        log_config['filters']['health_check_filter'] = {
            '()': 'healthz.HealthCheckFilter'
        }

        # Add uvicorn loggers to the existing config and have them propagate to the root logger
        log_config.setdefault('loggers', {})
        log_config['loggers']['uvicorn'] = {'level': log_level, 'propagate': True}
        log_config['loggers']['uvicorn.error'] = {'level': log_level, 'propagate': True}
        # Add a filter to the access logger to exclude /healthz requests
        log_config['loggers']['uvicorn.access'] = {'level': log_level, 'propagate': True, 'filters': ['health_check_filter']}

        health_port = int(os.getenv("HEALTH_PORT", "8080"))
        uvicorn.run(app, host="0.0.0.0", port=health_port, log_config=log_config)

    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()
