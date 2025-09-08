import os
import sys
import time
import argparse
import requests

DEFAULT_USER = "LunaNetEngine"


def fetch_pgn(username: str, max_games: int = 5, token: str = "") -> str:
    url = f"https://lichess.org/api/games/user/{username}?max={max_games}&moves=true&pgnInJson=false"
    headers = {"Accept": "application/x-chess-pgn"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def save_pgn(pgn_text: str, output_dir: str, username: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"{username}_{ts}.pgn")
    with open(path, "w", encoding="utf-8") as f:
        f.write(pgn_text)
    return path


def main():
    parser = argparse.ArgumentParser(description="Fetch sample PGNs for LunaNetEngine from Lichess")
    parser.add_argument("--username", default=DEFAULT_USER)
    parser.add_argument("--max_games", type=int, default=5)
    parser.add_argument("--output_dir", default="samples/luna/raw")
    args = parser.parse_args()

    token = os.getenv("LICHESS_API_TOKEN", "")
    try:
        pgn_text = fetch_pgn(args.username, args.max_games, token)
    except Exception as e:
        print(f"Error fetching PGNs: {e}")
        sys.exit(1)

    path = save_pgn(pgn_text, args.output_dir, args.username)
    print(f"Saved PGNs to {path}")


if __name__ == "__main__":
    main()

