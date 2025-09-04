import os
import argparse
import chess.pgn
import chess
import io
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
    """Extract White and Black players from PGN content."""
    game = chess.pgn.read_game(io.StringIO(pgn_content))
    if not game:
        return "Unknown", "Unknown"
    white_player = game.headers.get("White", "Unknown")
    black_player = game.headers.get("Black", "Unknown")
    return white_player, black_player

def get_game_from_pgn(pgn_content: str):
    """Parse PGN content and return game object."""
    return chess.pgn.read_game(io.StringIO(pgn_content))

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

def analyze_position_with_context(board: chess.Board, move_number: int, move_played: str, 
                                 stockfish_eval: Dict[str, Any], white_player: str, 
                                 black_player: str, game_phase: str = "middlegame") -> str:
    """Generate ChatGPT analysis for a specific position with Stockfish context."""
    client = OpenAI()
    
    # Determine game phase
    piece_count = len(board.piece_map())
    if piece_count <= 7:
        game_phase = "endgame"
    elif piece_count >= 28:
        game_phase = "opening"
    else:
        game_phase = "middlegame"
    
    # Format the evaluation
    eval_str = format_stockfish_eval(stockfish_eval)
    
    # Build detailed context
    side_to_move = "White" if move_number % 2 == 1 else "Black"
    move_display = f"{move_number // 2 + 1}{'.' if side_to_move == 'White' else '...'}{move_played}"
    
    # Extract specific insights from Stockfish
    is_best_move = False
    eval_loss = 0
    better_move = None
    
    if 'evaluation' in stockfish_eval:
        eval_info = stockfish_eval['evaluation']
        is_best_move = eval_info.get('is_best', False)
        eval_loss = eval_info.get('eval_loss', 0)
        better_move = eval_info.get('best_move_san')
    
    prompt = f"""You are an instructive chess coach analyzing move {move_display} in the {game_phase} of a game between {white_player} (White) and {black_player} (Black).

Stockfish Analysis: {eval_str}
Move Quality: {'Best move' if is_best_move else f'Suboptimal (loses {eval_loss:.2f} pawns)' if eval_loss > 0.1 else 'Good move'}

Provide brief, instructive commentary (2-3 sentences) that:
1. Explains the key idea behind the move or position
2. {'Praises the accurate play' if is_best_move else f'Suggests {better_move} was more accurate and explains why' if better_move and not is_best_move else 'Describes the position characteristics'}
3. Mentions any tactical themes, strategic plans, or instructive patterns

Keep it educational and constructive."""

    completion = client.chat.completions.create(
        model="gpt-5-nano",  # Using gpt-5-nano for better analysis
        messages=[
            {"role": "system", "content": "You are a friendly chess instructor providing clear, concise, educational commentary."},
            {"role": "user", "content": prompt}
        ],
    )
    return completion.choices[0].message.content.strip()

def analyze_game_combined(pgn_content: str, user_alias: str, stockfish_depth: int = 15) -> Optional[Dict[str, Any]]:
    """Analyze a chess game by combining Stockfish evaluation with ChatGPT commentary."""
    game = get_game_from_pgn(pgn_content)
    if not game:
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
    
    # Generate move-by-move combined analysis
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
    
    # Add initial position evaluation
    if -1 in stockfish_analysis:
        initial_eval = stockfish_analysis[-1]
        combined_analysis["initial_evaluation"] = format_stockfish_eval(initial_eval)
    
    print("Generating move-by-move commentary...")
    for move_node in game.mainline():
        move = move_node.move
        san_move = board.san(move)
        
        # Get Stockfish evaluation for this move
        stockfish_eval = stockfish_analysis.get(move_number, {})
        
        # Generate ChatGPT commentary
        commentary = analyze_position_with_context(
            board,
            move_number,
            san_move,
            stockfish_eval,
            white_player,
            black_player
        )
        
        # Store the combined analysis
        move_analysis = {
            "move_number": move_number // 2 + 1,
            "side": "white" if move_number % 2 == 0 else "black",
            "move": san_move,
            "fen_before": board.fen(),
            "stockfish": stockfish_eval,
            "commentary": commentary
        }
        
        # Add the move to the FEN
        board.push(move)
        move_analysis["fen_after"] = board.fen()
        
        combined_analysis["moves"].append(move_analysis)
        move_number += 1
        
        # Progress indicator
        if move_number % 10 == 0:
            print(f"  Processed {move_number} moves...")
    
    # Calculate game statistics
    stats = get_game_statistics([{
        'side': m['side'],
        'evaluation': m['stockfish']
    } for m in combined_analysis['moves'] if 'evaluation' in m['stockfish']])
    
    combined_analysis["statistics"] = stats
    
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

def analyze_games(pgn_folder: str, user_alias: str, stockfish_depth: int = 15, max_workers: Optional[int] = None):
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
    print(f"Analysis will be saved to: {analysis_folder}\n")

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
                continue
            except Exception as e:
                print(f"  Error loading cached analysis: {e}")
                print(f"  Re-analyzing...")
        
        # Perform combined analysis
        try:
            start_time = time.time()
            analysis = analyze_game_combined(pgn_content, user_alias, stockfish_depth)
            
            if analysis:
                # Save the analysis
                save_analysis_results(analysis, analysis_folder, game_name)
                all_analyses.append(analysis)
                
                elapsed = time.time() - start_time
                print(f"  Analysis completed in {elapsed:.1f} seconds")
            else:
                print(f"  Failed to analyze game")
                
        except Exception as e:
            print(f"  Error analyzing game: {e}")
            import traceback
            traceback.print_exc()
    
    # Generate overall analysis
    if all_analyses:
        print("\n" + "=" * 60)
        print("Generating overall analysis for all games...")
        print("=" * 60)
        
        overall_analysis = generate_overall_analysis(all_analyses, user_alias)
        
        # Save overall analysis
        overall_file = os.path.join(analysis_folder, "overall_analysis.txt")
        with open(overall_file, 'w') as f:
            f.write(f"Overall Chess Analysis for {user_alias}\n")
            f.write("=" * 70 + "\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Games analyzed: {len(all_analyses)}\n")
            f.write("=" * 70 + "\n\n")
            f.write(overall_analysis)
        
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
    print("=" * 60)
    
    return True

def main():
    parser = argparse.ArgumentParser(
        description="Analyze chess games with combined Stockfish and ChatGPT analysis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python analyze_game.py --pgn_folder ./games --user_alias "John Doe"
  python analyze_game.py --pgn_folder ./games --user_alias "John Doe" --depth 20
  python analyze_game.py --pgn_folder ./games --user_alias "John Doe" --workers 2
        """
    )
    parser.add_argument("--pgn_folder", required=True, help="Folder containing PGN files to analyze")
    parser.add_argument("--user_alias", required=True, help="User alias for personalized analysis")
    parser.add_argument("--depth", type=int, default=15, help="Stockfish analysis depth (default: 15)")
    parser.add_argument("--workers", type=int, default=None, help="Number of parallel workers (default: auto)")

    args = parser.parse_args()
    
    # Validate inputs
    if not os.path.exists(args.pgn_folder):
        print(f"Error: PGN folder '{args.pgn_folder}' does not exist.")
        return
    
    if args.depth < 1 or args.depth > 30:
        print(f"Warning: Depth {args.depth} is unusual. Recommended range is 10-20.")
    
    # Run analysis
    analyze_games(args.pgn_folder, args.user_alias, args.depth, args.workers)

if __name__ == "__main__":
    main()