from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional
import logging
import time
from io import StringIO
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

from apple_auth import AppleIdentityError, verify_apple_identity_token
from app_store import AppStoreVerificationError, verify_notification, verify_signed_transaction
from auth_service import (
    AuthConfigurationError,
    AuthContext,
    AuthError,
    BACKEND_TOKEN_TTL_SECONDS,
    authenticate_bearer_token,
    authenticate_development_api_key,
    issue_backend_token,
)
from entitlements import DatabaseConfigurationError, EntitlementError, EntitlementStore
from live_sessions import session_manager
from analysis_pipeline import analyze_pgn_to_feedback
from schemas import AppleAuthRequest, AppStorePurchaseRequest, AppStoreWebhookRequest

# Import redis for exception handling
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None
from fastapi import Form
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Configure structured logging
log_handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter('%(asctime)s %(name)s %(levelname)s %(message)s %(request_id)s')
log_handler.setFormatter(formatter)
logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(log_handler)
logger.propagate = False
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

# Environment validation
def validate_environment():
    """Validate required environment variables on startup."""
    required_vars = []
    optional_vars = [
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "ALLOWED_ORIGINS",
        "API_KEY",
        "APPSTORE_ISSUER_ID",
        "APPSTORE_KEY_ID",
        "APPSTORE_PRIVATE_KEY",
    ]

    # Production runtime needs durable auth and billing configuration.
    is_production = os.getenv("ENVIRONMENT", "development") == "production"
    if is_production:
        required_vars.extend(
            [
                "DATABASE_URL",
                "BACKEND_AUTH_SECRET",
                "APPLE_BUNDLE_ID",
                "APPLE_APPLE_ID",
                "APPSTORE_PRODUCT_ID_30_GAMES",
                "APPSTORE_ROOT_CERT_PATHS",
            ]
        )

    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        error_msg = f"Missing required environment variables: {', '.join(missing)}"
        logger.error(error_msg, extra={"request_id": "startup"})
        raise RuntimeError(error_msg)

    # Log warnings for optional but recommended vars
    for var in optional_vars:
        if not os.getenv(var):
            logger.warning(f"Optional environment variable not set: {var}", extra={"request_id": "startup"})

    logger.info("Environment validation passed", extra={"request_id": "startup"})

validate_environment()
entitlement_store = EntitlementStore()

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


def _http_500_config(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message)


def _unauthorized(detail: str = "Missing or invalid authorization header") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _payment_required(error: EntitlementError) -> HTTPException:
    return HTTPException(status_code=402, detail=error.to_payload())


def _get_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized()
    try:
        return authorization.split(" ", 1)[1].strip()
    except IndexError as exc:
        raise _unauthorized("Invalid authorization header format") from exc


def _development_context() -> AuthContext:
    user = entitlement_store.upsert_user("dev-anonymous-user", apple_email="dev@example.com")
    return AuthContext(
        user_id=user.id,
        apple_sub=user.apple_sub,
        apple_email=user.apple_email,
        is_development_override=True,
    )


def get_auth_context(authorization: Optional[str] = Header(None)) -> AuthContext:
    if not authorization:
        if not IS_PRODUCTION and not API_KEY:
            return _development_context()
        raise _unauthorized()

    token = _get_bearer_token(authorization)
    try:
        return authenticate_bearer_token(token, entitlement_store)
    except (AuthError, AuthConfigurationError):
        if not IS_PRODUCTION and API_KEY:
            try:
                return authenticate_development_api_key(token, entitlement_store)
            except AuthError:
                pass
        raise _unauthorized("Invalid bearer token")


def _load_owned_session(session_id: str, user_id: int) -> dict:
    sess = session_manager.get(session_id)
    owner_user_id = sess.get("owner_user_id")
    if owner_user_id is None and not IS_PRODUCTION:
        sess["owner_user_id"] = user_id
        session_manager.save(sess)
        return sess
    if owner_user_id != user_id:
        raise KeyError("Session not found")
    return sess


def _ensure_can_play(user_id: int):
    try:
        return entitlement_store.assert_can_play(user_id)
    except EntitlementError as exc:
        raise _payment_required(exc)


def _consume_session_game(sess: dict, user_id: int):
    if sess.get("game_charged"):
        return
    try:
        entitlement_store.consume_game(user_id, f"session:{sess['id']}", source="live_game")
    except EntitlementError as exc:
        raise _payment_required(exc)
    sess["game_charged"] = True
    sess["game_charge_event_key"] = f"session:{sess['id']}"
    session_manager.save(sess)


def _validate_pgn_payload(pgn: str) -> None:
    if len(pgn) > 100000:
        raise HTTPException(status_code=400, detail="PGN too large (max 100KB)")
    import chess.pgn

    game = chess.pgn.read_game(StringIO(pgn))
    if game is None or game.end().board().move_stack == []:
        raise HTTPException(status_code=400, detail="Invalid or empty PGN")

# Health check endpoints
@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint with Redis connectivity check."""
    checks = {
        "api": "healthy",
        "session_storage": "unknown",
        "timestamp": time.time()
    }

    # Check Redis connectivity if using Redis session manager
    from live_sessions import RedisSessionManager
    if isinstance(session_manager, RedisSessionManager):
        try:
            session_manager.redis_client.ping()
            checks["session_storage"] = "redis_ok"
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            checks["session_storage"] = "redis_unavailable"
            checks["status"] = "degraded"
            return JSONResponse(
                status_code=503,
                content=checks
            )
    else:
        checks["session_storage"] = "in_memory"

    checks["status"] = "healthy"
    return checks

@app.get("/ready", tags=["Health"])
async def readiness_check():
    """Readiness check - validates critical dependencies."""
    checks = {
        "api": "ok",
        "stockfish": "ok",
        "session_storage": "ok"
    }

    # Check if stockfish is available
    stockfish_path = os.getenv("STOCKFISH_PATH", "stockfish")
    import shutil
    if not shutil.which(stockfish_path):
        checks["stockfish"] = "unavailable"

    # Check Redis connectivity if using Redis session manager
    from live_sessions import RedisSessionManager
    if isinstance(session_manager, RedisSessionManager):
        try:
            session_manager.redis_client.ping()
            checks["session_storage"] = "redis_ok"
        except Exception as e:
            logger.error(f"Redis readiness check failed: {e}")
            checks["session_storage"] = "redis_unavailable"

    all_ok = all(v in ("ok", "redis_ok") for v in checks.values())
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

@app.post("/v1/auth/apple", tags=["Auth"])
@limiter.limit("30/minute")
async def auth_with_apple(request: Request, payload: AppleAuthRequest):
    try:
        claims = verify_apple_identity_token(payload.identity_token, payload.nonce)
        user = entitlement_store.upsert_user(
            str(claims["sub"]),
            apple_email=claims.get("email"),
        )
        return {
            "access_token": issue_backend_token(user),
            "token_type": "bearer",
            "expires_in": BACKEND_TOKEN_TTL_SECONDS,
            "entitlement": entitlement_store.get_entitlement_snapshot(user.id).to_dict(),
        }
    except AppleIdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (AuthConfigurationError, DatabaseConfigurationError) as exc:
        raise _http_500_config(str(exc)) from exc


@app.get("/v1/entitlements", tags=["Auth"])
@limiter.limit("60/minute")
async def get_entitlements(request: Request, current_user: AuthContext = Depends(get_auth_context)):
    return entitlement_store.get_entitlement_snapshot(current_user.user_id).to_dict()


@app.post("/v1/purchases/app-store", tags=["Billing"])
@limiter.limit("20/minute")
async def process_app_store_purchase(
    request: Request,
    payload: AppStorePurchaseRequest,
    current_user: AuthContext = Depends(get_auth_context),
):
    try:
        transaction = verify_signed_transaction(payload.signed_transaction_info)
        purchase = entitlement_store.apply_app_store_transaction(
            user_id=current_user.user_id,
            transaction_id=transaction.transaction_id,
            original_transaction_id=transaction.original_transaction_id,
            product_id=transaction.product_id,
            environment=transaction.environment,
            signed_transaction_info=transaction.signed_transaction_info,
            revoked=bool(transaction.revocation_date),
        )
        return {
            "transaction_id": transaction.transaction_id,
            "already_processed": purchase.already_processed,
            "games_changed": purchase.games_changed,
            "revoked": purchase.revoked,
            "entitlement": purchase.snapshot.to_dict(),
        }
    except AppStoreVerificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/webhooks/app-store", tags=["Billing"])
@limiter.limit("60/minute")
async def handle_app_store_webhook(request: Request, payload: AppStoreWebhookRequest):
    try:
        notification = verify_notification(payload.signedPayload)
    except AppStoreVerificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not notification.transaction:
        return {"status": "ignored", "reason": "missing_transaction"}

    existing = entitlement_store.get_transaction(notification.transaction.transaction_id)
    if not existing:
        logger.warning(
            "Ignoring App Store notification for unknown transaction",
            extra={"request_id": "app_store_webhook", "transaction_id": notification.transaction.transaction_id},
        )
        return {"status": "ignored", "reason": "unknown_transaction"}

    revoked = bool(notification.transaction.revocation_date) or notification.notification_type.upper() in {"REFUND", "REVOKE"}
    purchase = entitlement_store.apply_app_store_transaction(
        user_id=int(existing["user_id"]),
        transaction_id=notification.transaction.transaction_id,
        original_transaction_id=notification.transaction.original_transaction_id,
        product_id=notification.transaction.product_id,
        environment=notification.transaction.environment,
        signed_transaction_info=notification.transaction.signed_transaction_info,
        notification_type=notification.notification_type,
        revoked=revoked,
    )
    return {
        "status": "processed",
        "notification_type": notification.notification_type,
        "already_processed": purchase.already_processed,
        "games_changed": purchase.games_changed,
    }


@app.post("/v1/sessions", tags=["Sessions"])
@limiter.limit("10/minute")
async def create_session(
    request: Request,
    skill_level: str = "intermediate",
    game_mode: str = "play",
    current_user: AuthContext = Depends(get_auth_context),
):
    """Create a new chess coaching session."""
    _ensure_can_play(current_user.user_id)
    return session_manager.create(
        skill_level=skill_level,
        game_mode=game_mode,
        owner_user_id=current_user.user_id,
    )


@app.get("/v1/sessions/{session_id}", tags=["Sessions"])
@limiter.limit("30/minute")
async def get_session(
    request: Request,
    session_id: str,
    current_user: AuthContext = Depends(get_auth_context),
):
    """Get the current state of a session."""
    try:
        _load_owned_session(session_id, current_user.user_id)
        return session_manager.snapshot(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    except Exception as e:
        # Handle Redis connection errors
        if REDIS_AVAILABLE and redis and isinstance(e, (redis.RedisError, redis.ConnectionError)):
            logger.error(f"Redis error in get_session: {e}", extra={"request_id": getattr(request.state, "request_id", None)})
            raise HTTPException(status_code=503, detail="Session storage temporarily unavailable")
        # Re-raise other exceptions
        logger.error(f"Unexpected error in get_session: {e}", extra={"request_id": getattr(request.state, "request_id", None)})
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/v1/sessions/{session_id}/move", tags=["Sessions"])
@limiter.limit("60/minute")
async def play_move(
    request: Request,
    session_id: str,
    move: str,
    current_user: AuthContext = Depends(get_auth_context),
):
    """Submit a move and get coaching feedback."""
    try:
        sess = _load_owned_session(session_id, current_user.user_id)
        board = sess["board"]
        parsed_move, _, _ = session_manager._parse_move(board, move)
        if parsed_move is None or parsed_move not in board.legal_moves:
            raise HTTPException(status_code=400, detail="Illegal move")
        _consume_session_game(sess, current_user.user_id)
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
    except HTTPException:
        # Re-raise HTTP exceptions (like 400 for illegal moves)
        raise
    except Exception as e:
        # Handle Redis connection errors
        if REDIS_AVAILABLE and redis and isinstance(e, (redis.RedisError, redis.ConnectionError)):
            logger.error(f"Redis error in play_move: {e}", extra={"request_id": getattr(request.state, "request_id", None)})
            raise HTTPException(status_code=503, detail="Session storage temporarily unavailable")
        # Re-raise other exceptions
        logger.error(f"Unexpected error in play_move: {e}", extra={"request_id": getattr(request.state, "request_id", None)})
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/v1/sessions/{session_id}/stream", tags=["Sessions"])
@limiter.limit("60/minute")
async def stream_move(
    request: Request,
    session_id: str,
    move: str,
    current_user: AuthContext = Depends(get_auth_context),
):
    """SSE stream: emits a quick 'basic' event."""
    try:
        sess = _load_owned_session(session_id, current_user.user_id)
        board = sess["board"]
        m, san, uci = session_manager._parse_move(board, move)
        if m is None or m not in board.legal_moves:
            raise HTTPException(status_code=400, detail="Illegal move")
        _consume_session_game(sess, current_user.user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except Exception as e:
        if REDIS_AVAILABLE and redis and isinstance(e, (redis.RedisError, redis.ConnectionError)):
            logger.error(f"Redis error in stream_move setup: {e}")
            raise HTTPException(status_code=503, detail="Session storage temporarily unavailable")
        logger.error(f"Unexpected error in stream_move setup: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

    async def event_gen():
        # Compute using the same internal pipeline but split into two phases
        from stockfish_engine import StockfishAnalyzer, DEFAULT_MULTIPV
        from llm_coach import rule_basic, coach_move_with_llm, severity_from_cp_loss
        import chess

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

        # Phase 2: full analysis for extended
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

        level = sess.get("skill_level", "intermediate")
        coach = await coach_move_with_llm(full_payload, level=level)

        full_payload.update(
            {
                "basic": coach.get("basic"),
                "extended": coach.get("extended"),
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

        session_manager.save(sess)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/v1/runs", tags=["Analysis"])
@limiter.limit("5/minute")
async def run_batch_analysis(
    request: Request,
    pgn: str = Form(...),
    level: str = Form("intermediate"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: AuthContext = Depends(get_auth_context),
):
    """Run batch analysis on a complete PGN game."""
    _validate_pgn_payload(pgn)

    event_key = f"run:{current_user.user_id}:{idempotency_key}" if idempotency_key else f"run:{request.state.request_id}"
    try:
        entitlement_store.consume_game(current_user.user_id, event_key, source="batch_run")
    except EntitlementError as exc:
        raise _payment_required(exc)

    summary = await analyze_pgn_to_feedback(pgn, level=level)
    if not summary:
        raise HTTPException(status_code=400, detail="Invalid or empty PGN")
    return summary
