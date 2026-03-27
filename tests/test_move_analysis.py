"""Tests for the shared move_analysis module."""

from move_analysis import (
    severity_from_cp_loss,
    compute_mover_perspective_cp,
    build_multipv_entries,
    build_move_feedback,
)


def test_severity_thresholds():
    assert severity_from_cp_loss(0.0) == "best"
    assert severity_from_cp_loss(0.10) == "best"
    assert severity_from_cp_loss(0.15) == "best"
    assert severity_from_cp_loss(0.20) == "good"
    assert severity_from_cp_loss(0.30) == "good"
    assert severity_from_cp_loss(0.50) == "inaccuracy"
    assert severity_from_cp_loss(0.60) == "inaccuracy"
    assert severity_from_cp_loss(1.00) == "mistake"
    assert severity_from_cp_loss(1.50) == "mistake"
    assert severity_from_cp_loss(2.00) == "blunder"
    # Negative values (abs is taken)
    assert severity_from_cp_loss(-0.10) == "best"
    assert severity_from_cp_loss(-2.00) == "blunder"


def test_compute_mover_perspective_cp_white():
    cp_b, cp_a = compute_mover_perspective_cp(100, 80, mover_is_white=True)
    assert cp_b == 100
    assert cp_a == 80


def test_compute_mover_perspective_cp_black():
    cp_b, cp_a = compute_mover_perspective_cp(100, 80, mover_is_white=False)
    assert cp_b == -100
    assert cp_a == -80


def test_compute_mover_perspective_cp_none():
    cp_b, cp_a = compute_mover_perspective_cp(None, 80, mover_is_white=True)
    assert cp_b is None
    assert cp_a is None


def test_build_multipv_entries_truncates():
    raw = [
        {"move_san": "e4", "move_uci": "e2e4", "cp": 30, "mate": None, "line_san": list(range(20))},
    ]
    entries = build_multipv_entries(raw, max_line_length=10)
    assert len(entries) == 1
    assert len(entries[0]["line_san"]) == 10


def test_build_move_feedback_structure():
    eval_before = {"score": {"cp": 50}, "best_move_san": "Nf3", "pv": []}
    comparison = {"eval_after": {"score": {"cp": 40}}, "eval_loss": 0.10}
    fb = build_move_feedback(
        move_no=1, side="white", san="e4", uci="e2e4",
        fen_before="start", fen_after="after",
        eval_before=eval_before, comparison=comparison,
        mover_is_white=True,
    )
    assert fb["severity"] == "best"
    assert fb["cp_before"] == 50
    assert fb["cp_after"] == 40
    assert fb["best_move_san"] == "Nf3"
