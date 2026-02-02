"""Tests for training infrastructure."""

import os
import sys
import json
import tempfile
import pytest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # Set env var before importing store
    os.environ["TRAINING_DB_PATH"] = db_path

    yield db_path

    # Cleanup
    os.unlink(db_path)


class TestStore:
    """Tests for training.store module."""

    def test_init_db(self, temp_db):
        """Test database initialization."""
        # Force reimport with new env
        import importlib
        import training.store as store
        importlib.reload(store)

        # Should create tables without error
        store.init_db()

        # Verify tables exist
        with store.get_connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in cursor.fetchall()}

        assert "coaching_samples" in tables
        assert "models" in tables

    def test_insert_and_get_sample(self, temp_db):
        """Test inserting and retrieving a sample."""
        import importlib
        import training.store as store
        importlib.reload(store)

        sample_id = store.insert_sample(
            fen_before="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
            san="e4",
            best_move_san="e4",
            cp_loss=0.0,
            side="white",
            multipv=[{"move_san": "e4", "cp": 20}],
            player_level="intermediate",
            severity="best",
            coaching_response="Solid opening move.",
            source="llm",
            model_used="gpt-4o-mini",
            latency_ms=150,
        )

        assert sample_id is not None
        assert sample_id > 0

        # Retrieve
        samples = store.get_samples(limit=1)
        assert len(samples) == 1
        assert samples[0]["san"] == "e4"
        assert samples[0]["source"] == "llm"

    def test_sample_filtering(self, temp_db):
        """Test sample filtering by quality and source."""
        import importlib
        import training.store as store
        importlib.reload(store)

        # Insert samples with different sources
        store.insert_sample(
            fen_before=None, san="e4", best_move_san="e4",
            cp_loss=0.0, side="white", multipv=None,
            player_level="beginner", severity="best",
            coaching_response="Good move.",
            source="llm", model_used="gpt-4", latency_ms=100,
        )
        store.insert_sample(
            fen_before=None, san="d4", best_move_san="e4",
            cp_loss=0.1, side="white", multipv=None,
            player_level="beginner", severity="good",
            coaching_response="Solid move.",
            source="rules", model_used="gpt-4", latency_ms=0,
        )

        # Filter by source
        llm_samples = store.get_samples(source="llm")
        rules_samples = store.get_samples(source="rules")

        assert len(llm_samples) == 1
        assert len(rules_samples) == 1
        assert llm_samples[0]["san"] == "e4"
        assert rules_samples[0]["san"] == "d4"

    def test_quality_rating(self, temp_db):
        """Test quality rating updates."""
        import importlib
        import training.store as store
        importlib.reload(store)

        sample_id = store.insert_sample(
            fen_before=None, san="e4", best_move_san="e4",
            cp_loss=0.0, side="white", multipv=None,
            player_level="beginner", severity="best",
            coaching_response="Good move.",
            source="llm", model_used="gpt-4", latency_ms=100,
        )

        # Update quality
        store.update_sample_quality(sample_id, 5, "Excellent response")

        # Filter by quality
        high_quality = store.get_samples(min_quality=4)
        assert len(high_quality) == 1
        assert high_quality[0]["quality_rating"] == 5

    def test_flagging(self, temp_db):
        """Test sample flagging."""
        import importlib
        import training.store as store
        importlib.reload(store)

        sample_id = store.insert_sample(
            fen_before=None, san="e4", best_move_san="e4",
            cp_loss=0.0, side="white", multipv=None,
            player_level="beginner", severity="best",
            coaching_response="Bad response to flag.",
            source="llm", model_used="gpt-4", latency_ms=100,
        )

        # Flag it
        store.flag_sample(sample_id, True, "Low quality")

        # Should be excluded by default
        samples = store.get_samples()
        assert len(samples) == 0

        # Include flagged
        samples = store.get_samples(exclude_flagged=False)
        assert len(samples) == 1

    def test_model_registry(self, temp_db):
        """Test model registry operations."""
        import importlib
        import training.store as store
        importlib.reload(store)

        # Insert model
        model_id = store.insert_model(
            model_id="job-123",
            provider="openai",
            base_model="gpt-4o-mini",
            status="training",
        )

        assert model_id == "job-123"

        # Get model
        model = store.get_model("job-123")
        assert model is not None
        assert model["status"] == "training"

        # Update status
        store.update_model_status("job-123", "ready", {"accuracy": 0.95})

        model = store.get_model("job-123")
        assert model["status"] == "ready"

    def test_active_model(self, temp_db):
        """Test active model management."""
        import importlib
        import training.store as store
        importlib.reload(store)

        store.insert_model("model-1", "openai", "gpt-4o-mini", status="ready")
        store.insert_model("model-2", "openai", "gpt-4o-mini", status="ready")

        # No active model initially
        active = store.get_active_model()
        assert active is None

        # Set active
        store.set_active_model("model-1")
        active = store.get_active_model()
        assert active is not None
        assert active["id"] == "model-1"

        # Change active
        store.set_active_model("model-2")
        active = store.get_active_model()
        assert active["id"] == "model-2"


class TestExport:
    """Tests for training.export module."""

    def test_sample_to_messages(self, temp_db):
        """Test converting sample to message format."""
        import importlib
        import training.store as store
        import training.export as export
        importlib.reload(store)
        importlib.reload(export)

        sample = {
            "san": "e4",
            "best_move_san": "e4",
            "cp_loss": 0.0,
            "side": "white",
            "multipv_json": json.dumps([{"move_san": "e4", "cp": 20}]),
            "player_level": "intermediate",
            "coaching_response": "Solid move.",
        }

        messages = export.sample_to_messages(sample)

        assert "messages" in messages
        assert len(messages["messages"]) == 3
        assert messages["messages"][0]["role"] == "system"
        assert messages["messages"][1]["role"] == "user"
        assert messages["messages"][2]["role"] == "assistant"

        # Check assistant response is valid JSON
        assistant_content = messages["messages"][2]["content"]
        parsed = json.loads(assistant_content)
        assert parsed["basic"] == "Solid move."

    def test_export_to_jsonl(self, temp_db):
        """Test exporting samples to JSONL."""
        import importlib
        import training.store as store
        import training.export as export
        importlib.reload(store)
        importlib.reload(export)

        # Insert samples
        for i in range(5):
            store.insert_sample(
                fen_before=None, san=f"move{i}", best_move_san="e4",
                cp_loss=0.0, side="white", multipv=None,
                player_level="beginner", severity="best",
                coaching_response=f"Response {i}",
                source="llm", model_used="gpt-4", latency_ms=100,
            )

        # Export
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            output_path = f.name

        try:
            count = export.export_to_jsonl(output_path)
            assert count == 5

            # Validate file
            with open(output_path) as f:
                lines = f.readlines()
            assert len(lines) == 5

            # Each line should be valid JSON
            for line in lines:
                obj = json.loads(line)
                assert "messages" in obj
        finally:
            os.unlink(output_path)

    def test_validate_jsonl(self, temp_db):
        """Test JSONL validation."""
        import training.export as export

        # Create valid JSONL
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({
                "messages": [
                    {"role": "system", "content": "You are a coach."},
                    {"role": "user", "content": "Move data"},
                    {"role": "assistant", "content": '{"basic": "Good move."}'},
                ]
            }) + "\n")
            output_path = f.name

        try:
            result = export.validate_jsonl(output_path)
            assert result["valid"] is True
            assert result["stats"]["valid_lines"] == 1
            assert len(result["errors"]) == 0
        finally:
            os.unlink(output_path)

    def test_validate_jsonl_invalid(self, temp_db):
        """Test JSONL validation catches errors."""
        import training.export as export

        # Create invalid JSONL
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("not valid json\n")
            f.write(json.dumps({"wrong": "structure"}) + "\n")
            output_path = f.name

        try:
            result = export.validate_jsonl(output_path)
            assert result["valid"] is False
            assert len(result["errors"]) == 2
        finally:
            os.unlink(output_path)


class TestRegistry:
    """Tests for training.registry module."""

    def test_get_current_model_default(self, temp_db):
        """Test getting current model with no override."""
        import importlib
        import training.store as store
        import training.registry as registry
        importlib.reload(store)
        importlib.reload(registry)

        # Clear any env override
        os.environ.pop("FINETUNED_MODEL_ID", None)
        os.environ["OPENAI_MODEL"] = "test-model"

        model = registry.get_current_model()
        assert model == "test-model"

    def test_get_current_model_override(self, temp_db):
        """Test FINETUNED_MODEL_ID override."""
        import importlib
        import training.store as store
        import training.registry as registry
        importlib.reload(store)
        importlib.reload(registry)

        os.environ["FINETUNED_MODEL_ID"] = "ft:gpt-4:override"

        model = registry.get_current_model()
        assert model == "ft:gpt-4:override"

        # Cleanup
        del os.environ["FINETUNED_MODEL_ID"]

    def test_activate_model(self, temp_db):
        """Test activating a model."""
        import importlib
        import training.store as store
        import training.registry as registry
        importlib.reload(store)
        importlib.reload(registry)

        # Clear override
        os.environ.pop("FINETUNED_MODEL_ID", None)

        # Register and activate
        store.insert_model(
            "job-1", "openai", "gpt-4o-mini",
            fine_tuned_model_id="ft:gpt-4o-mini:org::123",
            status="ready",
        )

        registry.activate_model("job-1")

        current = registry.get_current_model()
        assert current == "ft:gpt-4o-mini:org::123"


class TestCollector:
    """Tests for training.collector module."""

    @pytest.mark.asyncio
    async def test_collection_disabled(self, temp_db):
        """Test that collection is skipped when disabled."""
        import importlib
        import training.store as store
        importlib.reload(store)

        os.environ["ENABLE_TRAINING_COLLECTION"] = "0"

        import training.collector as collector
        importlib.reload(collector)

        result = await collector.log_coaching_sample(
            move={"san": "e4"},
            level="beginner",
            response={"basic": "Good.", "source": "llm"},
            model="gpt-4",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_collection_enabled(self, temp_db):
        """Test that collection works when enabled."""
        import importlib
        import training.store as store
        importlib.reload(store)

        os.environ["ENABLE_TRAINING_COLLECTION"] = "1"

        import training.collector as collector
        importlib.reload(collector)

        result = await collector.log_coaching_sample(
            move={"san": "e4", "fen_before": "start", "side": "white"},
            level="beginner",
            response={"basic": "Good.", "source": "llm"},
            model="gpt-4",
            latency_ms=100,
        )

        assert result is not None
        assert result > 0

        # Verify in database
        samples = store.get_samples()
        assert len(samples) == 1
        assert samples[0]["san"] == "e4"

        # Cleanup
        os.environ["ENABLE_TRAINING_COLLECTION"] = "0"
