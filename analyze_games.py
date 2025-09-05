import os
import argparse
import chess.pgn
import chess
import io
import re
from openai import OpenAI
import concurrent.futures
import multiprocessing
import json
from typing import Dict, List, Any, Optional
import time
from datetime import datetime

# Import the enhanced stockfish engine
from stockfish_engine import (
    StockfishAnalyzer, 
    evaluate_game_detailed,
    get_game_statistics,
    evaluate_game
)

def extract_players_from_pgn(pgn_content: str) -> tuple:
    """Extract White and Black players from PGN content with error handling."""
    try:
        game = chess.pgn.read_game(io.StringIO(pgn_content))
        if not game:
            return "Unknown", "Unknown"
        white_player = game.headers.get("White", "Unknown")
        black_player = game.headers.get("Black", "Unknown")
        return white_player, black_player
    except Exception as e:
        print(f"Warning: Could not extract players from PGN: {e}")
        return "Unknown", "Unknown"

def get_game_from_pgn(pgn_content: str):
    """Parse PGN content and return game object with error handling."""
    try:
        # Create a custom visitor that ignores variations and comments
        class SimpleGameBuilder(chess.pgn.BaseVisitor):
            def __init__(self):
                self.game = chess.pgn.Game()
                self.current_node = self.game
                self.board_stack = [self.game.board()]
                
            def visit_header(self, tagname, tagvalue):
                self.game.headers[tagname] = tagvalue
                
            def visit_move(self, board, move):
                self.current_node = self.current_node.add_variation(move)
                board.push(move)
                
            def result(self):
                return self.game
        
        # First try standard parsing
        game = None
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_content))
            # Validate by replaying
            if game:
                board = game.board()
                for move in game.mainline_moves():
                    if move not in board.legal_moves:
                        print(f"Warning: Found illegal move, attempting repair...")
                        game = None
                        break
                    board.push(move)
        except Exception as e:
            print(f"Standard parsing failed: {e}")
            game = None
        
        # If standard parsing failed, try simplified parsing
        if not game:
            print("  Attempting simplified parsing...")
            visitor = SimpleGameBuilder()
            
            # Parse headers manually
            lines = pgn_content.strip().split('\n')
            movetext_lines = []
            
            for line in lines:
                line = line.strip()
                if line.startswith('[') and line.endswith(']'):
                    # Parse header
                    import re
                    match = re.match(r'\[(\w+)\s+"(.*)"\]', line)
                    if match:
                        visitor.visit_header(match.group(1), match.group(2))
                elif line and not line.startswith('['):
                    movetext_lines.append(line)
            
            # Parse movetext
            movetext = ' '.join(movetext_lines)
            game = repair_pgn(pgn_content)
        
        return game
        
    except Exception as e:
        print(f"Critical error parsing PGN: {e}")
        return None

def repair_pgn(pgn_content: str):
    """Attempt to repair a malformed PGN by extracting moves and rebuilding."""
    try:
        import re
        
        # Extract headers and movetext
        lines = pgn_content.split('\n')
        headers = {}
        movetext_lines = []
        
        for line in lines:
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                # Header line
                match = re.match(r'\[(\w+)\s+"(.*)"\]', line)
                if match:
                    headers[match.group(1)] = match.group(2)
            elif line and not line.startswith('['):
                movetext_lines.append(line)
        
        # Join and clean movetext
        full_movetext = ' '.join(movetext_lines)
        
        # Remove result from movetext
        full_movetext = re.sub(r'(1-0|0-1|1/2-1/2|\*)$', '', full_movetext)
        
        # Remove comments, variations, and NAGs
        full_movetext = re.sub(r'\{[^}]*\}', '', full_movetext)  # Remove comments
        full_movetext = re.sub(r'\([^)]*\)', '', full_movetext)  # Remove variations
        full_movetext = re.sub(r'\$\d+', '', full_movetext)      # Remove NAG annotations
        full_movetext = re.sub(r'[!?]+', '', full_movetext)       # Remove annotations like !, ?, !!, etc.
        
        # Extract moves more carefully
        # Match move numbers and moves separately
        tokens = full_movetext.split()
        
        # Create new game
        game = chess.pgn.Game()
        
        # Set headers
        for key, value in headers.items():
            game.headers[key] = value
        
        # Parse moves
        board = game.board()
        node = game
        current_move_number = 1
        expecting_white = True
        
        i = 0
        while i < len(tokens):
            token = tokens[i].strip()
            
            # Skip move numbers
            if re.match(r'^\d+\.+$', token):
                i += 1
                continue
            
            # Check if it looks like a move
            if re.match(r'^[a-hNBRQKO]', token, re.IGNORECASE):
                # Clean the move text
                move_text = token.strip('.,+#x ')
                
                # Handle castling
                if move_text.upper() in ['O-O', '0-0']:
                    move_text = 'O-O'
                elif move_text.upper() in ['O-O-O', '0-0-0']:
                    move_text = 'O-O-O'
                
                try:
                    # Try to parse the move
                    move = board.parse_san(move_text)
                    node = node.add_variation(move)
                    board.push(move)
                except Exception as e:
                    # Try alternative interpretations
                    alternatives = [
                        move_text.replace('x', ''),  # Remove capture notation
                        move_text.replace('+', ''),   # Remove check notation
                        move_text.replace('#', ''),   # Remove checkmate notation
                        move_text.upper(),            # Try uppercase
                        move_text.lower(),            # Try lowercase
                    ]
                    
                    move_parsed = False
                    for alt in alternatives:
                        try:
                            move = board.parse_san(alt)
                            node = node.add_variation(move)
                            board.push(move)
                            move_parsed = True
                            break
                        except:
                            continue
                    
                    if not move_parsed:
                        print(f"  Skipping unparseable move: {token}")
            
            i += 1
        
        # Return the game if we got any valid moves
        if len(list(game.mainline_moves())) > 0:
            print(f"  Successfully repaired PGN with {len(list(game.mainline_moves()))} moves")
            return game
        else:
            print("  Could not extract any valid moves from PGN")
            return None
        
    except Exception as e:
        print(f"  Failed to repair PGN: {e}")
        return None

def format_stockfish_eval(eval_data: Dict[str, Any]) -> str:
    """Format Stockfish evaluation data for readability."""
    if not eval_data:
        return "No evaluation available"
    
    formatted = []
    
    # Handle both direct evaluation and comparison formats
    if 'evaluation' in eval_data:
        # This is a move comparison
        eval_info = eval_data['evaluation']
        eval_before = eval_info.get('eval_before', {})
        
        if eval_before.get('score'):
            score = eval_before['score']
            if 'mate' in score:
                formatted.append(f"Mate in {score['mate']}")
            elif 'cp' in score:
                pawn_eval = score['cp'] / 100
                formatted.append(f"Eval: {pawn_eval:+.2f}")
        
        if eval_info.get('best_move_san'):
            formatted.append(f"Best: {eval_info['best_move_san']}")
        
        if not eval_info.get('is_best') and eval_info.get('eval_loss'):
            formatted.append(f"Loss: {eval_info['eval_loss']:.2f} pawns")
            
    else:
        # Direct position evaluation
        if 'score' in eval_data:
            score = eval_data['score']
            if 'mate' in score:
                formatted.append(f"Mate in {score['mate']}")
            elif 'cp' in score:
                pawn_eval = score['cp'] / 100
                formatted.append(f"Eval: {pawn_eval:+.2f}")
        
        if 'best_move_san' in eval_data and eval_data['best_move_san']:
            formatted.append(f"Best: {eval_data['best_move_san']}")
        
        if 'pv_san' in eval_data and eval_data['pv_san']:
            formatted.append(f"Line: {' '.join(eval_data['pv_san'][:5])}")
    
    return " | ".join(formatted) if formatted else "No evaluation available"

def analyze_all_positions_batch(positions_data: List[Dict[str, Any]], white_player: str, 
                               black_player: str, user_alias: str, max_moves_per_batch: int = 100) -> List[str]:
    """
    Generate ChatGPT analysis for all positions in batch calls.
    Will split into multiple batches if game is very long.
    """
    client = OpenAI()
    all_commentaries = []
    
    # Process in batches if game is very long
    for batch_start in range(0, len(positions_data), max_moves_per_batch):
        batch_end = min(batch_start + max_moves_per_batch, len(positions_data))
        batch_positions = positions_data[batch_start:batch_end]
        
        if len(positions_data) > max_moves_per_batch:
            print(f"    Processing moves {batch_start+1}-{batch_end} of {len(positions_data)}...")
        
        # Build a comprehensive prompt with positions in this batch
        positions_info = []
        for pos in batch_positions:
            move_number = pos['move_number']
            side = pos['side']
            move_played = pos['move']
            stockfish_eval = pos['stockfish_eval']
            game_phase = pos['game_phase']
            
            # Format evaluation
            eval_str = format_stockfish_eval(stockfish_eval)
            
            # Extract move quality info
            is_best_move = False
            eval_loss = 0
            better_move = None
            
            if 'evaluation' in stockfish_eval:
                eval_info = stockfish_eval['evaluation']
                is_best_move = eval_info.get('is_best', False)
                eval_loss = eval_info.get('eval_loss', 0)
                better_move = eval_info.get('best_move_san')
            
            move_quality = 'Best move' if is_best_move else f'Loses {eval_loss:.2f} pawns' if eval_loss > 0.1 else 'Good move'
            
            position_entry = {
                "move_number": move_number,
                "side": side,
                "move": move_played,
                "phase": game_phase,
                "stockfish_eval": eval_str,
                "is_best": is_best_move,
                "better_move": better_move if not is_best_move else None,
                "eval_loss": eval_loss
            }
            positions_info.append(position_entry)
        
        # Create the batch prompt
        prompt = f"""You are an instructive chess coach analyzing {'a portion of' if len(positions_data) > max_moves_per_batch else ''} a game between {white_player} (White) and {black_player} (Black) for {user_alias}.

I will provide you with {'some' if len(positions_data) > max_moves_per_batch else 'all the'} moves and their Stockfish evaluations. Please provide educational commentary for EACH move.

For each move, provide 2-3 sentences of instructive commentary that:
1. Explains the key idea behind the move or position
2. Praises accurate play or suggests improvements when moves are suboptimal
3. Mentions tactical themes, strategic plans, or instructive patterns

IMPORTANT: Return your response as a valid JSON array where each element corresponds to one move in order. Each element should be a string containing the commentary for that move.

Here are the moves to analyze:

{json.dumps(positions_info, indent=2)}

Return ONLY a JSON array of commentary strings, one for each move, in the exact same order as provided above. Example format:
[
  "Commentary for move 1...",
  "Commentary for move 2...",
  "Commentary for move 3..."
]
"""

        completion = client.chat.completions.create(
            model="gpt-5-nano",
            messages=[
                {"role": "system", "content": "You are a chess instructor. Return ONLY a valid JSON array of commentary strings."},
                {"role": "user", "content": prompt}
            ],
        )
        
        response = completion.choices[0].message.content.strip()
        
        # Parse the JSON response
        try:
            # Clean the response if needed (remove markdown code blocks if present)
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            
            commentaries = json.loads(response.strip())
            
            # Validate we got the right number of commentaries
            if len(commentaries) != len(batch_positions):
                print(f"Warning: Expected {len(batch_positions)} commentaries, got {len(commentaries)}")
                # Pad or truncate as needed
                while len(commentaries) < len(batch_positions):
                    commentaries.append("Position analysis unavailable.")
                commentaries = commentaries[:len(batch_positions)]
            
            all_commentaries.extend(commentaries)
            
        except json.JSONDecodeError as e:
            print(f"Error parsing ChatGPT JSON response: {e}")
            print(f"Response preview: {response[:500]}...")
            # Fallback: return generic commentaries for this batch
            all_commentaries.extend([f"Move {pos['move_number']}: Analysis unavailable due to parsing error." 
                                    for pos in batch_positions])
                
    
    return all_commentaries

def analyze_game_combined(pgn_content: str, user_alias: str, stockfish_depth: int = 18, batch_size: int = 140) -> Optional[Dict[str, Any]]:
    """Analyze a chess game by combining Stockfish evaluation with ChatGPT commentary."""
    game = get_game_from_pgn(pgn_content)
    if not game:
        print("  Warning: Could not parse PGN file")
        return None
    
    # Validate that the game has moves
    move_count = len(list(game.mainline_moves()))
    if move_count == 0:
        print("  Warning: Game has no valid moves")
        return None
    
    white_player = game.headers.get("White", "Unknown")
    black_player = game.headers.get("Black", "Unknown")
    result = game.headers.get("Result", "*")
    date = game.headers.get("Date", "Unknown")
    event = game.headers.get("Event", "Unknown")
    
    print(f"Analyzing game: {white_player} vs {black_player}")
    print(f"Running Stockfish analysis (depth={stockfish_depth})...")
    
    # Get detailed Stockfish analysis
    stockfish_analysis = evaluate_game_detailed(pgn_content, depth=stockfish_depth)
    
    # Prepare combined analysis structure
    combined_analysis = {
        "white": white_player,
        "black": black_player,
        "result": result,
        "date": date,
        "event": event,
        "user": user_alias,
        "moves": [],
        "opening": game.headers.get("Opening", "Unknown"),
        "eco": game.headers.get("ECO", "Unknown")
    }
    
    board = game.board()
    move_number = 0
    positions_data = []
    
    # Add initial position evaluation
    if -1 in stockfish_analysis:
        initial_eval = stockfish_analysis[-1]
        combined_analysis["initial_evaluation"] = format_stockfish_eval(initial_eval)
    
    print("Collecting position data...")
    
    # First pass: Collect all position data for batch processing
    for move_node in game.mainline():
        move = move_node.move
        san_move = board.san(move)
        
        # Get Stockfish evaluation for this move
        stockfish_eval = stockfish_analysis.get(move_number, {})
        
        # Determine game phase
        piece_count = len(board.piece_map())
        if piece_count <= 7:
            game_phase = "endgame"
        elif piece_count >= 28:
            game_phase = "opening"
        else:
            game_phase = "middlegame"
        
        # Store position data for batch processing
        positions_data.append({
            "move_number": move_number // 2 + 1,
            "side": "white" if move_number % 2 == 0 else "black",
            "move": san_move,
            "fen_before": board.fen(),
            "stockfish_eval": stockfish_eval,
            "game_phase": game_phase
        })
        
        # Make the move on the board
        board.push(move)
        move_number += 1
        
        # Progress indicator
        if move_number % 10 == 0:
            print(f"  Processed {move_number} moves...")
    
    # Batch process all positions with ChatGPT
    num_batches = (len(positions_data) + batch_size - 1) // batch_size
    if num_batches > 1:
        print(f"Generating commentary for {len(positions_data)} moves in {num_batches} batches...")
    else:
        print(f"Generating commentary for all {len(positions_data)} moves in a single batch...")
    
    commentaries = analyze_all_positions_batch(
        positions_data, white_player, black_player, user_alias, max_moves_per_batch=batch_size
    )
    
    # Second pass: Combine Stockfish analysis with ChatGPT commentary
    board = game.board()  # Reset board
    for i, move_node in enumerate(game.mainline()):
        move = move_node.move
        
        # Get the position data and commentary
        pos_data = positions_data[i]
        commentary = commentaries[i] if i < len(commentaries) else "Analysis unavailable."
        
        # Create the combined move analysis
        move_analysis = {
            "move_number": pos_data["move_number"],
            "side": pos_data["side"],
            "move": pos_data["move"],
            "fen_before": pos_data["fen_before"],
            "stockfish": pos_data["stockfish_eval"],
            "commentary": commentary
        }
        
        # Add the move to get fen_after
        board.push(move)
        move_analysis["fen_after"] = board.fen()
        
        combined_analysis["moves"].append(move_analysis)
    
    # Calculate game statistics
    stats = get_game_statistics([{
        'side': m['side'],
        'evaluation': m['stockfish']
    } for m in combined_analysis['moves'] if 'evaluation' in m['stockfish']])
    
    combined_analysis["statistics"] = stats
    
    print(f"  Analysis complete - processed {len(combined_analysis['moves'])} moves")
    
    return combined_analysis


def generate_overall_analysis(all_games_analysis: List[Dict[str, Any]], user_alias: str) -> str:
    """Generate comprehensive overall analysis based on multiple games."""
    client = OpenAI()
    
    if not all_games_analysis:
        return "No games to analyze."
    
    # Aggregate statistics
    total_games = len(all_games_analysis)
    total_moves = sum(len(g.get('moves', [])) for g in all_games_analysis)
    
    # Calculate average statistics
    avg_white_accuracy = []
    avg_black_accuracy = []
    common_openings = {}
    
    for game in all_games_analysis:
        stats = game.get('statistics', {})
        if 'white' in stats:
            avg_white_accuracy.append(stats['white'].get('accuracy', 0))
        if 'black' in stats:
            avg_black_accuracy.append(stats['black'].get('accuracy', 0))
        
        opening = game.get('opening', 'Unknown')
        if opening != 'Unknown':
            common_openings[opening] = common_openings.get(opening, 0) + 1
    
    # Prepare statistics summary
    stats_summary = f"""
Games analyzed: {total_games}
Total moves: {total_moves}
Average accuracy as White: {sum(avg_white_accuracy)/len(avg_white_accuracy):.1f}% (across {len(avg_white_accuracy)} games)
Average accuracy as Black: {sum(avg_black_accuracy)/len(avg_black_accuracy):.1f}% (across {len(avg_black_accuracy)} games)
Most common openings: {', '.join([f"{k} ({v})" for k, v in sorted(common_openings.items(), key=lambda x: x[1], reverse=True)[:3]])}
"""
    
    prompt = f"""You are a chess grandmaster and coach providing a comprehensive analysis for {user_alias}.

Based on the analysis of {total_games} games, provide detailed feedback covering:

{stats_summary}

Please provide:
1. **Overall Playing Strength Assessment** - What level does the player appear to be?
2. **Opening Repertoire Analysis** - Strengths and weaknesses in the opening phase
3. **Middlegame Understanding** - Tactical awareness and strategic planning
4. **Endgame Technique** - Technical skills in simplified positions
5. **Common Patterns** - Recurring mistakes or missed opportunities
6. **Psychological Aspects** - Time management, handling pressure, decision-making
7. **Specific Recommendations** - 3-5 concrete steps for improvement
8. **Study Materials** - Books, courses, or training methods suited to their level

Make your advice actionable and encouraging while being honest about areas for improvement."""
    
    completion = client.chat.completions.create(
        model="gpt-5-nano",
        messages=[
            {"role": "system", "content": "You are an experienced chess coach providing comprehensive, actionable improvement advice."},
            {"role": "user", "content": prompt}
        ],
    )
    return stats_summary + "\n" + completion.choices[0].message.content

def save_analysis_results(analysis: Dict[str, Any], output_dir: str, game_name: str):
    """Save analysis results in multiple formats."""
    # Save JSON format
    json_file = os.path.join(output_dir, f'{game_name}_analysis.json')
    with open(json_file, 'w') as f:
        json.dump(analysis, f, indent=2)
    print(f"  JSON analysis saved to {json_file}")
    
    # Save human-readable format
    text_file = os.path.join(output_dir, f'{game_name}_readable.txt')
    with open(text_file, 'w') as f:
        f.write(f"Chess Game Analysis\n")
        f.write(f"{'=' * 70}\n")
        f.write(f"White: {analysis['white']}\n")
        f.write(f"Black: {analysis['black']}\n")
        f.write(f"Result: {analysis.get('result', '*')}\n")
        f.write(f"Date: {analysis.get('date', 'Unknown')}\n")
        f.write(f"Opening: {analysis.get('opening', 'Unknown')} [{analysis.get('eco', '')}]\n")
        f.write(f"\nGame Statistics:\n")
        
        stats = analysis.get('statistics', {})
        if 'white' in stats:
            f.write(f"  White - Accuracy: {stats['white']['accuracy']:.1f}%, ")
            f.write(f"Avg. Loss: {stats['white']['avg_centipawn_loss']:.2f} pawns\n")
        if 'black' in stats:
            f.write(f"  Black - Accuracy: {stats['black']['accuracy']:.1f}%, ")
            f.write(f"Avg. Loss: {stats['black']['avg_centipawn_loss']:.2f} pawns\n")
        
        f.write(f"\n{'=' * 70}\n")
        f.write("Move-by-Move Analysis\n")
        f.write(f"{'=' * 70}\n\n")
        
        for move in analysis['moves']:
            move_num = move['move_number']
            side = move['side'].capitalize()
            move_str = move['move']
            
            f.write(f"Move {move_num}. {move_str} ({side})\n")
            
            if 'stockfish' in move and move['stockfish']:
                eval_str = format_stockfish_eval(move['stockfish'])
                f.write(f"Engine: {eval_str}\n")
            
            f.write(f"Commentary: {move['commentary']}\n")
            f.write(f"{'-' * 50}\n\n")
    
    print(f"  Readable analysis saved to {text_file}")

def analyze_games(pgn_folder: str, user_alias: str, stockfish_depth: int = 18, 
                 max_workers: Optional[int] = None, batch_size: int = 180):
    """Analyze a batch of chess games with combined Stockfish and ChatGPT analysis."""
    
    game_data = []
    all_analyses = []

    # Collect all PGN files
    for root, dirs, files in os.walk(pgn_folder):
        for file in files:
            if file.endswith(".pgn"):
                pgn_file_path = os.path.join(root, file)
                try:
                    with open(pgn_file_path, 'r') as pgn_file:
                        pgn_content = pgn_file.read()
                    if pgn_content:
                        game_data.append((pgn_content, file))
                except Exception as e:
                    print(f"Error reading {file}: {e}")

    if not game_data:
        print("No PGN files found in the specified folder.")
        return False

    # Create analysis directory
    analysis_folder = os.path.join(pgn_folder, 'analysis')
    os.makedirs(analysis_folder, exist_ok=True)

    print(f"\nFound {len(game_data)} PGN files to analyze")
    print(f"Using Stockfish depth: {stockfish_depth}")
    print(f"ChatGPT batch size: {batch_size} moves per call")
    print(f"Analysis will be saved to: {analysis_folder}\n")
    
    successful_analyses = 0
    failed_analyses = []

    # Analyze each game
    for idx, (pgn_content, filename) in enumerate(game_data, 1):
        game_name = os.path.splitext(filename)[0]
        analysis_file = os.path.join(analysis_folder, f'{game_name}_analysis.json')
        
        print(f"\n[{idx}/{len(game_data)}] Processing: {filename}")
        print("=" * 60)
        
        # Check if analysis already exists
        if os.path.exists(analysis_file):
            print(f"  Analysis already exists, loading from cache...")
            try:
                with open(analysis_file, 'r') as f:
                    analysis = json.load(f)
                all_analyses.append(analysis)
                successful_analyses += 1
                continue
            except Exception as e:
                print(f"  Error loading cached analysis: {e}")
                print(f"  Re-analyzing...")
        
        # Perform combined analysis
        try:
            start_time = time.time()
            analysis = analyze_game_combined(pgn_content, user_alias, stockfish_depth, batch_size)
            
            if analysis:
                # Save the analysis
                save_analysis_results(analysis, analysis_folder, game_name)
                all_analyses.append(analysis)
                
                elapsed = time.time() - start_time
                print(f"  Total analysis time: {elapsed:.1f} seconds")
                successful_analyses += 1
            else:
                print(f"  Skipping game due to parsing errors")
                failed_analyses.append(filename)
                # Save error log
                error_file = os.path.join(analysis_folder, f'{game_name}_error.txt')
                with open(error_file, 'w') as f:
                    f.write(f"Failed to analyze {filename}\n")
                    f.write(f"The PGN file may be corrupted or contain illegal moves.\n")
                    f.write(f"Original PGN content:\n\n{pgn_content}\n")
                print(f"  Error details saved to {error_file}")
                
        except Exception as e:
            print(f"  Error analyzing game: {e}")
            failed_analyses.append(filename)
            # Save error log
            error_file = os.path.join(analysis_folder, f'{game_name}_error.txt')
            with open(error_file, 'w') as f:
                f.write(f"Error analyzing {filename}: {e}\n")
                import traceback
                f.write(traceback.format_exc())
            print(f"  Error details saved to {error_file}")
            continue  # Continue with next game
    
    # Generate overall analysis
    if all_analyses:
        print("\n" + "=" * 60)
        print("Generating overall analysis for all games...")
        print("=" * 60)
        
        total_moves = sum(len(a.get('moves', [])) for a in all_analyses)
        avg_moves_per_game = total_moves // len(all_analyses) if all_analyses else 0
        
        # overall_analysis = generate_overall_analysis(all_analyses, user_alias)
        
        # Save overall analysis
        overall_file = os.path.join(analysis_folder, "overall_analysis.txt")
        with open(overall_file, 'w') as f:
            f.write(f"Overall Chess Analysis for {user_alias}\n")
            f.write("=" * 70 + "\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Games analyzed: {len(all_analyses)}\n")
            # f.write("=" * 70 + "\n\n")
            # f.write(overall_analysis)
        
        print(f"\nOverall analysis saved to: {overall_file}")
        
        # Save summary statistics
        summary_file = os.path.join(analysis_folder, "summary.json")
        summary = {
            "user": user_alias,
            "games_analyzed": len(all_analyses),
            "analysis_date": datetime.now().isoformat(),
            "stockfish_depth": stockfish_depth,
            "games": [
                {
                    "white": a.get("white"),
                    "black": a.get("black"),
                    "result": a.get("result"),
                    "date": a.get("date"),
                    "statistics": a.get("statistics")
                }
                for a in all_analyses
            ]
        }
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        
        print(f"Summary statistics saved to: {summary_file}")
    
    print("\n" + "=" * 60)
    print("Analysis complete!")
    print(f"Successfully analyzed: {successful_analyses}/{len(game_data)} games")
    if failed_analyses:
        print(f"Failed to analyze: {', '.join(failed_analyses)}")
    print("=" * 60)
    
    return True

def main():
    parser = argparse.ArgumentParser(
        description="Analyze chess games with combined Stockfish and ChatGPT analysis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python analyze_game.py --pgn_folder ./games --user_alias "John Doe"
  python analyze_game.py --pgn_folder ./games --user_alias "John Doe" --depth 18
  python analyze_game.py --pgn_folder ./games --user_alias "John Doe" --workers 1
  python analyze_game.py --pgn_folder ./games --user_alias "John Doe" --batch_size 180
        """
    )
    parser.add_argument("--pgn_folder", required=True, help="Folder containing PGN files to analyze")
    parser.add_argument("--user_alias", required=True, help="User alias for personalized analysis")
    parser.add_argument("--depth", type=int, default=18, help="Stockfish analysis depth (default: 18)")
    parser.add_argument("--workers", type=int, default=None, help="Number of parallel workers (default: auto)")
    parser.add_argument("--batch_size", type=int, default=180, 
                       help="Max moves per ChatGPT batch call (default: 180)")

    args = parser.parse_args()
    
    # Validate inputs
    if not os.path.exists(args.pgn_folder):
        print(f"Error: PGN folder '{args.pgn_folder}' does not exist.")
        return
    
    if args.depth < 1 or args.depth > 30:
        print(f"Warning: Depth {args.depth} is unusual. Recommended range is 10-20.")
    
    # Run analysis
    analyze_games(args.pgn_folder, args.user_alias, args.depth, args.workers, args.batch_size)

if __name__ == "__main__":
    main()