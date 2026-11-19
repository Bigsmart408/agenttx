"""Record actual response usage and bound requests in controlled SWE runs."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import time


@contextmanager
def model_usage(path: Path, token_budget: int | None):
    import litellm
    from sweagent.exceptions import CostLimitExceededError

    original = litellm.completion
    state = dict(source="api_response_usage", token_budget=token_budget,
                 requests=[], budget_exhausted=False, complete=True)

    def persist():
        known = [r for r in state["requests"] if r.get("total_tokens") is not None]
        state["known_total_tokens"] = sum(r["total_tokens"] for r in known)
        state["total_tokens"] = state["known_total_tokens"] if state["complete"] else None
        state["prompt_tokens"] = sum(r.get("prompt_tokens", 0) for r in known)
        state["completion_tokens"] = sum(r.get("completion_tokens", 0) for r in known)
        state["records"] = len(known)
        state["budget_exceeded"] = bool(token_budget is not None and
                                        state["known_total_tokens"] > token_budget)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n")
        temporary.replace(path)

    def completion(*args, **kwargs):
        # This experiment requests one completion at a time. Reserve the UTF-8
        # request bytes plus 16K tokens for framing before permitting a call.
        # Preserve every returned usage record and explicitly flag any overrun.
        known_total = sum(r.get("total_tokens") or 0 for r in state["requests"])
        public_input = {k: kwargs[k] for k in ("messages", "tools", "response_format") if k in kwargs}
        reserve = len(json.dumps(public_input, ensure_ascii=False).encode()) + 16384
        if token_budget is not None:
            allowance = token_budget - known_total - reserve
            if allowance <= 0:
                state["budget_exhausted"] = True
                persist()
                raise CostLimitExceededError("Registered total API-token budget exhausted")
            maximum = min(32768, allowance,
                          int(kwargs.get("max_completion_tokens") or kwargs.get("max_tokens") or 32768))
            kwargs.pop("max_tokens", None)
            kwargs["max_completion_tokens"] = maximum
        record = dict(index=len(state["requests"]), started_at=time.time(),
                      input_reservation=reserve,
                      max_completion_tokens=kwargs.get("max_completion_tokens"))
        state["requests"].append(record)
        try:
            response = original(*args, **kwargs)
        except BaseException as exc:
            record.update(error_type=type(exc).__name__, total_tokens=None)
            state["complete"] = False
            persist()
            raise
        usage = getattr(response, "usage", None)
        def field(name):
            return usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        values = {k: field(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        if any(not isinstance(v, int) for v in values.values()):
            record["total_tokens"] = None
            state["complete"] = False
        else:
            record.update(values)
        record["finished_at"] = time.time()
        persist()
        return response

    persist()
    litellm.completion = completion
    try:
        yield state
    finally:
        litellm.completion = original
        persist()
