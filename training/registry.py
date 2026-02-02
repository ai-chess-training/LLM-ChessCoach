"""Model registry for managing fine-tuned models."""

import os
import argparse
import json
from typing import Optional, Dict, Any

from training.store import (
    get_active_model,
    set_active_model,
    list_models,
    get_model,
    insert_model,
    update_model_status,
)


def get_current_model() -> str:
    """
    Get the model ID that should be used for inference.

    Priority:
    1. FINETUNED_MODEL_ID environment variable (explicit override)
    2. Active model from registry
    3. OPENAI_MODEL environment variable (base model fallback)
    4. Default: gpt-4
    """
    # Check for explicit override
    override = os.getenv("FINETUNED_MODEL_ID")
    if override:
        return override

    # Check registry
    active = get_active_model()
    if active and active.get("fine_tuned_model_id"):
        return active["fine_tuned_model_id"]

    # Fall back to base model config
    return os.getenv("OPENAI_MODEL", "gpt-4")


def register_model(
    model_id: str,
    provider: str = "openai",
    base_model: Optional[str] = None,
    fine_tuned_model_id: Optional[str] = None,
    status: str = "ready",
) -> str:
    """Register a new model in the registry."""
    return insert_model(
        model_id=model_id,
        provider=provider,
        base_model=base_model or "",
        fine_tuned_model_id=fine_tuned_model_id,
        status=status,
    )


def activate_model(model_id: str) -> bool:
    """Set a model as the active one for inference."""
    model = get_model(model_id)
    if not model:
        # If it's a fine-tuned model ID directly, register it
        if model_id.startswith("ft:"):
            register_model(
                model_id=model_id,
                fine_tuned_model_id=model_id,
                status="ready",
            )
        else:
            return False

    set_active_model(model_id)
    return True


def deactivate_all():
    """Deactivate all models (fall back to base model)."""
    from training.store import get_connection
    with get_connection() as conn:
        conn.execute("UPDATE models SET is_active = 0")


def main():
    parser = argparse.ArgumentParser(description="Model registry management")
    subparsers = parser.add_subparsers(dest="command")

    # List models
    list_parser = subparsers.add_parser("list", help="List all models")
    list_parser.add_argument("--status", help="Filter by status")

    # Get current
    current_parser = subparsers.add_parser("current", help="Show current model")

    # Set active
    set_parser = subparsers.add_parser("set-active", help="Set active model")
    set_parser.add_argument("model_id", help="Model ID to activate")

    # Deactivate
    deactivate_parser = subparsers.add_parser("deactivate", help="Deactivate all models")

    # Register
    register_parser = subparsers.add_parser("register", help="Register a model")
    register_parser.add_argument("model_id", help="Model ID")
    register_parser.add_argument("--provider", default="openai", help="Provider")
    register_parser.add_argument("--base", help="Base model")
    register_parser.add_argument("--fine-tuned", help="Fine-tuned model ID")
    register_parser.add_argument("--status", default="ready", help="Status")

    # Info
    info_parser = subparsers.add_parser("info", help="Get model info")
    info_parser.add_argument("model_id", help="Model ID")

    args = parser.parse_args()

    if args.command == "list" or args.command is None:
        models = list_models(status=getattr(args, "status", None))
        if not models:
            print("No models registered")
            return

        print(f"{'ID':<40} {'Status':<12} {'Active':<8} {'Fine-tuned Model'}")
        print("-" * 100)
        for m in models:
            active = "→" if m.get("is_active") else ""
            ft_model = m.get("fine_tuned_model_id") or "-"
            print(f"{m['id']:<40} {m.get('status', '-'):<12} {active:<8} {ft_model}")

    elif args.command == "current":
        current = get_current_model()
        active = get_active_model()

        print(f"Current model: {current}")
        if os.getenv("FINETUNED_MODEL_ID"):
            print(f"  (from FINETUNED_MODEL_ID override)")
        elif active:
            print(f"  (from registry, job: {active.get('id')})")
        else:
            print(f"  (from OPENAI_MODEL or default)")

    elif args.command == "set-active":
        if activate_model(args.model_id):
            print(f"Activated: {args.model_id}")
        else:
            print(f"Model not found: {args.model_id}")
            print("Use 'register' command first, or provide a fine-tuned model ID (ft:...)")

    elif args.command == "deactivate":
        deactivate_all()
        print("All models deactivated. Using base model.")

    elif args.command == "register":
        model_id = register_model(
            model_id=args.model_id,
            provider=args.provider,
            base_model=args.base,
            fine_tuned_model_id=args.fine_tuned,
            status=args.status,
        )
        print(f"Registered: {model_id}")

    elif args.command == "info":
        model = get_model(args.model_id)
        if model:
            print(json.dumps(dict(model), indent=2, default=str))
        else:
            print(f"Model not found: {args.model_id}")


if __name__ == "__main__":
    main()
