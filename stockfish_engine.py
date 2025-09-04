# stockfish_engine.py

import chess
import chess.engine
import chess.pgn
import os
import io
from typing import Dict, List, Optional, Any

# Set STOCKFISH_PATH from environment or default path
STOCKFISH_PATH = os.getenv('STOCKFISH_PATH', 'stockfish')

class StockfishAnalyzer:
    """Enhanced Stockfish analyzer for detailed position analysis."""
    
    def __init__(self, engine_path: str = STOCKFISH_PATH, depth: int = 15):
        """Initialize the Stockfish analyzer."""
        self.engine_path = engine_path
        self.depth = depth
        self.engine = None
    
    def __enter__(self):
        """Context manager entry - start the engine."""
        self.engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - quit the engine."""
        if self.engine:
            self.engine.quit()
    
    def analyze_position(self, board: chess.Board, depth: Optional[int] = None) -> Dict[str, Any]:
        """
        Analyze a single position with Stockfish.
        
        Returns:
            Dictionary containing:
            - score: The evaluation score (in centipawns or mate count)
            - best_move: The best move in the position
            - pv: Principal variation (best line)
            - depth: Analysis depth
        """
        if not self.engine:
            raise RuntimeError("Engine not initialized. Use within context manager.")
        
        analysis_depth = depth if depth is not None else self.depth
        
        try:
            info = self.engine.analyse(board, chess.engine.Limit(depth=analysis_depth))
            
            # Extract score
            score = info.get('score', chess.engine.Cp(0))
            score_dict = {}
            
            if score.is_mate():
                # Mate score from the perspective of white
                mate_score = score.white().mate()
                score_dict['mate'] = mate_score
            else:
                # Centipawn score from the perspective of white
                cp_score = score.white().score()
                score_dict['cp'] = cp_score if cp_score is not None else 0
            
            # Extract best move and principal variation
            pv = info.get('pv', [])
            best_move = str(pv[0]) if pv else None
            
            # Convert PV to SAN notation for readability
            pv_san = []
            if pv:
                temp_board = board.copy()
                for move in pv[:10]:  # Limit to first 10 moves of PV
                    try:
                        pv_san.append(temp_board.san(move))
                        temp_board.push(move)
                    except:
                        break
            
            return {
                'score': score_dict,
                'best_move': best_move,
                'best_move_san': board.san(chess.Move.from_uci(best_move)) if best_move else None,
                'pv': [str(m) for m in pv[:10]],  # UCI notation
                'pv_san': pv_san,  # SAN notation
                'depth': analysis_depth,
                'nodes': info.get('nodes', 0),
                'time': info.get('time', 0)
            }
            
        except Exception as e:
            print(f"Error analyzing position: {e}")
            return {
                'score': {'cp': 0},
                'best_move': None,
                'best_move_san': None,
                'pv': [],
                'pv_san': [],
                'depth': analysis_depth,
                'error': str(e)
            }
    
    def compare_move(self, board: chess.Board, move_played: chess.Move, depth: Optional[int] = None) -> Dict[str, Any]:
        """
        Compare the move played with the engine's best move.
        
        Returns:
            Dictionary containing:
            - move_played: The actual move played
            - best_move: The engine's recommended move
            - eval_before: Evaluation before the move
            - eval_after: Evaluation after the move
            - eval_loss: Evaluation loss from the move (if not best)
            - is_best: Whether the played move was the best
        """
        # Analyze position before the move
        eval_before = self.analyze_position(board, depth)
        
        # Get the best move
        best_move = eval_before.get('best_move')
        move_played_uci = str(move_played)
        
        # Check if played move is the best move
        is_best = (best_move == move_played_uci) if best_move else False
        
        # Analyze position after the move
        board.push(move_played)
        eval_after = self.analyze_position(board, depth)
        board.pop()  # Restore position
        
        # Calculate evaluation loss
        eval_loss = 0
        if not is_best and eval_before.get('score') and eval_after.get('score'):
            before_cp = eval_before['score'].get('cp', 0)
            after_cp = eval_after['score'].get('cp', 0)
            
            # Both are centipawn scores
            if before_cp is not None and after_cp is not None:
                # From the perspective of the player who just moved
                eval_loss = abs(before_cp - (-after_cp))  # Negate because perspective changes
        
        return {
            'move_played': move_played_uci,
            'move_played_san': board.san(move_played),
            'best_move': best_move,
            'best_move_san': eval_before.get('best_move_san'),
            'eval_before': eval_before,
            'eval_after': eval_after,
            'eval_loss': eval_loss / 100 if eval_loss else 0,  # Convert to pawns
            'is_best': is_best
        }


def evaluate_game(pgn_path: str, depth: int = 15) -> List[Dict[str, Any]]:
    """
    Legacy function for backward compatibility.
    Evaluates an entire game from a PGN file.
    
    Args:
        pgn_path: Path to the PGN file
        depth: Analysis depth
    
    Returns:
        List of evaluations for each move
    """
    with open(pgn_path, 'r') as f:
        game = chess.pgn.read_game(f)
    
    if not game:
        return []
    
    evaluations = []
    board = game.board()
    
    with StockfishAnalyzer(depth=depth) as analyzer:
        move_num = 0
        for move_node in game.mainline():
            move = move_node.move
            
            # Analyze the move
            comparison = analyzer.compare_move(board, move, depth)
            
            evaluations.append({
                'move_number': move_num // 2 + 1,
                'side': 'white' if move_num % 2 == 0 else 'black',
                'move': board.san(move),
                'evaluation': comparison
            })
            
            board.push(move)
            move_num += 1
    
    return evaluations


def evaluate_game_detailed(pgn_content: str, depth: int = 15) -> Dict[int, Dict[str, Any]]:
    """
    Evaluate a game from PGN content string with detailed position-by-position analysis.
    
    Args:
        pgn_content: PGN content as a string
        depth: Analysis depth
    
    Returns:
        Dictionary mapping move numbers to detailed evaluations
    """
    game = chess.pgn.read_game(io.StringIO(pgn_content))
    
    if not game:
        return {}
    
    analysis = {}
    board = game.board()
    
    with StockfishAnalyzer(depth=depth) as analyzer:
        # Analyze starting position
        initial_eval = analyzer.analyze_position(board, depth)
        analysis[-1] = initial_eval  # Position before first move
        
        move_num = 0
        for move_node in game.mainline():
            move = move_node.move
            
            # Compare the move with best move
            comparison = analyzer.compare_move(board, move, depth)
            
            # Store the analysis
            analysis[move_num] = comparison
            
            # Make the move
            board.push(move)
            move_num += 1
        
        # Analyze final position
        final_eval = analyzer.analyze_position(board, depth)
        analysis[move_num] = final_eval
    
    return analysis


def analyze_multiple_games(pgn_files: List[str], depth: int = 15) -> Dict[str, List[Dict[str, Any]]]:
    """
    Analyze multiple games from PGN files.
    
    Args:
        pgn_files: List of PGN file paths
        depth: Analysis depth
    
    Returns:
        Dictionary mapping file names to their analyses
    """
    results = {}
    
    for pgn_file in pgn_files:
        try:
            filename = os.path.basename(pgn_file)
            print(f"Analyzing {filename}...")
            analysis = evaluate_game(pgn_file, depth)
            results[filename] = analysis
        except Exception as e:
            print(f"Error analyzing {pgn_file}: {e}")
            results[os.path.basename(pgn_file)] = []
    
    return results


def get_game_statistics(analysis: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate statistics from game analysis.
    
    Returns:
        Dictionary with game statistics like average centipawn loss, accuracy, etc.
    """
    if not analysis:
        return {}
    
    white_losses = []
    black_losses = []
    white_best_moves = 0
    black_best_moves = 0
    white_moves = 0
    black_moves = 0
    
    for move_data in analysis:
        evaluation = move_data.get('evaluation', {})
        eval_loss = evaluation.get('eval_loss', 0)
        is_best = evaluation.get('is_best', False)
        
        if move_data['side'] == 'white':
            white_moves += 1
            white_losses.append(eval_loss)
            if is_best:
                white_best_moves += 1
        else:
            black_moves += 1
            black_losses.append(eval_loss)
            if is_best:
                black_best_moves += 1
    
    def calculate_accuracy(losses):
        """Calculate accuracy percentage based on centipawn losses."""
        if not losses:
            return 100.0
        # Formula: accuracy = 103.1668 * exp(-0.04354 * avg_centipawn_loss) - 3.1669
        avg_loss = sum(losses) / len(losses)
        import math
        accuracy = 103.1668 * math.exp(-0.04354 * avg_loss * 100) - 3.1669
        return max(0, min(100, accuracy))
    
    return {
        'white': {
            'avg_centipawn_loss': sum(white_losses) / len(white_losses) if white_losses else 0,
            'accuracy': calculate_accuracy(white_losses),
            'best_move_percentage': (white_best_moves / white_moves * 100) if white_moves else 0,
            'total_moves': white_moves
        },
        'black': {
            'avg_centipawn_loss': sum(black_losses) / len(black_losses) if black_losses else 0,
            'accuracy': calculate_accuracy(black_losses),
            'best_move_percentage': (black_best_moves / black_moves * 100) if black_moves else 0,
            'total_moves': black_moves
        },
        'total_moves': len(analysis)
    }


if __name__ == "__main__":
    # Example usage
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python stockfish_engine.py <pgn_file> [depth]")
        sys.exit(1)
    
    pgn_file = sys.argv[1]
    depth = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    
    print(f"Analyzing {pgn_file} with depth {depth}...")
    analysis = evaluate_game(pgn_file, depth)
    
    # Print statistics
    stats = get_game_statistics(analysis)
    print("\nGame Statistics:")
    print(f"White accuracy: {stats['white']['accuracy']:.1f}%")
    print(f"Black accuracy: {stats['black']['accuracy']:.1f}%")
    print(f"White avg. centipawn loss: {stats['white']['avg_centipawn_loss']:.2f}")
    print(f"Black avg. centipawn loss: {stats['black']['avg_centipawn_loss']:.2f}")