"""Export training data to JSONL format for fine-tuning."""

import os
import json
import argparse
from datetime import datetime
from typing import Optional, List, Dict, Any

from training.store import get_samples, get_sample_count


# System prompt used during inference - must match for fine-tuning
SYSTEM_PROMPT = "You are a concise chess coach that outputs strict JSON."


def build_user_prompt(sample: Dict[str, Any]) -> str:
    """Reconstruct the user prompt from a training sample."""
    structured = {
        "san": sample.get("san"),
        "best_move_san": sample.get("best_move_san"),
        "cp_loss": sample.get("cp_loss"),
        "side": sample.get("side"),
        "multipv": json.loads(sample.get("multipv_json") or "[]"),
    }

    level = sample.get("player_level", "intermediate")

    prompt = (
        "You are a concise chess coach. Given a move and engine data, "
        "return JSON with: basic (<=40 words) "
        f"Player level: {level}. Ground advice in PV; do not contradict engine.\n\n"
        f"Data:\n{json.dumps(structured)}\n\n"
        "Return only a JSON object with keys: basic."
    )
    return prompt


def build_assistant_response(sample: Dict[str, Any]) -> str:
    """Build the assistant response JSON."""
    return json.dumps({"basic": sample.get("coaching_response", "")})


def sample_to_messages(sample: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a training sample to OpenAI fine-tuning message format."""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(sample)},
            {"role": "assistant", "content": build_assistant_response(sample)},
        ]
    }


def export_to_jsonl(
    output_path: str,
    min_quality: Optional[int] = None,
    source: Optional[str] = None,
    exclude_flagged: bool = True,
    limit: Optional[int] = None,
) -> int:
    """
    Export training samples to JSONL format.

    Args:
        output_path: Path to write JSONL file
        min_quality: Minimum quality rating (1-5), None for unrated
        source: Filter by source ('llm' or 'rules'), None for all
        exclude_flagged: Exclude flagged samples
        limit: Maximum number of samples to export

    Returns:
        Number of samples exported
    """
    samples = get_samples(
        min_quality=min_quality,
        source=source,
        exclude_flagged=exclude_flagged,
        limit=limit,
    )

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    count = 0
    with open(output_path, "w") as f:
        for sample in samples:
            # Skip samples without coaching response
            if not sample.get("coaching_response"):
                continue

            messages = sample_to_messages(sample)
            f.write(json.dumps(messages) + "\n")
            count += 1

    return count


def validate_jsonl(path: str) -> Dict[str, Any]:
    """
    Validate a JSONL file for fine-tuning.

    Returns:
        Dict with validation results (valid, errors, warnings, stats)
    """
    errors = []
    warnings = []
    stats = {
        "total_lines": 0,
        "valid_lines": 0,
        "total_tokens_estimate": 0,
    }

    with open(path, "r") as f:
        for i, line in enumerate(f, 1):
            stats["total_lines"] += 1

            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"Line {i}: Invalid JSON - {e}")
                continue

            # Check structure
            if "messages" not in obj:
                errors.append(f"Line {i}: Missing 'messages' key")
                continue

            messages = obj["messages"]
            if not isinstance(messages, list):
                errors.append(f"Line {i}: 'messages' must be a list")
                continue

            if len(messages) < 2:
                errors.append(f"Line {i}: Must have at least 2 messages")
                continue

            # Check roles
            roles = [m.get("role") for m in messages]
            if "assistant" not in roles:
                errors.append(f"Line {i}: Must have at least one assistant message")
                continue

            # Check for system message (recommended)
            if "system" not in roles:
                warnings.append(f"Line {i}: No system message (recommended)")

            # Estimate tokens (rough: 4 chars per token)
            total_chars = sum(len(m.get("content", "")) for m in messages)
            stats["total_tokens_estimate"] += total_chars // 4

            stats["valid_lines"] += 1

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }


def split_train_val(
    input_path: str,
    train_path: str,
    val_path: str,
    val_ratio: float = 0.1,
) -> Dict[str, int]:
    """
    Split a JSONL file into training and validation sets.

    Args:
        input_path: Path to input JSONL
        train_path: Path to write training JSONL
        val_path: Path to write validation JSONL
        val_ratio: Fraction of data for validation (0.0-1.0)

    Returns:
        Dict with counts for train and val
    """
    import random

    # Read all lines
    with open(input_path, "r") as f:
        lines = f.readlines()

    # Shuffle
    random.shuffle(lines)

    # Split
    val_count = max(1, int(len(lines) * val_ratio))
    val_lines = lines[:val_count]
    train_lines = lines[val_count:]

    # Write
    os.makedirs(os.path.dirname(train_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(val_path) or ".", exist_ok=True)

    with open(train_path, "w") as f:
        f.writelines(train_lines)

    with open(val_path, "w") as f:
        f.writelines(val_lines)

    return {
        "train": len(train_lines),
        "val": len(val_lines),
    }


def main():
    parser = argparse.ArgumentParser(description="Export training data to JSONL")
    parser.add_argument(
        "--output", "-o",
        default="data/exports/train.jsonl",
        help="Output JSONL file path",
    )
    parser.add_argument(
        "--min-quality",
        type=int,
        default=None,
        help="Minimum quality rating (1-5)",
    )
    parser.add_argument(
        "--source",
        choices=["llm", "rules"],
        default=None,
        help="Filter by source",
    )
    parser.add_argument(
        "--include-flagged",
        action="store_true",
        help="Include flagged samples",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum samples to export",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate output file after export",
    )
    parser.add_argument(
        "--split",
        type=float,
        default=None,
        help="Split into train/val with given validation ratio (e.g., 0.1)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show database statistics",
    )

    args = parser.parse_args()

    if args.stats:
        total = get_sample_count(exclude_flagged=False)
        llm = get_sample_count(source="llm")
        rules = get_sample_count(source="rules")
        rated = get_sample_count(min_quality=1)
        high_quality = get_sample_count(min_quality=4)

        print(f"Training Data Statistics:")
        print(f"  Total samples:      {total}")
        print(f"  LLM responses:      {llm}")
        print(f"  Rule responses:     {rules}")
        print(f"  Rated samples:      {rated}")
        print(f"  High quality (4+):  {high_quality}")
        return

    # Export
    count = export_to_jsonl(
        output_path=args.output,
        min_quality=args.min_quality,
        source=args.source,
        exclude_flagged=not args.include_flagged,
        limit=args.limit,
    )
    print(f"Exported {count} samples to {args.output}")

    # Validate
    if args.validate:
        result = validate_jsonl(args.output)
        print(f"\nValidation: {'PASSED' if result['valid'] else 'FAILED'}")
        print(f"  Valid lines: {result['stats']['valid_lines']}/{result['stats']['total_lines']}")
        print(f"  Estimated tokens: {result['stats']['total_tokens_estimate']}")

        if result["errors"]:
            print(f"\nErrors ({len(result['errors'])}):")
            for err in result["errors"][:10]:
                print(f"  - {err}")

        if result["warnings"]:
            print(f"\nWarnings ({len(result['warnings'])}):")
            for warn in result["warnings"][:5]:
                print(f"  - {warn}")

    # Split
    if args.split is not None:
        base, ext = os.path.splitext(args.output)
        train_path = f"{base}_train{ext}"
        val_path = f"{base}_val{ext}"

        counts = split_train_val(args.output, train_path, val_path, args.split)
        print(f"\nSplit into:")
        print(f"  Training:   {counts['train']} samples -> {train_path}")
        print(f"  Validation: {counts['val']} samples -> {val_path}")


if __name__ == "__main__":
    main()
