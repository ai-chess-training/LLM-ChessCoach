# LLM-ChessCoach API Documentation

## Overview

The LLM-ChessCoach API provides endpoints for chess game analysis, live coaching sessions, and batch processing. The API is divided into two main sections:
- **Legacy API** (`/api/*`): Original endpoints for game analysis and scheduling
- **v1 Mobile-first API** (`/v1/*`): Modern, mobile-optimized endpoints with SSE streaming support

## Authentication

The v1 API endpoints require Bearer token authentication:
```
Authorization: Bearer <API_KEY>
```

If no `API_KEY` environment variable is configured, authentication is bypassed for local development.

## API Endpoints

### Legacy Endpoints

#### POST `/api/analyze`
Analyzes games from a specific date.

**Request Body:**
```json
{
  "date": "string"  // Date in text format
}
```

**Response:**
```json
{
  "run_id": "string"  // Unique identifier for the analysis run
}
```

**Error Response:**
```json
{
  "error": "Downloader unavailable"
}
```

---

#### GET `/api/analysis/{run_id}`
Retrieves analysis results for a specific run.

**Path Parameters:**
- `run_id`: The unique identifier returned from `/api/analyze`

**Response:**
```json
{
  "<filename>": "string"  // File content for each analysis file
}
```

---

#### POST `/api/schedule`
Schedules recurring analysis jobs.

**Request Body:**
```json
{
  "date": "string",      // Starting date
  "frequency": "string"  // Frequency of analysis
}
```

**Response:**
```json
{
  "status": "scheduled"
}
```

---

#### GET `/api/dashboard/{username}`
Gets user dashboard information including scheduled jobs.

**Path Parameters:**
- `username`: User's username

**Response:**
```json
{
  "username": "string",
  "scheduled_jobs": [
    {
      "date": "string",
      "frequency": "string",
      "id": "string"  // UUID
    }
  ]
}
```

---

### v1 Mobile-first API

#### POST `/v1/sessions`
Creates a new live coaching session with optional interactive gameplay.

**Headers:**
- `Authorization`: Bearer token (required if API_KEY is configured)

**Request Body:**
```json
{
  "skill_level": "intermediate",  // Options: "beginner", "intermediate", "advanced", "expert"
  "game_mode": "play"             // Options: "play" (interactive), "training" (analysis only)
}
```

**Response:**
```json
{
  "session_id": "string",    // UUID for the session
  "fen_start": "string",     // Starting position in FEN notation
  "game_mode": "play",       // Game mode setting
  "skill_level": "string"    // Selected skill level
}
```

**Skill Level Mappings:**
| Level | Stockfish Skill | Move Time (ms) | Description |
|-------|----------------|----------------|-------------|
| `beginner` | 3 | 150 | Suitable for new players |
| `intermediate` | 8 | 250 | For developing players |
| `advanced` | 13 | 400 | For strong club players |
| `expert` | 18 | 800 | Near maximum strength |

---

#### GET `/v1/sessions/{session_id}`
Retrieves current session state and move history.

**Headers:**
- `Authorization`: Bearer token (required)

**Path Parameters:**
- `session_id`: Session UUID

**Response:**
```json
{
  "session_id": "string",
  "skill_level": "string",
  "game_mode": "play",      // Game mode: "play" or "training"
  "fen": "string",          // Current board position in FEN
  "is_game_over": false,    // Whether the game has ended
  "turn": "white",          // Current turn: "white" or "black"
  "moves": [                // Array of MoveFeedback objects (see schema below)
    {
      "move_no": 1,
      "side": "white",
      "san": "e4",
      "uci": "e2e4",
      "fen_before": "string",
      "fen_after": "string",
      "cp_before": 20,
      "cp_after": 25,
      "cp_loss": 0.0,
      "severity": "good",
      "best_move_san": "e4",
      "multipv": [...],
      "basic": "Good opening move",
      "extended": "The King's pawn opening...",
      "is_engine_move": false  // Indicates if this was an engine move
    }
  ]
}
```

**Error Response (404):**
```json
{
  "detail": "Session not found"
}
```

---

#### POST `/v1/sessions/{session_id}/move`
Submits a move for analysis in a session. In "play" mode, also returns the engine's response move.

**Headers:**
- `Authorization`: Bearer token (required)

**Path Parameters:**
- `session_id`: Session UUID

**Request Body:**
```json
{
  "move": "string"  // Move in UCI or SAN notation (e.g., "e2e4" or "e4")
}
```

**Response (Success - Play Mode):**
```json
{
  "legal": true,
  "human_feedback": {          // Analysis of the human move
    "move_no": 1,
    "side": "white" | "black",
    "san": "string",
    "uci": "string",
    "fen_before": "string",
    "fen_after": "string",
    "cp_before": null | number,     // Centipawns from mover's perspective
    "cp_after": null | number,      // Centipawns from mover's perspective
    "cp_loss": number,              // Evaluation loss in pawns (positive = worse)
    "severity": "best" | "good" | "inaccuracy" | "mistake" | "blunder",
    "best_move_san": "string",
    "multipv": [                    // Top 5 engine variations
      {
        "move_san": "string",
        "move_uci": "string",
        "cp": number,
        "mate": null | number,
        "line_san": ["string"]       // Continuation moves (up to 10)
      }
    ],
    "basic": "string",               // Brief feedback (≤15 words)
    "extended": "string",            // Detailed explanation (≤100 words)
    "source": "llm" | "rules"        // Coaching source
  },
  "engine_move": {              // Engine's response (only in "play" mode)
    "san": "Nf3",
    "uci": "g1f3",
    "fen_after": "string",      // Position after engine move
    "score": {                  // Engine's evaluation
      "cp": 25,                 // Centipawns
      "mate": null              // Mate in N (if applicable)
    }
  }
}
```

**Response (Success - Training Mode):**
```json
{
  "legal": true,
  "human_feedback": { ... },    // Same as above
  "engine_move": null           // No engine move in training mode
}
```

**Response (Illegal Move):**
```json
{
  "legal": false,
  "error": "Illegal move"
}
```

**Error Response (404):**
```json
{
  "detail": "Session not found"
}
```

---

#### GET `/v1/sessions/{session_id}/stream?move={move}`
Server-Sent Events (SSE) endpoint for streaming move analysis.

**Headers:**
- `Authorization`: Bearer token (required)

**Path Parameters:**
- `session_id`: Session UUID

**Query Parameters:**
- `move`: Move in UCI or SAN notation

**SSE Event Stream:**

1. **Basic Event** (sent first, ~300ms):
```
event: basic
data: {
  "basic": "Good move!",
  "preview": {
    "move_no": 1,
    "side": "white",
    "san": "e4",
    "uci": "e2e4",
    "fen_before": "string",
    "cp_before": 20,
    "cp_after": 25,
    "cp_loss": 0.0,
    "severity": "good",
    "best_move_san": "e4",
    "multipv": [...]
  }
}
```

2. **Extended Event** (sent second, ~2s):
```
event: extended
data: {
  "move_no": 1,
  "side": "white",
  "san": "e4",
  "uci": "e2e4",
  "fen_before": "string",
  "fen_after": "string",
  "cp_before": 20,
  "cp_after": 25,
  "cp_loss": 0.0,
  "severity": "good",
  "best_move_san": "e4",
  "multipv": [...],
  "basic": "Good opening move",
  "extended": "The King's pawn opening controls the center...",
}
```

3. **Engine Move Event** (sent third in "play" mode):
```
event: engine_move
data: {
  "san": "e5",
  "uci": "e7e5",
  "fen_after": "string",
  "score": {
    "cp": 20,
    "mate": null
  },
  "skill_level": 8
}
```

4. **Error Event** (if applicable):
```
event: error
data: {"error": "Session not found" | "Illegal move"}
```

---

#### POST `/v1/runs`
Batch analysis of complete games via PGN.

**Headers:**
- `Authorization`: Bearer token (required)

**Request Body (Form Data):**
- `pgn`: PGN string of the game to analyze (required)
- `level`: Skill level for coaching ("beginner" | "intermediate" | "advanced"), default: "intermediate"

**Response:**
```json
{
  "moves": [
    {
      "move_no": 1,
      "side": "white" | "black",
      "san": "string",
      "uci": "string",
      "fen_before": "string",
      "fen_after": "string",
      "cp_before": null | number,
      "cp_after": null | number,
      "cp_loss": number,
      "severity": "best" | "good" | "inaccuracy" | "mistake" | "blunder",
      "best_move_san": "string",
      "multipv": [...],
      "basic": "string",
      "extended": "string",
      "source": "llm" | "rules"
    }
  ],
  "acpl_white": number,              // Average centipawn loss (in pawns)
  "acpl_black": number,
  "best_move_rate_white": number,    // Percentage of best/good moves
  "best_move_rate_black": number,
  "mistakes_white": number,          // Count of mistakes
  "mistakes_black": number,
  "blunders_white": number,          // Count of blunders
  "blunders_black": number,
  "openings": ["string"],            // Opening names
  "critical_positions": [number]     // Move numbers with mistakes/blunders
}
```

**Error Response (400):**
```json
{
  "detail": "Invalid or empty PGN"
}
```

---

## Data Schemas

### MoveFeedback
Complete feedback for a single move:
```typescript
{
  move_no: number;                   // Move number in the game
  side: "white" | "black";          // Side making the move
  san: string;                       // Standard Algebraic Notation
  uci?: string;                      // Universal Chess Interface notation
  fen_before: string;                // Position before move (FEN)
  fen_after?: string;                // Position after move (FEN)

  // Engine evaluation
  cp_before?: number;                // Centipawns before (mover perspective)
  cp_after?: number;                 // Centipawns after (mover perspective)
  cp_loss?: number;                  // Evaluation loss in pawns
  severity: Severity;                // Move classification
  best_move_san?: string;            // Engine's best move
  multipv: MultiPVEntry[];           // Top engine variations

  // Coaching
  basic?: string;                    // Brief feedback (≤15 words)
  extended?: string;                 // Detailed explanation (≤100 words)
}
```

### MultiPVEntry
Engine variation details:
```typescript
{
  move_san?: string;                 // Move in SAN
  move_uci?: string;                 // Move in UCI
  cp?: number;                       // Centipawn evaluation
  mate?: number;                     // Mate in N moves
  line_san: string[];                // Continuation line
}
```

### Drill
Practice position for improvement:
```typescript
{
  fen: string;                       // Position in FEN
  side_to_move: "white" | "black";  // Side to play
  objective: string;                 // Goal description
  best_line_san: string[];          // Solution moves
  alt_traps_san: string[];          // Alternative lines
}
```

### Severity Levels
- `best`: Perfect engine move
- `good`: Strong move (cp_loss < 0.5 pawns)
- `inaccuracy`: Minor error (0.5 ≤ cp_loss < 1.0 pawns)
- `mistake`: Significant error (1.0 ≤ cp_loss < 3.0 pawns)
- `blunder`: Critical error (cp_loss ≥ 3.0 pawns)

## Performance Targets

- **Basic feedback**: ≤300ms response time
- **Extended feedback**: ≤2s response time
- **SSE streaming**: Basic event within 300ms, extended within 2s
- **Batch analysis**: Depends on game length, typically <10s for 40-move game

## Engine Configuration

- **MultiPV**: 5 variations (configurable via `MULTIPV` env var)
- **Nodes per variation**: ~1,000,000 in production (configurable via `NODES_PER_PV`)
- **Quick analysis**: 50,000 nodes for basic SSE feedback
- **Full analysis**: 1,000,000 nodes for extended feedback

## CORS Configuration

The API allows all origins, methods, and headers for development. In production, configure appropriate CORS restrictions.

## Error Handling

All endpoints return standard HTTP status codes:
- **200**: Success
- **400**: Bad request (invalid input)
- **401**: Unauthorized (missing token)
- **403**: Forbidden (invalid token)
- **404**: Resource not found
- **500**: Internal server error

Error responses include a `detail` field with error description.

## Interactive Gameplay Feature

The API supports interactive chess gameplay against Stockfish at configurable skill levels. This feature enables:

### Game Modes

1. **Play Mode** (`"play"`):
   - Interactive gameplay against Stockfish
   - Engine responds to each human move
   - Skill-adjusted engine strength
   - Real-time coaching on human moves

2. **Training Mode** (`"training"`):
   - Analysis-only mode
   - No engine responses
   - Focus on move evaluation and coaching
   - Suitable for game review

### Gameplay Flow

1. **Create Session**: Specify skill level and game mode
2. **Human Move**: Submit move via `/move` endpoint
3. **Analysis**: Full-strength analysis with coaching feedback
4. **Engine Response**: Stockfish plays at configured skill level (play mode only)
5. **Continue**: Repeat until game ends

### Example Interactive Game

```bash
# 1. Create a beginner-level game
curl -X POST "http://localhost:8000/v1/sessions" \
  -H "Content-Type: application/json" \
  -d '{"skill_level": "beginner", "game_mode": "play"}'

# Response:
# {"session_id": "abc123", "fen_start": "...", "game_mode": "play", "skill_level": "beginner"}

# 2. Play a move
curl -X POST "http://localhost:8000/v1/sessions/abc123/move" \
  -H "Content-Type: application/json" \
  -d '{"move": "e4"}'

# Response includes both human feedback and engine move:
# {
#   "legal": true,
#   "human_feedback": {
#     "san": "e4",
#     "severity": "best",
#     "basic": "Solid move. Keep building your plan.",
#     ...
#   },
#   "engine_move": {
#     "san": "e5",
#     "uci": "e7e5",
#     "fen_after": "...",
#     ...
#   }
# }
```

### Key Design Principles

- **Separation of Analysis and Play**: Human moves are analyzed at full strength for accurate coaching, while engine plays at adjusted skill
- **Progressive Difficulty**: Four skill levels from beginner to expert
- **Real-time Feedback**: Immediate coaching on move quality
- **Mobile Optimization**: Clean API structure for mobile app integration