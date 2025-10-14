"""Gunicorn configuration for production deployment."""
import os
import multiprocessing

# Server socket
bind = "unix:/opt/llm-chess-coach/llm-chess-coach.sock"
umask = 0o007
backlog = 2048

# Worker processes
workers = int(os.getenv("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50
timeout = 120
graceful_timeout = 30
keepalive = 5

# Process naming
proc_name = "llm-chess-coach"

# Logging
accesslog = "/opt/llm-chess-coach/logs/access.log"
errorlog = "/opt/llm-chess-coach/logs/error.log"
loglevel = os.getenv("LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Server mechanics
daemon = False
pidfile = "/opt/llm-chess-coach/gunicorn.pid"
user = "chesscoach"
group = "chesscoach"
tmp_upload_dir = None

# SSL (if terminating SSL at gunicorn instead of nginx)
keyfile = None
certfile = None

def on_starting(server):
    """Called just before the master process is initialized."""
    server.log.info("Starting LLM Chess Coach application")

def on_reload(server):
    """Called to recycle workers during a reload via SIGHUP."""
    server.log.info("Reloading LLM Chess Coach application")

def worker_int(worker):
    """Called when a worker receives the SIGINT or SIGQUIT signal."""
    worker.log.info("Worker received INT or QUIT signal")

def worker_abort(worker):
    """Called when a worker receives the SIGABRT signal."""
    worker.log.info("Worker received ABORT signal")
