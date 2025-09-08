from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from typing import Optional
try:
    from export_lichess_games import ChessGameDownloader
except Exception:  # pragma: no cover - optional legacy dependency for v1 tests
    ChessGameDownloader = None
import os
import json
import uuid
import asyncio

from live_sessions import session_manager
from analysis_pipeline import analyze_pgn_to_feedback
from fastapi import Form

app = FastAPI(title="LLM Chess Coach API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

downloader = ChessGameDownloader() if ChessGameDownloader else None


# --- Simple Auth (MVP): Bearer token ---
API_KEY = os.getenv("API_KEY")


def require_auth(authorization: Optional[str] = None):
    if not API_KEY:
        # If no API key configured, allow for local dev
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != API_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    return

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

@app.post("/v1/sessions")
async def create_session(skill_level: str = "intermediate", authorization: Optional[str] = Header(None)):
    require_auth(authorization)
    return session_manager.create(skill_level=skill_level)


@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str, authorization: Optional[str] = Header(None)):
    require_auth(authorization)
    try:
        return session_manager.snapshot(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.post("/v1/sessions/{session_id}/move")
async def play_move(session_id: str, move: str, authorization: Optional[str] = Header(None)):
    require_auth(authorization)
    try:
        result = session_manager.apply_move(session_id, move)
        if not result.get("legal"):
            raise HTTPException(status_code=400, detail=result.get("error", "Illegal move"))
        return result
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.get("/v1/sessions/{session_id}/stream")
async def stream_move(session_id: str, move: str, authorization: Optional[str] = Header(None)):
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

        level = sess.get("skill_level", "intermediate")
        coach = coach_move_with_llm(full_payload, level=level)
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

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/v1/runs")
async def run_batch_analysis(
    pgn: str = Form(...),
    level: str = Form("intermediate"),
    authorization: Optional[str] = Header(None),
):
    require_auth(authorization)
    summary = analyze_pgn_to_feedback(pgn, level=level)
    if not summary:
        raise HTTPException(status_code=400, detail="Invalid or empty PGN")
    return summary
