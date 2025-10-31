import json
import types

import llm_coach


def test_coach_move_with_llm_returns_llm_source(monkeypatch):
    fake_response = {
        "basic": "Test basic guidance.",
        "extended": "Detailed extended coaching text for the player.",
    }

    class FakeCompletions:
        def create(self, **kwargs):
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            content=json.dumps(fake_response)
                        )
                    )
                ]
            )

    class FakeClient:
        def __init__(self):
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    import openai

    monkeypatch.setattr(openai, "OpenAI", lambda *args, **kwargs: FakeClient())

    move_payload = {
        "san": "e4",
        "cp_loss": 0.8,
        "best_move_san": "e4",
        "fen_before": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        "side": "white",
        "multipv": [
            {
                "move_san": "e4",
                "move_uci": "e2e4",
                "cp": 20,
                "mate": None,
                "line_san": ["e4", "e5", "Nf3", "Nc6"],
            }
        ],
    }

    result = llm_coach.coach_move_with_llm(move_payload, level="intermediate")

    assert result["source"] == "llm"
    assert result["basic"] == fake_response["basic"]
    assert result["extended"].startswith("Detailed extended coaching")
