"""Fine-tuning job management with OpenAI API."""

import os
import argparse
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any

from env_loader import load_env

load_env()


def get_client():
    """Get OpenAI client."""
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set")

    # For fine-tuning, use OpenAI directly (not OpenRouter)
    # OpenRouter doesn't support fine-tuning
    return OpenAI(api_key=api_key)


def upload_file(client, file_path: str, purpose: str = "fine-tune") -> str:
    """
    Upload a file to OpenAI.

    Args:
        client: OpenAI client
        file_path: Path to JSONL file
        purpose: File purpose ('fine-tune' or 'fine-tune-results')

    Returns:
        File ID
    """
    with open(file_path, "rb") as f:
        response = client.files.create(file=f, purpose=purpose)
    return response.id


def create_fine_tuning_job(
    client,
    training_file_id: str,
    model: str = "gpt-4o-mini-2024-07-18",
    validation_file_id: Optional[str] = None,
    suffix: Optional[str] = None,
    hyperparameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a fine-tuning job.

    Args:
        client: OpenAI client
        training_file_id: ID of uploaded training file
        model: Base model to fine-tune
        validation_file_id: Optional validation file ID
        suffix: Optional model name suffix
        hyperparameters: Optional hyperparameters (n_epochs, batch_size, etc.)

    Returns:
        Job details dict
    """
    params = {
        "training_file": training_file_id,
        "model": model,
    }

    if validation_file_id:
        params["validation_file"] = validation_file_id

    if suffix:
        params["suffix"] = suffix

    if hyperparameters:
        params["hyperparameters"] = hyperparameters

    job = client.fine_tuning.jobs.create(**params)

    return {
        "id": job.id,
        "model": job.model,
        "status": job.status,
        "created_at": datetime.fromtimestamp(job.created_at).isoformat(),
        "training_file": job.training_file,
        "validation_file": job.validation_file,
        "fine_tuned_model": job.fine_tuned_model,
    }


def get_job_status(client, job_id: str) -> Dict[str, Any]:
    """Get the status of a fine-tuning job."""
    job = client.fine_tuning.jobs.retrieve(job_id)

    result = {
        "id": job.id,
        "model": job.model,
        "status": job.status,
        "created_at": datetime.fromtimestamp(job.created_at).isoformat(),
        "fine_tuned_model": job.fine_tuned_model,
    }

    if job.finished_at:
        result["finished_at"] = datetime.fromtimestamp(job.finished_at).isoformat()

    if job.error:
        result["error"] = {
            "code": job.error.code,
            "message": job.error.message,
        }

    return result


def list_jobs(client, limit: int = 10) -> list:
    """List recent fine-tuning jobs."""
    jobs = client.fine_tuning.jobs.list(limit=limit)
    return [
        {
            "id": job.id,
            "model": job.model,
            "status": job.status,
            "created_at": datetime.fromtimestamp(job.created_at).isoformat(),
            "fine_tuned_model": job.fine_tuned_model,
        }
        for job in jobs.data
    ]


def cancel_job(client, job_id: str) -> Dict[str, Any]:
    """Cancel a running fine-tuning job."""
    job = client.fine_tuning.jobs.cancel(job_id)
    return {
        "id": job.id,
        "status": job.status,
    }


def list_events(client, job_id: str, limit: int = 20) -> list:
    """List events for a fine-tuning job."""
    events = client.fine_tuning.jobs.list_events(fine_tuning_job_id=job_id, limit=limit)
    return [
        {
            "created_at": datetime.fromtimestamp(e.created_at).isoformat(),
            "level": e.level,
            "message": e.message,
        }
        for e in events.data
    ]


def wait_for_completion(
    client,
    job_id: str,
    poll_interval: int = 30,
    timeout: int = 7200,
) -> Dict[str, Any]:
    """
    Wait for a fine-tuning job to complete.

    Args:
        client: OpenAI client
        job_id: Job ID
        poll_interval: Seconds between status checks
        timeout: Maximum seconds to wait

    Returns:
        Final job status
    """
    start_time = time.time()

    while True:
        status = get_job_status(client, job_id)
        print(f"[{datetime.now().isoformat()}] Status: {status['status']}")

        if status["status"] in ("succeeded", "failed", "cancelled"):
            return status

        if time.time() - start_time > timeout:
            raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

        time.sleep(poll_interval)


def main():
    parser = argparse.ArgumentParser(description="Fine-tuning job management")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Create job
    create_parser = subparsers.add_parser("create", help="Create a fine-tuning job")
    create_parser.add_argument("--file", "-f", required=True, help="Training JSONL file")
    create_parser.add_argument("--val-file", help="Validation JSONL file")
    create_parser.add_argument(
        "--model", "-m",
        default="gpt-4o-mini-2024-07-18",
        help="Base model (default: gpt-4o-mini-2024-07-18)",
    )
    create_parser.add_argument("--suffix", "-s", help="Model name suffix")
    create_parser.add_argument("--epochs", type=int, help="Number of epochs")
    create_parser.add_argument("--wait", "-w", action="store_true", help="Wait for completion")

    # Status
    status_parser = subparsers.add_parser("status", help="Get job status")
    status_parser.add_argument("job_id", help="Job ID")

    # List
    list_parser = subparsers.add_parser("list", help="List recent jobs")
    list_parser.add_argument("--limit", "-n", type=int, default=10, help="Number of jobs")

    # Events
    events_parser = subparsers.add_parser("events", help="List job events")
    events_parser.add_argument("job_id", help="Job ID")
    events_parser.add_argument("--limit", "-n", type=int, default=20, help="Number of events")

    # Cancel
    cancel_parser = subparsers.add_parser("cancel", help="Cancel a job")
    cancel_parser.add_argument("job_id", help="Job ID")

    args = parser.parse_args()
    client = get_client()

    if args.command == "create":
        print(f"Uploading training file: {args.file}")
        train_file_id = upload_file(client, args.file)
        print(f"  File ID: {train_file_id}")

        val_file_id = None
        if args.val_file:
            print(f"Uploading validation file: {args.val_file}")
            val_file_id = upload_file(client, args.val_file)
            print(f"  File ID: {val_file_id}")

        hyperparams = {}
        if args.epochs:
            hyperparams["n_epochs"] = args.epochs

        print(f"Creating fine-tuning job...")
        job = create_fine_tuning_job(
            client,
            training_file_id=train_file_id,
            model=args.model,
            validation_file_id=val_file_id,
            suffix=args.suffix,
            hyperparameters=hyperparams if hyperparams else None,
        )
        print(f"Job created:")
        print(json.dumps(job, indent=2))

        # Register in local database
        try:
            from training.store import insert_model
            insert_model(
                model_id=job["id"],
                provider="openai",
                base_model=args.model,
                status="training",
            )
            print(f"Registered job in local database")
        except Exception as e:
            print(f"Warning: Could not register in local database: {e}")

        if args.wait:
            print("\nWaiting for completion...")
            final = wait_for_completion(client, job["id"])
            print(f"\nFinal status:")
            print(json.dumps(final, indent=2))

            if final["status"] == "succeeded" and final.get("fine_tuned_model"):
                print(f"\nFine-tuned model: {final['fine_tuned_model']}")
                print(f"Set as active with: python -m training.registry --set-active {final['fine_tuned_model']}")

    elif args.command == "status":
        status = get_job_status(client, args.job_id)
        print(json.dumps(status, indent=2))

    elif args.command == "list":
        jobs = list_jobs(client, args.limit)
        for job in jobs:
            status_icon = {
                "succeeded": "✓",
                "failed": "✗",
                "cancelled": "○",
                "running": "→",
                "queued": "…",
            }.get(job["status"], "?")
            model = job.get("fine_tuned_model") or job["model"]
            print(f"{status_icon} {job['id']} [{job['status']}] {model}")

    elif args.command == "events":
        events = list_events(client, args.job_id, args.limit)
        for event in reversed(events):
            print(f"[{event['created_at']}] {event['level']}: {event['message']}")

    elif args.command == "cancel":
        result = cancel_job(client, args.job_id)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
