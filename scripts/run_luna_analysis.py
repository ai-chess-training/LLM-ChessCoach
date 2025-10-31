import os
import io
import sys
import argparse
import json
import time
from typing import List, Optional

import chess.pgn

# Ensure project root is on sys.path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from analysis_pipeline import analyze_pgn_to_feedback


def read_games_from_pgn_file(path: str) -> List[str]:
    """Return a list of serialized PGNs (one per game) from a PGN file."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    games = []
    reader = io.StringIO(content)
    while True:
        game = chess.pgn.read_game(reader)
        if game is None:
            break
        buf = io.StringIO()
        exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
        pgn_str = game.accept(exporter)
        games.append(pgn_str)
    return games


def latest_pgn_file(raw_dir: str) -> Optional[str]:
    if not os.path.isdir(raw_dir):
        return None
    files = [os.path.join(raw_dir, f) for f in os.listdir(raw_dir) if f.endswith(".pgn")]
    if not files:
        return None
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0]


def write_outputs(base_dir: str, stem: str, data: dict):
    os.makedirs(base_dir, exist_ok=True)
    json_path = os.path.join(base_dir, f"{stem}.json")
    txt_path = os.path.join(base_dir, f"{stem}.txt")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("Chess Analysis Summary\n")
        f.write("=" * 60 + "\n")
        f.write(f"ACPL (W/B): {data.get('acpl_white')} / {data.get('acpl_black')}\n")
        f.write(f"Best-move rate (W/B): {data.get('best_move_rate_white')}% / {data.get('best_move_rate_black')}%\n")
        f.write(f"Mistakes (W/B): {data.get('mistakes_white')} / {data.get('mistakes_black')}\n")
        f.write(f"Blunders (W/B): {data.get('blunders_white')} / {data.get('blunders_black')}\n")
        f.write("\nMove-by-move:\n")
        for m in data.get("moves", [])[:40]:
            f.write(f"{m['move_no']}. {m['san']} ({m['side']})  |  {m.get('basic','')}\n")
            f.write(f"  Extended: {m.get('extended','')}\n")
        f.write("\n")

    print(f"Wrote {json_path} and {txt_path}")


def main():
    parser = argparse.ArgumentParser(description="Run analysis on LunaNetEngine samples")
    parser.add_argument("--raw_dir", default="samples/luna/raw")
    parser.add_argument("--out_dir", default="samples/luna/analysis")
    parser.add_argument("--level", default="expert", choices=["beginner","adv_beginner","intermediate","advanced","expert"])
    parser.add_argument("--sample_moves", type=int, default=10, help="Number of plies to include in sample run output (approx). Use 0 for full game only.")
    parser.add_argument("--mode", choices=["both","full","sample"], default="both", help="Which outputs to generate.")
    parser.add_argument("--llm", choices=["on","off"], default="on", help="Toggle ChatGPT commentary per run.")
    parser.add_argument("--llm_mode", choices=["all","critical"], default="all", help="If 'critical', only analyze mistakes/blunders with LLM.")
    args = parser.parse_args()

    pgn_file = latest_pgn_file(args.raw_dir)
    if not pgn_file:
        raise SystemExit(f"No PGN files found in {args.raw_dir}. Run scripts/fetch_luna_games.py first.")

    games = read_games_from_pgn_file(pgn_file)
    if not games:
        raise SystemExit("No valid games parsed from PGN file.")

    ts = time.strftime("%Y%m%d_%H%M%S")

    use_llm = args.llm == "on"

    if args.mode in ("both", "full"):
        full = analyze_pgn_to_feedback(games[0], level=args.level, use_llm=use_llm, llm_mode=args.llm_mode)
        write_outputs(args.out_dir, f"full_{ts}", full)

    if args.mode in ("both", "sample"):
        # If we already computed full, slice it; otherwise compute a fresh one and slice
        if 'full' in locals() and full and full.get("moves"):
            sample = dict(full)
            sample["moves"] = full["moves"][: args.sample_moves]
        else:
            sample_res = analyze_pgn_to_feedback(
                games[0], level=args.level, max_plies=args.sample_moves, use_llm=use_llm, llm_mode=args.llm_mode
            )
            sample = sample_res or {"moves": []}
        write_outputs(args.out_dir, f"sample_{ts}", sample)


if __name__ == "__main__":
    main()
