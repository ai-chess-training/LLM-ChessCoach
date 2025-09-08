# stockfish_engine.py

import chess
import chess.engine
import chess.pgn
import os
import io
from typing import Dict, List, Optional, Any

# Set STOCKFISH_PATH from environment or default path
STOCKFISH_PATH = os.getenv('STOCKFISH_PATH', 'stockfish')

# Defaults for MVP
DEFAULT_MULTIPV = int(os.getenv('MULTIPV', '5'))
DEFAULT_NODES_PER_PV = int(os.getenv('NODES_PER_PV', '1000000'))

class StockfishAnalyzer:
    """Enhanced Stockfish analyzer for detailed position analysis (MultiPV support)."""

    def __init__(
        self,
        engine_path: str = STOCKFISH_PATH,
        depth: int = 15,
        nodes_limit: int = 500_000,
        multipv: int = DEFAULT_MULTIPV,
        nodes_per_pv: int = DEFAULT_NODES_PER_PV,
    ):
        """Initialize the Stockfish analyzer.

        - depth: optional fixed depth (rarely used if nodes_limit provided)
        - nodes_limit: fallback nodes cap if multipv/nodes_per_pv not used
        - multipv: number of PVs to compute
        - nodes_per_pv: approximate nodes budget per PV (total nodes ≈ multipv * nodes_per_pv)
        """
        self.engine_path = engine_path
        self.depth = depth
        self.nodes_limit = nodes_limit
        self.multipv = max(1, int(multipv))
        self.nodes_per_pv = max(10_000, int(nodes_per_pv))
        self.engine = None
        self.num_threads = min(8, os.cpu_count())

    
    def __enter__(self):
        """Context manager entry - start the engine."""
        self.engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
        # MultiPV is managed per-analyse call via multipv= argument; do not set here
        self.engine.configure({"Threads": self.num_threads})
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - quit the engine."""
        if self.engine:
            self.engine.quit()
    
    def analyze_position(
        self,
        board: chess.Board,
        depth: Optional[int] = None,
        nodes_limit: Optional[int] = None,
        multipv: Optional[int] = None,
        nodes_per_pv: Optional[int] = None,
    ) -> Dict[str, Any]:
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
        mpv = multipv if multipv is not None else self.multipv
        npp = nodes_per_pv if nodes_per_pv is not None else self.nodes_per_pv
        # Aim for ~1M nodes per PV by scaling total node budget
        analysis_node_limit = max(npp * mpv, nodes_limit if nodes_limit is not None else self.nodes_limit)
        
        try:
            # Request MultiPV analysis
            infos = self.engine.analyse(
                board,
                chess.engine.Limit(nodes=analysis_node_limit),
                multipv=mpv,
            )

            # Normalize to list
            if isinstance(infos, dict):
                infos = [infos]

            multipv_entries: List[Dict[str, Any]] = []
            best_move = None
            best_move_san = None
            top_score_dict = {}

            # Collect PVs
            for idx, info in enumerate(infos):
                score = info.get('score', chess.engine.Cp(0))
                score_dict = {}
                if score.is_mate():
                    score_dict['mate'] = score.white().mate()
                else:
                    cp_score = score.white().score()
                    score_dict['cp'] = cp_score if cp_score is not None else 0

                pv = info.get('pv', [])
                pv_san = []
                move_uci = str(pv[0]) if pv else None
                move_san = None

                if pv:
                    temp_board = board.copy()
                    for j, move in enumerate(pv[:10]):
                        try:
                            san = temp_board.san(move)
                            pv_san.append(san)
                            if j == 0:
                                move_san = san
                            temp_board.push(move)
                        except Exception:
                            break

                entry = {
                    'move_san': move_san,
                    'move_uci': move_uci,
                    'cp': score_dict.get('cp'),
                    'mate': score_dict.get('mate'),
                    'line_san': pv_san,
                }
                multipv_entries.append(entry)

                if idx == 0:
                    best_move = move_uci
                    best_move_san = move_san
                    top_score_dict = score_dict

            # Use info from the top PV to populate summary fields
            # Try to pick nodes/time from first info object
            nodes_val = 0
            time_val = 0.0
            if infos:
                nodes_val = infos[0].get('nodes', 0)
                time_val = infos[0].get('time', 0.0)

            return {
                'score': top_score_dict,
                'best_move': best_move,
                'best_move_san': best_move_san,
                'pv': multipv_entries,  # MultiPV list
                'depth': analysis_depth,
                'nodes': nodes_val,
                'time': time_val,
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
    
    def compare_move(
        self,
        board: chess.Board,
        move_played: chess.Move,
        depth: Optional[int] = None,
        nodes_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
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
        eval_before = self.analyze_position(board, depth, nodes_limit)
        
        # Get the best move
        best_move = eval_before.get('best_move')
        move_played_uci = str(move_played)

        # Check if played move is the best move
        is_best = (best_move == move_played_uci) if best_move else False
        
        # Analyze position after the move
        board.push(move_played)
        eval_after = self.analyze_position(board, depth, nodes_limit)
        board.pop()  # Restore position
        
        # Calculate evaluation loss from the mover's perspective
        eval_loss_cp = 0
        mover_is_white = board.turn  # True if white to move before pushing

        before_cp_white = eval_before.get('score', {}).get('cp')
        after_cp_white = eval_after.get('score', {}).get('cp')

        if before_cp_white is not None and after_cp_white is not None:
            if mover_is_white:
                before_cp_mover = before_cp_white
                after_cp_mover = after_cp_white
            else:
                # From black perspective invert
                before_cp_mover = -before_cp_white
                after_cp_mover = -after_cp_white
            eval_loss_cp = (before_cp_mover - after_cp_mover)
        
        return {
            'move_played': move_played_uci,
            'move_played_san': board.san(move_played),
            'best_move': best_move,
            'best_move_san': eval_before.get('best_move_san'),
            'eval_before': eval_before,
            'eval_after': eval_after,
            'eval_loss': (eval_loss_cp / 100.0) if eval_loss_cp else 0.0,  # pawns, positive means worse for mover
            'is_best': is_best
        }


def evaluate_game(pgn_path: str, depth: int = 15, nodes_limit: int = 500000) -> List[Dict[str, Any]]:
    """
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
    
    with StockfishAnalyzer(depth=depth, nodes_limit=nodes_limit) as analyzer:
        move_num = 0
        for move_node in game.mainline():
            move = move_node.move
            
            # Analyze the move
            comparison = analyzer.compare_move(board, move, depth, nodes_limit)
            
            evaluations.append({
                'move_number': move_num // 2 + 1,
                'side': 'white' if move_num % 2 == 0 else 'black',
                'move': board.san(move),
                'evaluation': comparison
            })
            
            board.push(move)
            move_num += 1
    
    return evaluations


def evaluate_game_detailed(pgn_content: str, depth: int = 15,
                           nodes_limit: int = 500000) -> Dict[int, Dict[str, Any]]:
    """
    Evaluate a game from PGN content string with detailed position-by-position analysis.
    
    Args:
        pgn_content: PGN content as a string
        depth: Analysis depth
    
    Returns:
        Dictionary mapping move numbers to detailed evaluations
    """
    try:
        game = chess.pgn.read_game(io.StringIO(pgn_content))
    except Exception as e:
        print(f"Error parsing PGN in evaluate_game_detailed: {e}")
        return {}
    
    if not game:
        return {}
    
    # Validate the game has moves
    move_list = list(game.mainline_moves())
    if not move_list:
        print("Warning: Game has no valid moves for analysis")
        return {}
    
    analysis = {}
    board = game.board()
    
    try:
        with StockfishAnalyzer(depth=depth) as analyzer:
            # Analyze starting position
            initial_eval = analyzer.analyze_position(board, depth)
            analysis[-1] = initial_eval  # Position before first move
            
            move_num = 0
            for move_node in game.mainline():
                move = move_node.move
                
                # Validate move is legal
                if move not in board.legal_moves:
                    print(f"Warning: Skipping illegal move at position {move_num}")
                    move_num += 1
                    continue
                
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
    
    except Exception as e:
        print(f"Error during Stockfish analysis: {e}")
        # Return what we have so far
        pass
    
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
    evals_white_after = []
    evals_black_after = []
    evals_white_before = []
    evals_black_before = []
    
    for move_data in analysis:
        evaluation = move_data.get('evaluation', {})
        eval_loss = evaluation.get('eval_loss', 0)
        is_best = evaluation.get('is_best', False)
        
        eval_after_move = evaluation.get("eval_after", {}).get('score', {}).get('cp', 0)
        eval_before_move = evaluation.get("eval_before", {}).get('score', {}).get('cp', 0)

        if move_data['side'] == 'white':
            white_moves += 1
            white_losses.append(eval_loss)
            evals_white_after.append(eval_after_move)
            evals_white_before.append(eval_before_move)
            if is_best:
                white_best_moves += 1
        else:
            black_moves += 1
            black_losses.append(eval_loss)
            evals_black_after.append(eval_after_move)
            evals_black_before.append(eval_before_move)
            if is_best:
                black_best_moves += 1
    
    def calculate_accuracy( evals_before, evals_after):
        """Calculate accuracy percentage based on centipawn losses."""
        import math
        win_percentages_before = [(50 + 50 * (2 / (1 + math.exp(-0.00368208 * eval)) - 1)) for eval in evals_before]
        win_percentages_after = [(50 + 50 * (2 / (1 + math.exp(-0.00368208 * eval) ) - 1)) for eval in evals_after]
        
        def accuracy(win_after, win_before):
            return 103.1668 * math.exp(-0.04354 * (win_before - win_after)) - 3.1669
        
        accuracy_per_move = [accuracy(wb, wa) for wb, wa in zip(win_percentages_before, win_percentages_after)]

        return accuracy_per_move
    
    return {
        'white': {
            'accuracy_per_move': calculate_accuracy( evals_white_before, evals_white_after),
            'best_move_percentage': (white_best_moves / white_moves * 100) if white_moves else 0,
            'total_moves': white_moves
        },
        'black': {
            'accuracy_per_move': calculate_accuracy( evals_black_before, evals_black_after),
            'best_move_percentage': (black_best_moves / black_moves * 100) if black_moves else 0,
            'total_moves': black_moves
        },
        'total_moves': len(analysis)
    }


if __name__ == "__main__":
    # Example usage
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python stockfish_engine.py <pgn_file> [depth] [nodes_limit]")
        sys.exit(1)
    
    pgn_file = sys.argv[1]
    depth = int(sys.argv[2]) if len(sys.argv) > 2 else 18
    nodes_limit = int(sys.argv[3]) if len(sys.argv) > 3 else 500000
    
    #print(f"Analyzing {pgn_file} with depth at {depth}...")
    print(f"Analyzing {pgn_file} with node_limit at {nodes_limit}...")
    
    #analysis = evaluate_game(pgn_file, depth, nodes_limit)

    from analyze_games import get_game_from_pgn
    
    with open(pgn_file, 'r') as pgn_file:
        pgn_content = pgn_file.read()

    game = get_game_from_pgn(pgn_content)
    analysis = evaluate_game_detailed(pgn_content, depth, nodes_limit)


    # import pdb; pdb.set_trace()
    print(analysis)
    # Print statistics
    # stats = get_game_statistics(analysis)
    # print("\nGame Statistics:")
    # print(f"White accuracy: {stats['white']['accuracy_per_move']}")
    # print(f"Black accuracy: {stats['black']['accuracy_per_move']}")
    # print(f"White best move percentage: {stats['white']['best_move_percentage']:.1f}%")
    # print(f"Black best move percentage: {stats['black']['best_move_percentage']:.1f}%")
