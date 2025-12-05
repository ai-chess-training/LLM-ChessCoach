# Interactive Chess Gameplay Feature

This document describes the new interactive chess gameplay feature that allows users to play against Stockfish at different skill levels while receiving real-time coaching feedback.

## Overview

The system now supports two game modes:
- **Play Mode**: Interactive gameplay against Stockfish with coaching feedback
- **Training Mode**: Analysis-only mode for reviewing moves without engine responses

## Skill Levels

The system supports four skill levels, each mapped to specific Stockfish configurations:

| Level         | Stockfish Skill | Move Time (ms) | Description                  |
|---------------|-----------------|----------------|------------------------------|
| `beginner`    | 3               | 150            | Suitable for new players     |
| `intermediate`| 8               | 250            | For developing players       |
| `advanced`    | 13              | 400            | For strong club players      |
| `expert`      | 18              | 800            | Near maximum strength        |

## API Usage

### Create a Game Session

```http
POST /v1/sessions
```

Parameters:
- `skill_level`: One of `beginner`, `intermediate`, `advanced`, `expert`
- `game_mode`: Either `play` (interactive) or `training` (analysis only)

Response:
```json
{
  "session_id": "uuid",
  "fen_start": "starting position",
  "game_mode": "play",
  "skill_level": "intermediate"
}
```

### Make a Move

```http
POST /v1/sessions/{session_id}/move
```

Parameters:
- `move`: Move in UCI or SAN notation (e.g., "e4", "Nf3", "e2e4")

Response (Play Mode):
```json
{
  "legal": true,
  "human_feedback": {
    "move_no": 1,
    "san": "e4",
    "severity": "best",
    "cp_loss": 0.05,
    "basic": "Solid move. Keep building your plan.",
    "multipv": [...]
  },
  "engine_move": {
    "san": "e5",
    "uci": "e7e5",
    "fen_after": "...",
    "score": {"cp": 10}
  }
}
```

### Stream Move with SSE

```http
GET /v1/sessions/{session_id}/stream?move={move}
```

This endpoint streams the analysis in real-time:
1. `basic` event: Quick feedback on the human move
3. `engine_move` event: Stockfish's response (if in play mode)

## Implementation Details

### Key Components

1. **StockfishAnalyzer** (`stockfish_engine.py`)
   - Added `skill_level` parameter for configuration
   - New `get_engine_move()` method for generating moves at specified skill

2. **SessionManager** (`live_sessions.py`)
   - Tracks game mode and engine configuration
   - Manages two-phase move processing (human + engine)

3. **API Server** (`api_server.py`)
   - Updated endpoints to support game modes
   - Enhanced SSE streaming for engine moves

4. **Schemas** (`schemas.py`)
   - New models: `EngineMove`, `MoveResponse`, `SessionInfo`
   - Support for game mode and skill level types

## Example Usage

### Python Client Example

```python
import requests

# Create a beginner-level game
response = requests.post(
    "http://localhost:8000/v1/sessions",
    params={"skill_level": "beginner", "game_mode": "play"}
)
session = response.json()

# Make a move
response = requests.post(
    f"http://localhost:8000/v1/sessions/{session['session_id']}/move",
    params={"move": "e4"}
)
result = response.json()

# Human feedback
print(f"You played: {result['human_feedback']['san']}")
print(f"Coach says: {result['human_feedback']['basic']}")

# Engine response
if result['engine_move']:
    print(f"Engine plays: {result['engine_move']['san']}")
```

### Running Tests

```bash
# Run the test suite
python3 test_interactive_play.py

# Run the interactive example
python3 example_interactive_play.py
```

## Benefits

1. **Adaptive Difficulty**: Players can choose appropriate skill levels
2. **Real-time Coaching**: Get immediate feedback on move quality
3. **Interactive Learning**: Learn by playing against an adjustable opponent
4. **Mobile-Friendly**: Optimized for mobile API consumption

## Future Enhancements

- Time controls and clock management
- Opening book integration
- Endgame tablebase support
- Player rating estimation
- Personalized coaching based on play history