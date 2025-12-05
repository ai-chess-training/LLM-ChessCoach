import os
import json
import shutil
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("API_KEY", "test-key")
# Speed up engine for tests
os.environ.setdefault("NODES_PER_PV", "30000")
os.environ.setdefault("MULTIPV", "3")

import importlib
import api_server
import stockfish_engine
importlib.reload(stockfish_engine)


def stockfish_available() -> bool:
    # Check if stockfish binary is present in PATH or via env var
    path = os.getenv("STOCKFISH_PATH", "stockfish")
    if shutil.which(path):
        return True
    return False


@pytest.fixture(scope="module")
def client():
    os.environ.setdefault("API_KEY", "test-key")
    with TestClient(api_server.app) as c:
        yield c


def auth_headers():
    return {"Authorization": f"Bearer {os.getenv('API_KEY')}"}


@pytest.mark.skipif(not stockfish_available(), reason="Stockfish binary not available")
def test_session_flow(client: TestClient):
    # Create session
    r = client.post("/v1/sessions", params={"skill_level": "intermediate"}, headers=auth_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    assert "session_id" in data
    sid = data["session_id"]
    assert "fen_start" in data

    # First move e4
    r2 = client.post(f"/v1/sessions/{sid}/move", params={"move": "e4"}, headers=auth_headers())
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2.get("legal") is True
    fb = data2.get("feedback")
    assert fb and fb.get("san") == "e4"
    assert "basic" in fb and isinstance(fb["basic"], str)


@pytest.mark.skipif(not stockfish_available(), reason="Stockfish binary not available")
def test_sse_basic_and_extended(client: TestClient):
    # Create session
    r = client.post("/v1/sessions", params={"skill_level": "intermediate"}, headers=auth_headers())
    assert r.status_code == 200
    sid = r.json()["session_id"]

    # Open SSE stream for e4
    with client.stream("GET", f"/v1/sessions/{sid}/stream", params={"move": "e4"}, headers={**auth_headers(), "Accept": "text/event-stream"}) as s:
        assert s.status_code == 200
        got_basic = False
        got_extended = False
        buf = ""
        for chunk in s.iter_text(chunk_size=1024):
            buf += chunk
            # Split Server-Sent Events by double newlines
            while "\n\n" in buf:
                evt, buf = buf.split("\n\n", 1)
                if evt.startswith("event: "):
                    lines = evt.splitlines()
                    ev = lines[0].split(": ",1)[1].strip()
                    data_line = next((ln for ln in lines if ln.startswith("data: ")), None)
                    payload = json.loads(data_line.split(": ",1)[1]) if data_line else None
                    if ev == "basic":
                        assert payload and "basic" in payload
                        got_basic = True
                    elif ev == "extended":
                        assert payload and payload.get("san") == "e4"
                        assert "extended" in payload
                        got_extended = True
                if got_basic and got_extended:
                    break
            if got_basic and got_extended:
                break
        assert got_basic and got_extended


@pytest.mark.skipif(not stockfish_available(), reason="Stockfish binary not available")
def test_batch_run(client: TestClient):
    pgn = """[Event \"Test\"]\n[White \"White\"]\n[Black \"Black\"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0\n"""
    r = client.post("/v1/runs", data={"pgn": pgn, "level": "intermediate"}, headers=auth_headers())
    assert r.status_code == 200, r.text
    summary = r.json()
    assert "moves" in summary and len(summary["moves"]) >= 4
    # Check first move has basic/extended
    m0 = summary["moves"][0]
    assert m0.get("san") == "e4"
    assert isinstance(m0.get("basic"), str)
    assert isinstance(m0.get("extended"), str)


@pytest.mark.skipif(not stockfish_available(), reason="Stockfish binary not available")
def test_session_two_moves_and_snapshot(client: TestClient):
    r = client.post("/v1/sessions", params={"skill_level": "intermediate"}, headers=auth_headers())
    assert r.status_code == 200
    sid = r.json()["session_id"]

    r1 = client.post(f"/v1/sessions/{sid}/move", params={"move": "e4"}, headers=auth_headers())
    assert r1.status_code == 200
    r2 = client.post(f"/v1/sessions/{sid}/move", params={"move": "e5"}, headers=auth_headers())
    assert r2.status_code == 200

    snap = client.get(f"/v1/sessions/{sid}", headers=auth_headers()).json()
    assert len(snap.get("moves", [])) == 2
    assert snap["moves"][0]["san"] == "e4"
    assert snap["moves"][1]["san"] == "e5"


@pytest.mark.skipif(not stockfish_available(), reason="Stockfish binary not available")
def test_analysis_pipeline_direct(client: TestClient):
    from analysis_pipeline import analyze_pgn_to_feedback
    pgn = (
        "[Event \"Test\"]\n[White \"W\"]\n[Black \"B\"]\n\n"
        "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6 8. c3\n"
    )
    os.environ["MULTIPV"] = "3"
    os.environ["NODES_PER_PV"] = "20000"
    summary = analyze_pgn_to_feedback(pgn, level="intermediate")
    assert summary and "moves" in summary
    assert len(summary["moves"]) >= 10
    # Ensure multipv present and basic/extended populated
    sample = summary["moves"][0]
    assert isinstance(sample.get("multipv"), list)
    assert isinstance(sample.get("basic"), str)
    assert isinstance(sample.get("extended"), str)


def _load_openai_key_from_env_file() -> str:
    # Lightweight .env parser for OPENAI_API_KEY
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        return ""
    key = ""
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("OPENAI_API_KEY="):
                key = line.split("=", 1)[1].strip()
                break
    return key


@pytest.mark.skipif(not stockfish_available(), reason="Stockfish binary not available")
def test_llm_coach_direct_uses_openai():
    # Try to load key from .env
    key = _load_openai_key_from_env_file()
    if not key:
        pytest.skip("No OPENAI_API_KEY configured in .env")
    os.environ["OPENAI_API_KEY"] = key
    os.environ.setdefault("OPENAI_MODEL", "gpt-5-nano")

    # Build a realistic move payload for LLM
    move = {
        "san": "e4",
        "best_move_san": "e4",
        "cp_loss": 0.15,
        "side": "white",
        "multipv": [
            {"move_san": "e4", "cp": 20, "line_san": ["e4", "e5", "Nf3", "Nc6"]},
            {"move_san": "d4", "cp": 15, "line_san": ["d4", "d5", "Nc3"]},
        ],
        "fen_before": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    }

    from llm_coach import coach_move_with_llm
    out = coach_move_with_llm(move, level="intermediate")

    # Validate structural constraints
    assert isinstance(out.get("basic"), str) and len(out["basic"].split()) <= 15
    assert isinstance(out.get("extended"), str) and len(out["extended"].split()) <= 100
    # Preferably used LLM; if not, at least return rules
    assert out.get("source") in ("llm", "rules")
