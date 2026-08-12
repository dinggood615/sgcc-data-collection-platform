import io
import json

from app.sgcc import local_model


class _Response:
    status = 200

    def __init__(self, body=b"{}"):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def test_high_and_low_rule_scores_skip_model(monkeypatch):
    monkeypatch.setenv("LOCAL_MODEL_ENABLED", "1")
    monkeypatch.setattr(local_model, "_ensure_service", lambda *_args: (_ for _ in ()).throw(AssertionError()))
    assert local_model.review_candidate("text", 10) is None
    assert local_model.review_candidate("text", 80) is None


def test_model_review_parses_validated_json(monkeypatch):
    content = json.dumps({"relevant": True, "confidence": 86, "category": "软件实施", "reason": "原文包含系统实施"}, ensure_ascii=False)
    response = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=False).encode()
    monkeypatch.setattr(local_model, "_ensure_service", lambda *_args: True)
    monkeypatch.setattr(local_model, "urlopen", lambda *_args, **_kwargs: _Response(response))
    review = local_model.review_candidate("系统实施服务", 50)
    assert review is not None
    assert review.relevant is True
    assert review.confidence == 86
    assert review.model == "qwen3-0.6b"
    assert local_model.combined_score(50, review) == 63


def test_model_failure_falls_back_to_rule_score(monkeypatch):
    monkeypatch.setattr(local_model, "_ensure_service", lambda *_args: True)
    monkeypatch.setattr(local_model, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()))
    assert local_model.review_candidate("系统实施服务", 50) is None
    assert local_model.combined_score(50, None) == 50
