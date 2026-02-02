"""Evaluation harness for comparing models."""

import os
import json
import argparse
import asyncio
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from statistics import mean, stdev

from env_loader import load_env

load_env()


@dataclass
class EvalResult:
    """Result from a single evaluation."""
    sample_id: int
    input_data: Dict[str, Any]
    expected: str
    baseline_output: str
    candidate_output: str
    baseline_latency_ms: int
    candidate_latency_ms: int
    baseline_error: Optional[str] = None
    candidate_error: Optional[str] = None


@dataclass
class EvalSummary:
    """Summary statistics from evaluation."""
    total_samples: int = 0
    baseline_errors: int = 0
    candidate_errors: int = 0
    baseline_avg_latency_ms: float = 0.0
    candidate_avg_latency_ms: float = 0.0
    baseline_latencies: List[float] = field(default_factory=list)
    candidate_latencies: List[float] = field(default_factory=list)


async def call_model(
    model: str,
    messages: List[Dict[str, str]],
    api_key: str,
    api_endpoint: str = "https://api.openai.com/v1",
) -> tuple[str, int, Optional[str]]:
    """
    Call a model and return (response, latency_ms, error).
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, base_url=api_endpoint)

    start = time.time()
    try:
        completion = await client.chat.completions.create(
            model=model,
            messages=messages,
        )
        content = completion.choices[0].message.content.strip()
        latency = int((time.time() - start) * 1000)
        return content, latency, None
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return "", latency, str(e)


async def evaluate_sample(
    sample: Dict[str, Any],
    baseline_model: str,
    candidate_model: str,
    api_key: str,
    api_endpoint: str,
) -> EvalResult:
    """Evaluate a single sample against both models."""
    from training.export import SYSTEM_PROMPT, build_user_prompt

    # Build messages
    user_prompt = build_user_prompt(sample)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # Call both models in parallel
    baseline_task = call_model(baseline_model, messages, api_key, api_endpoint)
    candidate_task = call_model(candidate_model, messages, api_key, api_endpoint)

    baseline_result, candidate_result = await asyncio.gather(baseline_task, candidate_task)

    baseline_output, baseline_latency, baseline_error = baseline_result
    candidate_output, candidate_latency, candidate_error = candidate_result

    return EvalResult(
        sample_id=sample.get("id", 0),
        input_data={
            "san": sample.get("san"),
            "best_move_san": sample.get("best_move_san"),
            "cp_loss": sample.get("cp_loss"),
            "severity": sample.get("severity"),
        },
        expected=sample.get("coaching_response", ""),
        baseline_output=baseline_output,
        candidate_output=candidate_output,
        baseline_latency_ms=baseline_latency,
        candidate_latency_ms=candidate_latency,
        baseline_error=baseline_error,
        candidate_error=candidate_error,
    )


async def run_evaluation(
    baseline_model: str,
    candidate_model: str,
    samples: List[Dict[str, Any]],
    api_key: str,
    api_endpoint: str = "https://api.openai.com/v1",
    concurrency: int = 5,
) -> tuple[List[EvalResult], EvalSummary]:
    """
    Run evaluation on all samples.

    Args:
        baseline_model: Model ID for baseline
        candidate_model: Model ID for candidate (fine-tuned)
        samples: List of training samples to evaluate
        api_key: OpenAI API key
        api_endpoint: API endpoint
        concurrency: Max concurrent requests

    Returns:
        Tuple of (results list, summary stats)
    """
    results = []
    summary = EvalSummary()

    semaphore = asyncio.Semaphore(concurrency)

    async def eval_with_semaphore(sample):
        async with semaphore:
            return await evaluate_sample(
                sample, baseline_model, candidate_model, api_key, api_endpoint
            )

    # Run all evaluations
    tasks = [eval_with_semaphore(s) for s in samples]
    results = await asyncio.gather(*tasks)

    # Compute summary
    summary.total_samples = len(results)

    for r in results:
        if r.baseline_error:
            summary.baseline_errors += 1
        else:
            summary.baseline_latencies.append(r.baseline_latency_ms)

        if r.candidate_error:
            summary.candidate_errors += 1
        else:
            summary.candidate_latencies.append(r.candidate_latency_ms)

    if summary.baseline_latencies:
        summary.baseline_avg_latency_ms = mean(summary.baseline_latencies)
    if summary.candidate_latencies:
        summary.candidate_avg_latency_ms = mean(summary.candidate_latencies)

    return results, summary


def print_comparison(result: EvalResult):
    """Print a side-by-side comparison of outputs."""
    print(f"\n{'='*60}")
    print(f"Sample {result.sample_id}: {result.input_data.get('san')} (loss: {result.input_data.get('cp_loss')}, {result.input_data.get('severity')})")
    print(f"{'='*60}")

    print(f"\nExpected:")
    print(f"  {result.expected}")

    print(f"\nBaseline ({result.baseline_latency_ms}ms):")
    if result.baseline_error:
        print(f"  ERROR: {result.baseline_error}")
    else:
        # Try to parse JSON and extract basic
        try:
            obj = json.loads(result.baseline_output)
            print(f"  {obj.get('basic', result.baseline_output)}")
        except:
            print(f"  {result.baseline_output[:200]}")

    print(f"\nCandidate ({result.candidate_latency_ms}ms):")
    if result.candidate_error:
        print(f"  ERROR: {result.candidate_error}")
    else:
        try:
            obj = json.loads(result.candidate_output)
            print(f"  {obj.get('basic', result.candidate_output)}")
        except:
            print(f"  {result.candidate_output[:200]}")


def print_summary(summary: EvalSummary, baseline: str, candidate: str):
    """Print evaluation summary."""
    print(f"\n{'='*60}")
    print("EVALUATION SUMMARY")
    print(f"{'='*60}")

    print(f"\nTotal samples: {summary.total_samples}")

    print(f"\n{baseline} (baseline):")
    print(f"  Errors: {summary.baseline_errors}")
    print(f"  Avg latency: {summary.baseline_avg_latency_ms:.0f}ms")
    if summary.baseline_latencies:
        print(f"  Latency stdev: {stdev(summary.baseline_latencies) if len(summary.baseline_latencies) > 1 else 0:.0f}ms")

    print(f"\n{candidate} (candidate):")
    print(f"  Errors: {summary.candidate_errors}")
    print(f"  Avg latency: {summary.candidate_avg_latency_ms:.0f}ms")
    if summary.candidate_latencies:
        print(f"  Latency stdev: {stdev(summary.candidate_latencies) if len(summary.candidate_latencies) > 1 else 0:.0f}ms")

    if summary.baseline_avg_latency_ms and summary.candidate_avg_latency_ms:
        speedup = summary.baseline_avg_latency_ms / summary.candidate_avg_latency_ms
        print(f"\nLatency speedup: {speedup:.2f}x")


def main():
    parser = argparse.ArgumentParser(description="Evaluate and compare models")
    parser.add_argument(
        "--baseline", "-b",
        default="gpt-4o-mini",
        help="Baseline model ID",
    )
    parser.add_argument(
        "--candidate", "-c",
        required=True,
        help="Candidate (fine-tuned) model ID",
    )
    parser.add_argument(
        "--samples", "-n",
        type=int,
        default=20,
        help="Number of samples to evaluate",
    )
    parser.add_argument(
        "--source",
        choices=["llm", "rules"],
        default="llm",
        help="Source filter for samples",
    )
    parser.add_argument(
        "--min-quality",
        type=int,
        help="Minimum quality rating for samples",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent API requests",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all comparisons (not just summary)",
    )
    parser.add_argument(
        "--output", "-o",
        help="Save results to JSON file",
    )

    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY not set")
        return

    # Use OpenAI directly for fine-tuned models
    api_endpoint = "https://api.openai.com/v1"

    # Get samples from database
    from training.store import get_samples
    samples = get_samples(
        source=args.source,
        min_quality=args.min_quality,
        limit=args.samples,
    )

    if not samples:
        print("No samples found. Collect training data first.")
        return

    print(f"Evaluating {len(samples)} samples...")
    print(f"Baseline: {args.baseline}")
    print(f"Candidate: {args.candidate}")

    # Run evaluation
    results, summary = asyncio.run(run_evaluation(
        baseline_model=args.baseline,
        candidate_model=args.candidate,
        samples=samples,
        api_key=api_key,
        api_endpoint=api_endpoint,
        concurrency=args.concurrency,
    ))

    # Show results
    if args.show_all:
        for r in results:
            print_comparison(r)

    print_summary(summary, args.baseline, args.candidate)

    # Save results
    if args.output:
        output_data = {
            "baseline": args.baseline,
            "candidate": args.candidate,
            "summary": {
                "total_samples": summary.total_samples,
                "baseline_errors": summary.baseline_errors,
                "candidate_errors": summary.candidate_errors,
                "baseline_avg_latency_ms": summary.baseline_avg_latency_ms,
                "candidate_avg_latency_ms": summary.candidate_avg_latency_ms,
            },
            "results": [
                {
                    "sample_id": r.sample_id,
                    "input": r.input_data,
                    "expected": r.expected,
                    "baseline": r.baseline_output,
                    "candidate": r.candidate_output,
                    "baseline_latency_ms": r.baseline_latency_ms,
                    "candidate_latency_ms": r.candidate_latency_ms,
                    "baseline_error": r.baseline_error,
                    "candidate_error": r.candidate_error,
                }
                for r in results
            ],
        }
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
