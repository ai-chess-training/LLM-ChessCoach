from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field


Severity = Literal["best", "good", "inaccuracy", "mistake", "blunder"]
GameMode = Literal["play", "training"]
SkillLevel = Literal["beginner", "intermediate", "advanced", "expert"]


class MultiPVEntry(BaseModel):
    move_san: Optional[str] = None
    move_uci: Optional[str] = None
    cp: Optional[int] = None
    mate: Optional[int] = None
    line_san: List[str] = Field(default_factory=list)


class Drill(BaseModel):
    fen: str
    side_to_move: Literal["white", "black"]
    objective: str
    best_line_san: List[str] = Field(default_factory=list)
    alt_traps_san: List[str] = Field(default_factory=list)


class MoveFeedback(BaseModel):
    move_no: int
    side: Literal["white", "black"]
    san: str
    uci: Optional[str] = None
    fen_before: str
    fen_after: Optional[str] = None

    # Engine evaluation
    cp_before: Optional[int] = None  # from mover perspective
    cp_after: Optional[int] = None   # from mover perspective
    cp_loss: Optional[float] = None  # in pawns (positive is worse for mover)
    severity: Severity = "good"
    best_move_san: Optional[str] = None
    multipv: List[MultiPVEntry] = Field(default_factory=list)

    # Coaching
    basic: Optional[str] = None   # <=15 words
    extended: Optional[str] = None  # <=100 words
    tags: List[str] = Field(default_factory=list)
    drills: List[Drill] = Field(default_factory=list)


class EngineMove(BaseModel):
    san: str
    uci: str
    fen_after: str
    score: Dict[str, Any] = Field(default_factory=dict)
    skill_level: Optional[int] = None


class MoveResponse(BaseModel):
    legal: bool
    human_feedback: Optional[MoveFeedback] = None
    engine_move: Optional[EngineMove] = None
    error: Optional[str] = None


class SessionInfo(BaseModel):
    session_id: str
    skill_level: SkillLevel
    game_mode: GameMode
    fen: str
    moves: List[MoveFeedback] = Field(default_factory=list)
    is_game_over: bool = False
    turn: Literal["white", "black"]


class GameSummary(BaseModel):
    moves: List[MoveFeedback] = Field(default_factory=list)
    acpl_white: Optional[float] = None
    acpl_black: Optional[float] = None
    best_move_rate_white: Optional[float] = None
    best_move_rate_black: Optional[float] = None
    mistakes_white: Optional[int] = None
    mistakes_black: Optional[int] = None
    blunders_white: Optional[int] = None
    blunders_black: Optional[int] = None
    openings: List[str] = Field(default_factory=list)
    critical_positions: List[int] = Field(default_factory=list)

