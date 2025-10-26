# Heroku Deployment Guide for LLM-ChessCoach

Complete guide for deploying LLM-ChessCoach to Heroku with best practices, performance optimization, and troubleshooting.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Deployment Methods](#deployment-methods)
- [Configuration](#configuration)
- [Performance Tuning](#performance-tuning)
- [Monitoring & Logging](#monitoring--logging)
- [Cost Optimization](#cost-optimization)
- [Troubleshooting](#troubleshooting)
- [Security Best Practices](#security-best-practices)

## Overview

LLM-ChessCoach runs on Heroku as a Python application using:
- **Buildpacks**: heroku-buildpack-apt (for Stockfish) + Python
- **Process Type**: Web dynos running Gunicorn with Uvicorn workers
- **Dependencies**: Stockfish chess engine, FastAPI, OpenAI SDK
- **Optional Add-ons**: Redis (caching), Papertrail (logging)

## Prerequisites

1. **Heroku Account**: Sign up at [heroku.com](https://www.heroku.com)
2. **Heroku CLI**: Install from [devcenter.heroku.com/articles/heroku-cli](https://devcenter.heroku.com/articles/heroku-cli)
3. **Git**: Ensure your code is in a Git repository
4. **API Keys**:
   - OpenAI API key OR OpenRouter account
   - (Recommended) Use OpenRouter for cost-effective access to multiple models

## Deployment Methods

### Method 1: One-Click Deploy (Easiest)

[![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy)

1. Click the "Deploy to Heroku" button
2. Fill in required environment variables
3. Click "Deploy app"
4. Wait 5-10 minutes for initial build

### Method 2: Heroku CLI (Recommended)

```bash
# 1. Login to Heroku
heroku login

# 2. Create new app
heroku create your-app-name

# 3. Add buildpacks (ORDER MATTERS!)
heroku buildpacks:add --index 1 https://github.com/heroku/heroku-buildpack-apt
heroku buildpacks:add --index 2 heroku/python

# 4. Configure environment variables (see Configuration section)
heroku config:set API_KEY=$(openssl rand -hex 32)
heroku config:set OPENAI_API_KEY=your-key-here
# ... (see full list below)

# 5. Deploy
git push heroku master

# 6. Verify
heroku logs --tail
heroku open /health
```

### Method 3: GitHub Integration (Auto-Deploy)

1. Go to your Heroku dashboard → your app → Deploy tab
2. Connect to GitHub and select your repository
3. Enable "Automatic Deploys" from master branch
4. Optional: Enable "Wait for CI to pass before deploy"

### Method 4: Container Deployment (Advanced)

For users who prefer Docker:

```bash
# 1. Set stack to container
heroku stack:set container

# 2. Deploy using heroku.yml and Dockerfile
git push heroku master

# Note: This uses Dockerfile and heroku.yml instead of buildpacks
```

## Configuration

### Required Environment Variables

```bash
# Authentication
heroku config:set API_KEY=$(openssl rand -hex 32)

# LLM Configuration
heroku config:set OPENAI_API_KEY=your-api-key-here
heroku config:set OPENAI_MODEL=google/gemini-2.5-flash-lite
heroku config:set OPENAI_API_ENDPOINT=https://openrouter.ai/api/v1

# Chess Engine
heroku config:set STOCKFISH_PATH=engines/stockfish

# Application Settings
heroku config:set ENVIRONMENT=production
heroku config:set LOG_LEVEL=INFO
```

### Optional Environment Variables

```bash
# CORS Configuration
heroku config:set ALLOWED_ORIGINS=https://your-frontend.com,https://www.your-frontend.com

# Performance Tuning (adjust based on dyno tier - see next section)
heroku config:set MULTIPV=3
heroku config:set NODES_PER_PV=250000
heroku config:set GUNICORN_WORKERS=4

# Lichess Integration (optional)
heroku config:set LICHESS_API_TOKEN=your-lichess-token
```

## Performance Tuning

### Dyno Tier Recommendations

#### Basic ($7/month) - Development Only
```bash
heroku ps:type web=basic
heroku config:set GUNICORN_WORKERS=2
heroku config:set MULTIPV=2
heroku config:set NODES_PER_PV=50000
```

**Characteristics**:
- 512MB RAM
- Sleeps after 30 min inactivity
- ~2-5 seconds per move analysis
- Suitable for testing only

#### Standard-1x ($25/month) - Production (Recommended)
```bash
heroku ps:type web=standard-1x
heroku config:set GUNICORN_WORKERS=4
heroku config:set MULTIPV=3
heroku config:set NODES_PER_PV=250000
```

**Characteristics**:
- 512MB RAM
- No sleeping
- ~1-3 seconds per move analysis
- Good for moderate traffic (< 100 req/min)

#### Standard-2x ($50/month) - High Traffic
```bash
heroku ps:type web=standard-2x
heroku config:set GUNICORN_WORKERS=8
heroku config:set MULTIPV=5
heroku config:set NODES_PER_PV=500000
```

**Characteristics**:
- 1GB RAM
- Better concurrent request handling
- ~0.5-2 seconds per move analysis
- Good for high traffic (< 500 req/min)

#### Performance-M ($250/month) - Heavy Workloads
```bash
heroku ps:type web=performance-m
heroku config:set GUNICORN_WORKERS=12
heroku config:set MULTIPV=5
heroku config:set NODES_PER_PV=1000000
```

**Characteristics**:
- 2.5GB RAM
- Maximum analysis quality
- ~0.3-1 second per move analysis
- Handles very high traffic

### Worker Configuration Formula

```python
# Optimal workers = (2 × CPU cores) + 1
# Heroku dynos have different CPU allocations:
# Basic/Standard-1x: ~1-2 cores → 2-4 workers
# Standard-2x: ~2-4 cores → 4-8 workers
# Performance-M: ~4-8 cores → 8-12 workers
```

### Memory Management

Monitor memory usage:
```bash
heroku ps -a your-app-name
heroku logs --tail | grep "Memory"
```

If you see memory errors (R14, R15):
1. Reduce `NODES_PER_PV` by 50%
2. Reduce `MULTIPV` by 1-2
3. Reduce `GUNICORN_WORKERS` by 2-4
4. Or upgrade dyno tier

## Add-ons

### Redis (Caching) - Highly Recommended

Improves performance by caching engine analysis and LLM responses.

```bash
# Add Redis
heroku addons:create heroku-redis:mini

# Verify
heroku addons:info heroku-redis
heroku config | grep REDIS_URL
```

**Benefits**:
- 60-80% faster repeated position analysis
- Reduces OpenAI API costs
- Handles session state across dyno restarts

**Note**: Requires code changes to implement caching (not yet implemented by default)

### Papertrail (Logging) - Recommended

Better log management and search.

```bash
# Add Papertrail
heroku addons:create papertrail:choklad

# View logs
heroku addons:open papertrail
```

**Benefits**:
- 7-day log retention (vs 1 day on Heroku)
- Full-text search
- Real-time log tailing
- Alerts and notifications

### PostgreSQL (Database) - Optional

For persistent storage of game analyses and user data.

```bash
# Add PostgreSQL
heroku addons:create heroku-postgresql:mini

# Verify
heroku pg:info
```

**Note**: Requires code changes to implement database storage (not needed for basic operation)

## Monitoring & Logging

### View Logs

```bash
# Real-time logs
heroku logs --tail

# Last 100 lines
heroku logs -n 100

# Filter by source
heroku logs --source app --tail

# Filter by dyno
heroku logs --dyno web.1 --tail
```

### Key Metrics to Monitor

```bash
# Dyno status
heroku ps

# Application metrics (requires Heroku dashboard)
# - Response time (target: < 2s for analysis endpoints)
# - Memory usage (target: < 80% of dyno limit)
# - Error rate (target: < 1%)
# - Throughput (requests per minute)
```

### Health Checks

```bash
# Basic health check
curl https://your-app-name.herokuapp.com/health

# Readiness check (validates Stockfish availability)
curl https://your-app-name.herokuapp.com/ready

# Expected response:
# {"status": "ready", "checks": {"api": "ok", "stockfish": "ok"}}
```

## Cost Optimization

### LLM API Costs

Use cost-effective models via OpenRouter:

```bash
# Gemini Flash (cheapest, good quality)
heroku config:set OPENAI_MODEL=google/gemini-2.5-flash-lite
heroku config:set OPENAI_API_ENDPOINT=https://openrouter.ai/api/v1

# Pricing comparison (approximate):
# - GPT-4: $30/1M tokens
# - GPT-3.5: $1/1M tokens
# - Gemini Flash: $0.075/1M tokens (40x cheaper than GPT-4!)
```

### Reduce Compute Costs

1. **Use Basic dyno for development**: $7/month vs $25/month
2. **Scale down during off-hours**: `heroku ps:scale web=0` (free tier)
3. **Use review apps only when needed**: Configure in app.json
4. **Monitor and optimize slow queries**: Use Stockfish limits wisely

### Free Tier (Eco Dynos)

Heroku offers $5/month eco dynos (1000 hours shared across apps):

```bash
heroku ps:type web=eco
```

**Limitations**:
- Sleeps after 30 min inactivity
- Slower cold starts (~10-30 seconds)
- Not suitable for production

## Troubleshooting

### Issue: "Application Error" or H10 Error

**Symptom**: App crashes immediately after deploy

**Solutions**:
```bash
# Check logs for exact error
heroku logs --tail

# Common causes:
# 1. Missing environment variables
heroku config

# 2. Buildpack order wrong
heroku buildpacks
# Should show: 1. apt, 2. python

# 3. Port binding issue
# Ensure gunicorn binds to $PORT (auto-configured in gunicorn_config.py)
```

### Issue: "Stockfish not found" or R99 Error

**Symptom**: `/ready` endpoint shows `"stockfish": "unavailable"`

**Solutions**:
```bash
# 1. Verify apt buildpack is first
heroku buildpacks:clear
heroku buildpacks:add --index 1 https://github.com/heroku/heroku-buildpack-apt
heroku buildpacks:add --index 2 heroku/python

# 2. Verify Aptfile exists and contains "stockfish"
cat Aptfile

# 3. Verify STOCKFISH_PATH is set correctly
heroku config:get STOCKFISH_PATH
# Should be: /usr/games/stockfish

# 4. Trigger rebuild
git commit --allow-empty -m "Rebuild for Stockfish"
git push heroku master
```

### Issue: H12 Request Timeout

**Symptom**: Requests timeout after 30 seconds

**Solutions**:
```bash
# 1. Reduce analysis depth
heroku config:set NODES_PER_PV=100000
heroku config:set MULTIPV=2

# 2. Upgrade dyno for faster processing
heroku ps:type web=standard-2x

# 3. For batch analysis, consider background jobs (requires code changes)
```

### Issue: R14 Memory Quota Exceeded

**Symptom**: Dyno runs out of memory, may crash

**Solutions**:
```bash
# 1. Reduce workers
heroku config:set GUNICORN_WORKERS=2

# 2. Reduce analysis settings
heroku config:set NODES_PER_PV=100000
heroku config:set MULTIPV=2

# 3. Upgrade dyno tier
heroku ps:type web=standard-2x

# 4. Monitor memory usage
heroku ps -a your-app-name
```

### Issue: Slow Performance

**Symptom**: Requests take 5+ seconds

**Solutions**:
```bash
# 1. Check dyno isn't sleeping (upgrade from Basic to Standard)
heroku ps:type web=standard-1x

# 2. Add Redis for caching (requires code changes)
heroku addons:create heroku-redis:mini

# 3. Optimize Stockfish settings
heroku config:set NODES_PER_PV=150000

# 4. Use faster LLM model
heroku config:set OPENAI_MODEL=google/gemini-2.5-flash-lite
```

### Issue: High OpenAI API Costs

**Solutions**:
```bash
# 1. Switch to cheaper model
heroku config:set OPENAI_MODEL=google/gemini-2.5-flash-lite
heroku config:set OPENAI_API_ENDPOINT=https://openrouter.ai/api/v1

# 2. Implement caching (requires code changes)

# 3. Use rule-based coaching for simple positions (already implemented)
```

## Security Best Practices

### 1. Secure API Key

```bash
# Generate strong random key
heroku config:set API_KEY=$(openssl rand -hex 32)

# Rotate regularly (every 90 days)
heroku config:set API_KEY=$(openssl rand -hex 32)
```

### 2. CORS Configuration

```bash
# Don't use wildcards in production
heroku config:set ALLOWED_ORIGINS=https://your-frontend.com

# Multiple origins
heroku config:set ALLOWED_ORIGINS=https://your-frontend.com,https://www.your-frontend.com
```

### 3. Environment Variables

```bash
# NEVER commit secrets to Git
# Always use Heroku config vars

# View all config
heroku config

# Remove sensitive config
heroku config:unset UNUSED_VAR
```

### 4. HTTPS Only

Heroku automatically provides HTTPS. Ensure your app enforces it:

```bash
# Set production environment
heroku config:set ENVIRONMENT=production

# The app automatically:
# - Disables Swagger docs in production
# - Enforces HTTPS redirects
# - Sets secure headers
```

### 5. Rate Limiting

Already implemented via `slowapi`:
- 10/minute for session creation
- 60/minute for move submission
- 5/minute for batch analysis

Monitor for abuse:
```bash
heroku logs --tail | grep "rate limit"
```

## Scaling

### Horizontal Scaling (More Dynos)

```bash
# Scale to 2 dynos
heroku ps:scale web=2

# Scale to 5 dynos
heroku ps:scale web=5

# Scale down to 1
heroku ps:scale web=1

# Cost: $25/month × number of Standard-1x dynos
```

**When to scale horizontally**:
- High request volume (> 100 req/min per dyno)
- Need high availability
- CPU-bound workloads

### Vertical Scaling (Bigger Dynos)

```bash
# Upgrade to Standard-2x
heroku ps:type web=standard-2x

# Upgrade to Performance-M
heroku ps:type web=performance-m
```

**When to scale vertically**:
- Memory pressure (R14 errors)
- Slow individual requests
- Need more CPU per request

### Autoscaling (Requires Add-on)

Consider [Adept Scale](https://elements.heroku.com/addons/adept-scale) or [HireFire](https://elements.heroku.com/addons/hirefire) for automatic scaling based on load.

## Backup and Disaster Recovery

### Configuration Backup

```bash
# Export all config vars
heroku config -s > heroku_config_backup.txt

# Restore from backup
cat heroku_config_backup.txt | xargs heroku config:set
```

### Database Backup (if using PostgreSQL)

```bash
# Create backup
heroku pg:backups:capture

# List backups
heroku pg:backups

# Download backup
heroku pg:backups:download
```

## CI/CD Integration

### GitHub Actions Example

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Heroku

on:
  push:
    branches: [master]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: akhileshns/heroku-deploy@v3.12.12
        with:
          heroku_api_key: ${{secrets.HEROKU_API_KEY}}
          heroku_app_name: "your-app-name"
          heroku_email: "your-email@example.com"
```

## Support and Resources

- **Heroku Documentation**: https://devcenter.heroku.com/
- **LLM-ChessCoach Issues**: https://github.com/yourusername/LLM-ChessCoach/issues
- **Heroku Support**: https://help.heroku.com/
- **OpenRouter Documentation**: https://openrouter.ai/docs

## Conclusion

Your LLM-ChessCoach application is now deployed to Heroku!

**Next Steps**:
1. Test all API endpoints
2. Set up monitoring (Papertrail, metrics)
3. Configure your frontend to use the Heroku API URL
4. Monitor costs and optimize as needed
5. Set up CI/CD for automated deployments

For VPS deployment instead of Heroku, see [DEPLOYMENT.md](DEPLOYMENT.md).
