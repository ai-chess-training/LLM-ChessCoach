# Heroku Process Configuration for LLM-ChessCoach
# This file defines how Heroku should run the application

# Web process: Run Gunicorn with Uvicorn workers
# - Binds to $PORT (Heroku's dynamic port)
# - Uses configuration from gunicorn_config.py
# - Worker count is configured via GUNICORN_WORKERS env var (default: 2x CPU + 1)
web: gunicorn api_server:app --bind 0.0.0.0:$PORT --config gunicorn_config.py

# Release process: Run before new release is deployed
# Currently validates environment and dependencies
release: python -c "from api_server import validate_environment; validate_environment()"
