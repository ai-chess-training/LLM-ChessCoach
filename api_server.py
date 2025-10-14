from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional
import logging
import time
from pythonjsonlogger import jsonlogger

try:
    from export_lichess_games import ChessGameDownloader
except Exception:  # pragma: no cover - optional legacy dependency for v1 tests
    ChessGameDownloader = None
import os
import json
import uuid
import asyncio

from env_loader import load_env

load_env()

from live_sessions import session_manager
from analysis_pipeline import analyze_pgn_to_feedback
from fastapi import Form
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Configure structured logging
log_handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter('%(asctime)s %(name)s %(levelname)s %(message)s %(request_id)s')
log_handler.setFormatter(formatter)
logger = logging.getLogger(__name__)
logger.addHandler(log_handler)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

# Environment validation
def validate_environment():
    """Validate required environment variables on startup."""
    required_vars = []
    optional_vars = ["OPENAI_API_KEY", "OPENAI_MODEL", "ALLOWED_ORIGINS"]

    # In production, API_KEY is required
    is_production = os.getenv("ENVIRONMENT", "development") == "production"
    if is_production:
        required_vars.append("API_KEY")

    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        error_msg = f"Missing required environment variables: {', '.join(missing)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    # Log warnings for optional but recommended vars
    for var in optional_vars:
        if not os.getenv(var):
            logger.warning(f"Optional environment variable not set: {var}")

    logger.info("Environment validation passed", extra={"request_id": "startup"})

validate_environment()

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="LLM Chess Coach API",
    version="1.0.0",
    docs_url="/docs" if os.getenv("ENVIRONMENT") != "production" else None,
    redoc_url="/redoc" if os.getenv("ENVIRONMENT") != "production" else None
)

# Add rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS with restricted origins
allowed_origins_str = os.getenv("ALLOWED_ORIGINS", "*")
allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",")] if allowed_origins_str != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    max_age=600,
)

# Security headers middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Add request ID for tracing
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Process request with timing
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time

        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = str(process_time)

        # Log request
        logger.info(
            f"{request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "process_time": process_time,
                "client_host": request.client.host if request.client else None,
            }
        )

        return response

app.add_middleware(SecurityHeadersMiddleware)

downloader = ChessGameDownloader() if ChessGameDownloader else None

# --- Bearer Token Authentication ---
API_KEY = os.getenv("API_KEY")
IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"

def require_auth(authorization: Optional[str] = None):
    """Require bearer token authentication."""
    if not API_KEY:
        if IS_PRODUCTION:
            logger.error("API_KEY not configured in production", extra={"request_id": "auth"})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server configuration error"
            )
        # Allow in development if no key configured
        return

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"}
        )

    try:
        token = authorization.split(" ", 1)[1].strip()
    except IndexError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
            headers={"WWW-Authenticate": "Bearer"}
        )

    if token != API_KEY:
        logger.warning("Invalid API key attempt", extra={"request_id": "auth"})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key"
        )
    return

# Health check endpoints
@app.get("/health", tags=["Health"])
async def health_check():
    """Basic health check endpoint."""
    return {"status": "healthy", "timestamp": time.time()}

@app.get("/ready", tags=["Health"])
async def readiness_check():
    """Readiness check - validates critical dependencies."""
    checks = {
        "api": "ok",
        "stockfish": "ok",
    }

    # Check if stockfish is available
    stockfish_path = os.getenv("STOCKFISH_PATH", "stockfish")
    import shutil
    if not shutil.which(stockfish_path):
        checks["stockfish"] = "unavailable"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if all_ok else "not_ready", "checks": checks}
    )

@app.post("/api/analyze")
async def analyze(date: str):
    if not downloader:
        return {"error": "Downloader unavailable"}
    epoch_time = downloader.date_text_to_epoch(date)
    folder = downloader.fetch_and_save_games(epoch_time)
    username = downloader.config['lichess_user_name']
    downloader.run_analysis(username=username)
    run_id = os.path.basename(folder)
    return {"run_id": run_id}

@app.get("/api/analysis/{run_id}")
async def get_analysis(run_id: str):
    base_dir = os.path.abspath('games')
    analysis_root = os.path.normpath(os.path.join(base_dir, run_id, 'analysis'))
    result = {}
    if not analysis_root.startswith(base_dir):
        # Prevent path traversal
        return {}
    if os.path.exists(analysis_root):
        for file in os.listdir(analysis_root):
            file_path = os.path.normpath(os.path.join(analysis_root, file))
            if not file_path.startswith(analysis_root):
                continue
            with open(file_path) as f:
                result[file] = f.read()
    return result

SCHEDULE_FILE = 'schedules.json'

def load_schedules():
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE) as f:
            return json.load(f)
    return []

def save_schedules(data):
    with open(SCHEDULE_FILE, 'w') as f:
        json.dump(data, f)

@app.post("/api/schedule")
async def add_schedule(date: str, frequency: str):
    schedules = load_schedules()
    schedules.append({'date': date, 'frequency': frequency, 'id': str(uuid.uuid4())})
    save_schedules(schedules)
    return {"status": "scheduled"}

@app.get("/api/dashboard/{username}")
async def dashboard(username: str):
    # Placeholder summary
    schedules = load_schedules()
    return {"username": username, "scheduled_jobs": schedules}


# --------------------
# v1 Mobile-first API
# --------------------

@app.post("/v1/sessions", tags=["Sessions"])
@limiter.limit("10/minute")
async def create_session(
    request: Request,
    skill_level: str = "intermediate",
    game_mode: str = "play",
    authorization: Optional[str] = Header(None)
):
    """Create a new chess coaching session."""
    require_auth(authorization)
    return session_manager.create(skill_level=skill_level, game_mode=game_mode)


@app.get("/v1/sessions/{session_id}", tags=["Sessions"])
@limiter.limit("30/minute")
async def get_session(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(None)
):
    """Get the current state of a session."""
    require_auth(authorization)
    try:
        return session_manager.snapshot(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.post("/v1/sessions/{session_id}/move", tags=["Sessions"])
@limiter.limit("60/minute")
async def play_move(
    request: Request,
    session_id: str,
    move: str,
    authorization: Optional[str] = Header(None)
):
    """Submit a move and get coaching feedback."""
    require_auth(authorization)
    try:
        result = await session_manager.apply_move(session_id, move)
        if not result.get("legal"):
            raise HTTPException(status_code=400, detail=result.get("error", "Illegal move"))

        # Return structured response with human feedback and engine move (if applicable)
        response = {
            "legal": True,
            "human_feedback": result.get("human_feedback"),
            "engine_move": result.get("engine_move")
        }
        return response
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.get("/v1/sessions/{session_id}/stream", tags=["Sessions"])
@limiter.limit("60/minute")
async def stream_move(
    request: Request,
    session_id: str,
    move: str,
    authorization: Optional[str] = Header(None)
):
    """SSE stream: emits a quick 'basic' event, then a full 'extended' event."""
    require_auth(authorization)

    async def event_gen():
        # Compute using the same internal pipeline but split into two phases
        from stockfish_engine import StockfishAnalyzer, DEFAULT_MULTIPV
        from llm_coach import rule_basic, coach_move_with_llm, severity_from_cp_loss
        import chess

        try:
            sess = session_manager.get(session_id)
        except KeyError:
            yield f"event: error\ndata: {json.dumps({'error':'Session not found'})}\n\n"
            return

        board: chess.Board = sess["board"]
        # Parse move
        m, san, uci = session_manager._parse_move(board, move)
        if m is None or m not in board.legal_moves:
            yield f"event: error\ndata: {json.dumps({'error':'Illegal move'})}\n\n"
            return

        fen_before = board.fen()
        side = "white" if board.turn else "black"

        # Phase 1: quick analysis for basic comment
        with StockfishAnalyzer(multipv=DEFAULT_MULTIPV, nodes_per_pv=50_000) as analyzer:
            eval_before = analyzer.analyze_position(board)
            comparison = analyzer.compare_move(board, m)

        # Build basic feedback object
        before_cp_white = eval_before.get("score", {}).get("cp")
        after_cp_white = comparison.get("eval_after", {}).get("score", {}).get("cp")
        mover_is_white = (side == "white")
        cp_before = before_cp_white if mover_is_white else (-before_cp_white if before_cp_white is not None else None)
        cp_after = after_cp_white if mover_is_white else (-after_cp_white if after_cp_white is not None else None)
        cp_loss = comparison.get("eval_loss", 0.0)
        best_move_san = eval_before.get("best_move_san")
        multipv = eval_before.get("pv", [])

        basic_payload = {
            "move_no": len(sess["moves"]) + 1,
            "side": side,
            "san": san,
            "uci": uci,
            "fen_before": fen_before,
            "cp_before": cp_before,
            "cp_after": cp_after,
            "cp_loss": cp_loss,
            "severity": severity_from_cp_loss(cp_loss),
            "best_move_san": best_move_san,
            "multipv": multipv,
        }
        basic_text = rule_basic(basic_payload)
        yield f"event: basic\ndata: {json.dumps({'basic': basic_text, 'preview': basic_payload})}\n\n"

        # Phase 2: full analysis for extended + drills
        from llm_coach import make_drills, rule_extended
        with StockfishAnalyzer(multipv=DEFAULT_MULTIPV) as analyzer:
            # recompute with full budget (~1M per PV configured in analyzer)
            eval_before_full = analyzer.analyze_position(board)
            comparison_full = analyzer.compare_move(board, m)

        # Push the move in session now
        board.push(m)
        fen_after = board.fen()

        before_cp_white = eval_before_full.get("score", {}).get("cp")
        after_cp_white = comparison_full.get("eval_after", {}).get("score", {}).get("cp")
        cp_before = before_cp_white if mover_is_white else (-before_cp_white if before_cp_white is not None else None)
        cp_after = after_cp_white if mover_is_white else (-after_cp_white if after_cp_white is not None else None)
        cp_loss = comparison_full.get("eval_loss", 0.0)
        best_move_san = eval_before_full.get("best_move_san")
        multipv = eval_before_full.get("pv", [])

        full_payload = {
            "move_no": len(sess["moves"]) + 1,
            "side": side,
            "san": san,
            "uci": uci,
            "fen_before": fen_before,
            "fen_after": fen_after,
            "cp_before": cp_before,
            "cp_after": cp_after,
            "cp_loss": cp_loss,
            "severity": severity_from_cp_loss(cp_loss),
            "best_move_san": best_move_san,
            "multipv": multipv,
        }

        print(full_payload)

        level = sess.get("skill_level", "intermediate")
        coach = await coach_move_with_llm(full_payload, level=level)

        print(coach)

        full_payload.update(
            {
                "basic": coach.get("basic"),
                "extended": coach.get("extended"),
                "tags": coach.get("tags", []),
                "drills": coach.get("drills", []),
            }
        )

        # Save to session moves
        sess["moves"].append(full_payload)

        yield f"event: extended\ndata: {json.dumps(full_payload)}\n\n"

        # Get engine move if in play mode
        if sess.get("game_mode") == "play" and not board.is_game_over():
            from stockfish_engine import SKILL_LEVEL_MAPPINGS
            skill_config = SKILL_LEVEL_MAPPINGS.get(sess.get("skill_level", "intermediate"))

            with StockfishAnalyzer(skill_level=skill_config["skill_level"]) as analyzer:
                engine_response = analyzer.get_engine_move(board, time_limit_ms=skill_config["move_time_ms"])

            if engine_response.get("move_uci"):
                # Apply engine move
                engine_move = chess.Move.from_uci(engine_response["move_uci"])
                board.push(engine_move)

                engine_payload = {
                    "san": engine_response.get("move_san"),
                    "uci": engine_response.get("move_uci"),
                    "fen_after": board.fen(),
                    "score": engine_response.get("score", {}),
                    "skill_level": skill_config["skill_level"]
                }

                # Store engine move in session
                engine_feedback = {
                    "move_no": len(sess["moves"]),
                    "side": "white" if board.turn == chess.BLACK else "black",
                    "san": engine_response.get("move_san"),
                    "uci": engine_response.get("move_uci"),
                    "fen_after": board.fen(),
                    "is_engine_move": True
                }
                sess["moves"].append(engine_feedback)

                yield f"event: engine_move\ndata: {json.dumps(engine_payload)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/v1/runs", tags=["Analysis"])
@limiter.limit("5/minute")
async def run_batch_analysis(
    request: Request,
    pgn: str = Form(...),
    level: str = Form("intermediate"),
    authorization: Optional[str] = Header(None),
):
    """Run batch analysis on a complete PGN game."""
    require_auth(authorization)

    # Validate PGN size
    if len(pgn) > 100000:  # ~100KB limit
        raise HTTPException(status_code=400, detail="PGN too large (max 100KB)")

    summary = await analyze_pgn_to_feedback(pgn, level=level)
    if not summary:
        raise HTTPException(status_code=400, detail="Invalid or empty PGN")
    return summary
