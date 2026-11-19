import json
from types import SimpleNamespace

import litellm
import pytest

from experiments.scripts.micro_model_usage import model_usage


def test_actual_usage_survives_a_later_failed_request(tmp_path, monkeypatch):
    replies = iter([SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100,
                       completion_tokens=40, total_tokens=140)), RuntimeError("network failed")])
    def completion(**kwargs):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(litellm, "completion", completion)
    path = tmp_path / "usage.json"
    with model_usage(path, 100000):
        litellm.completion(messages=[{"role":"user", "content":"test"}])
        with pytest.raises(RuntimeError):
            litellm.completion(messages=[])
    result = json.loads(path.read_text())
    assert result["known_total_tokens"] == 140
    assert result["total_tokens"] is None
    assert result["complete"] is False
    assert len(result["requests"]) == 2
    assert litellm.completion is completion


def test_budget_blocks_a_request_before_network_and_limits_completion(tmp_path, monkeypatch):
    from sweagent.exceptions import CostLimitExceededError
    calls = []
    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage={"prompt_tokens":19000, "completion_tokens":500,
                                      "total_tokens":19500})
    monkeypatch.setattr(litellm, "completion", completion)
    with model_usage(tmp_path / "usage.json", 20000) as state:
        litellm.completion(messages=[])
        assert 0 < calls[0]["max_completion_tokens"] <= 3616
        with pytest.raises(CostLimitExceededError):
            litellm.completion(messages=[])
    assert len(calls) == 1
    assert state["budget_exhausted"] is True
    assert state["total_tokens"] == 19500


def test_missing_response_usage_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(litellm, "completion", lambda **kwargs: SimpleNamespace(usage=None))
    with model_usage(tmp_path / "usage.json", 100000) as state:
        litellm.completion(messages=[])
    assert state["total_tokens"] is None
    assert state["records"] == 0
