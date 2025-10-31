# LLM-ChessCoach

## Introduction
LLM-ChessCoach is an innovative tool that leverages Large Language Models (LLM), specifically GPT-5-Nano, along with the Stockfish engine to analyze chess games. It fetches games from online chess platforms, analyzes them, and provides insightful feedback to help players improve their strategies. 

## Features
- **Game Import**: Downloads games from online chess platforms.
- **Advanced Analysis**: Uses GPT-5-Nano and Stockfish to provide detailed game analyses.

## Components
1. `api_server.py`: FastAPI server providing REST endpoints (mobile-first MVP).
2. `stockfish_engine.py`: Engine wrapper with MultiPV and mover-perspective loss.
3. `live_sessions.py`: In-memory live sessions (play vs engine) with SSE streaming.
4. `analysis_pipeline.py`: Batch PGN analysis to MoveFeedback + summary.
5. `llm_coach.py`: LLM-backed coaching with rule-based fallback.
6. `schemas.py`: Pydantic models for API responses.
7. `export_lichess_games.py`: Lichess fetcher (reads token from env var).
8. `legacy/`: Previous Streamlit and React UI kept for reference.

## Installation
1. Clone the repository.
2. Install dependencies: `pip install -r requirements.txt`.
3. Install the Stockfish engine (`apt install stockfish` on Ubuntu).
4. Set up your Lichess API token in `config.json`.

## Usage
### API (mobile-first)
Start the API server: `uvicorn api_server:app --reload`.

Auth: set `API_KEY` in environment and include `Authorization: Bearer <API_KEY>` in requests.

Key endpoints:
- `POST /v1/sessions?skill_level=intermediate` → `{session_id, fen_start}`
- `POST /v1/sessions/{id}/move?move=e4` → per-move feedback (basic+extended)
- `GET /v1/sessions/{id}/stream?move=e4` (SSE) → `basic` then `extended` events
- `POST /v1/runs` (body: `pgn`) → full game feedback and summary

### Legacy UIs
Streamlit and the previous React demo are available under `legacy/`.

### LLM Model
- The backend uses OpenAI models for extended coaching. By default it targets `gpt-5-nano`.
- You can override with `OPENAI_MODEL` (e.g., `gpt-5`, `gpt-5-pro`), but models older than GPT‑5 are ignored in favor of `gpt-5-nano` to preserve chess understanding quality.

### LunaNetEngine Sample Workflow
Fetch a small sample of PGNs from Lichess for the `LunaNetEngine` account and run both a short per-move sample and full-game batch analysis. Results are written to `samples/luna/analysis/`.

1) Fetch games (optionally set `LICHESS_API_TOKEN` for higher limits):

```
python3 scripts/fetch_luna_games.py --username LunaNetEngine --max_games 5 --output_dir samples/luna/raw
```

2) Run analysis on the latest fetched file (uses Stockfish + ChatGPT if `OPENAI_API_KEY` is set):

```
python3 scripts/run_luna_analysis.py --raw_dir samples/luna/raw --out_dir samples/luna/analysis --level expert --sample_moves 12
```

3) Review outputs:
- `samples/luna/analysis/full_*.json` and `.txt`: complete per-move feedback + summary
- `samples/luna/analysis/sample_*.json` and `.txt`: first N moves for a quick spot check

## Deployment

### Heroku Deployment (Cloud Platform)

Deploy LLM-ChessCoach to Heroku with automated buildpacks, environment configuration, and scalable dynos.

#### Prerequisites

- [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli) installed
- Heroku account
- OpenAI API key or OpenRouter account

#### One-Click Deploy

[![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy)

Click the button above to deploy instantly with pre-configured settings.

#### Manual Deployment

1. **Create Heroku App**:
   ```bash
   heroku create your-app-name
   ```

2. **Add Buildpacks** (for Stockfish installation):
   ```bash
   heroku buildpacks:add --index 1 https://github.com/heroku/heroku-buildpack-apt
   heroku buildpacks:add --index 2 heroku/python
   ```

3. **Configure Environment Variables**:
   ```bash
   # Required: Generate secure API key
   heroku config:set API_KEY=$(openssl rand -hex 32)

   # Required: OpenAI or OpenRouter API key
   heroku config:set OPENAI_API_KEY=your-api-key-here

   # Recommended: Use cost-effective model via OpenRouter
   heroku config:set OPENAI_MODEL=google/gemini-2.5-flash-lite
   heroku config:set OPENAI_API_ENDPOINT=https://openrouter.ai/api/v1

   # Required: Stockfish path (installed via Aptfile)
   heroku config:set STOCKFISH_PATH=engines/stockfish

   # Production settings
   heroku config:set ENVIRONMENT=production
   heroku config:set LOG_LEVEL=INFO

   # CORS (update with your frontend domain)
   heroku config:set ALLOWED_ORIGINS=https://your-frontend-domain.com

   # Performance tuning (adjust based on dyno tier)
   heroku config:set MULTIPV=3
   heroku config:set NODES_PER_PV=250000
   heroku config:set GUNICORN_WORKERS=4
   ```

4. **Optional: Add Redis for Caching** (improves performance):
   ```bash
   heroku addons:create heroku-redis:mini
   ```

5. **Deploy to Heroku**:
   ```bash
   git push heroku master
   ```

6. **Verify Deployment**:
   ```bash
   # Check application logs
   heroku logs --tail

   # Test health endpoint
   curl https://your-app-name.herokuapp.com/health

   # Test readiness (validates Stockfish)
   curl https://your-app-name.herokuapp.com/ready
   ```

#### Dyno Recommendations

- **Basic ($7/month)**:
  - 512MB RAM
  - Sleeps after 30 min inactivity
  - Set `MULTIPV=2`, `NODES_PER_PV=50000`, `GUNICORN_WORKERS=2`


#### GitHub Integration (Auto-Deploy)

1. Connect your Heroku app to GitHub repository
2. Enable automatic deploys from master branch
3. Optional: Enable "Wait for CI to pass" if you have tests configured

#### Monitoring & Logs

```bash
# View real-time logs
heroku logs --tail

# View logs from specific dyno
heroku logs --tail --dyno web.1

# Add Papertrail for better log management
heroku addons:create papertrail:choklad
```

#### Scaling

```bash
# Scale web dynos
heroku ps:scale web=2

# Change dyno type
heroku ps:type web=standard-2x
```

#### Troubleshooting

**Issue: "Stockfish not found"**
- Solution: Verify buildpacks are in correct order (apt first, then python)
- Check: `heroku buildpacks` should show apt at index 1

**Issue: "Memory exceeded"**
- Solution: Reduce `NODES_PER_PV` or `MULTIPV` settings
- Or: Upgrade to larger dyno tier

**Issue: "H12 Request timeout"**
- Solution: Long analysis may timeout. Consider reducing analysis depth or upgrading dyno

For more details, see [Heroku Documentation](https://devcenter.heroku.com/).

---

### Ubuntu VPS Deployment (Production)

For production deployment to an Ubuntu VPS (OVHCloud, DigitalOcean, Linode, etc.), see the comprehensive [DEPLOYMENT.md](DEPLOYMENT.md) guide.

#### Quick Start

1. **Automated Setup**:
   ```bash
   sudo bash scripts/setup_ubuntu_vps.sh
   ```

2. **Configure Environment**:
   ```bash
   cp .env.example .env
   nano .env  # Set API_KEY, OPENAI_API_KEY, ALLOWED_ORIGINS
   ```

3. **Set Up SSL** (with domain):
   ```bash
   sudo bash scripts/setup_ssl.sh yourdomain.com your-email@example.com
   ```

4. **Harden Security**:
   ```bash
   sudo bash scripts/harden_server.sh
   ```

5. **Start Application**:
   ```bash
   sudo systemctl start llm-chess-coach.service
   sudo systemctl enable llm-chess-coach.service
   ```

The application includes:
- ✅ Automated Ubuntu VPS setup
- ✅ Nginx reverse proxy with SSL/TLS
- ✅ Systemd service management
- ✅ Security hardening (fail2ban, firewall, SSH hardening)
- ✅ Rate limiting and CORS protection
- ✅ Structured logging and monitoring
- ✅ Automatic backups and log rotation

For detailed instructions, troubleshooting, and security best practices, see [DEPLOYMENT.md](DEPLOYMENT.md).

## Contributing
Contributions are welcome. Please read the contributing guidelines first.

## License
This project is licensed under the MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgements
- Chess websites for game data.
- OpenAI's GPT-5-Nano for game analysis.


