"""Discover current OpenRouter chat models for AgentTX tool-calling runs."""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Dict, Iterable, List, Optional, Sequence

from agenttx.providers import load_provider_env, resolve_provider

MAJOR_VENDORS = (
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "meta-llama",
    "qwen",
    "x-ai",
    "mistralai",
    "amazon",
    "moonshotai",
    "minimax",
    "z-ai",
)

# Families that are no longer the current generation on OpenRouter.
LEGACY_SUBSTR = (
    "gpt-3.5",
    "gpt-4-",
    "gpt-4o",
    "gpt-4.1",
    "o1-",
    "o3-",
    "o4-mini",
    "claude-3",
    "claude-opus-4",
    "claude-sonnet-4",
    "claude-haiku-4",
    "llama-3",
    "llama-4-maverick",
    "gemini-1",
    "gemini-2",
    "qwen-2",
    "qwen2.",
    "qwen3-14b",
    "qwen3-32b",
    "qwen3-72b",
    "qwen3-235b-a22b",
    "deepseek-chat",
    "deepseek-r1",
    "deepseek-v3",
    "mistral-large-2407",
    "mistral-large-2411",
    "command-r",
    "nova-lite-v1",
    "nova-micro-v1",
    "nova-pro-v1",
    "nova-premier-v1",
    "kimi-k2-0905",
    "kimi-k2-thinking",
    "minimax-m1",
    "minimax-m2",
    "glm-4.5",
    "glm-4.6",
    ":free",
    ":batch",
    ":preview",
    "-exp",
    "vision",
    ":thinking",
)

SKIP_SUBSTR = (
    "embed",
    "whisper",
    "tts",
    "moderation",
    "rerank",
    "transcri",
    "codec",
    ":batch",
    "guard",
    "safety",
    "voxtral",
)

PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Availability probe",
        "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    },
}


def _key_and_base() -> tuple[str, str]:
    load_provider_env()
    profile = resolve_provider("openrouter")
    if not profile.api_key:
        raise RuntimeError("OPENROUTER_API_KEY / OPENROUTER_KEY is not set")
    return profile.api_key, (profile.base_url or "https://openrouter.ai/api/v1").rstrip("/")


def _headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("OPENROUTER_HTTP_REFERER", "https://openrouter.ai"),
        "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "AgentTX"),
        "User-Agent": "agenttx-openrouter-catalog",
    }


def list_models() -> List[dict]:
    api_key, base = _key_and_base()
    req = urllib.request.Request(f"{base}/models", headers=_headers(api_key))
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())
    return list(payload.get("data") or [])


def supports_tools(model: dict) -> bool:
    params = model.get("supported_parameters") or []
    return "tools" in params or "tool_choice" in params


def is_text_model(model: dict) -> bool:
    arch = model.get("architecture") or {}
    outputs = arch.get("output_modalities")
    if isinstance(outputs, list) and outputs:
        return "text" in outputs
    modality = str(arch.get("modality") or "text")
    return "text" in modality


def is_legacy(model_id: str) -> bool:
    low = model_id.lower()
    return any(bit in low for bit in LEGACY_SUBSTR + SKIP_SUBSTR)


def is_current_mainstream(model: dict, vendors: Sequence[str] = MAJOR_VENDORS) -> bool:
    mid = str(model.get("id") or "")
    vendor = mid.split("/", 1)[0]
    if vendor not in vendors:
        return False
    if is_legacy(mid):
        return False
    return supports_tools(model) and is_text_model(model)


def catalog_records() -> List[dict]:
    rows = []
    for model in list_models():
        if not is_current_mainstream(model):
            continue
        mid = model["id"]
        rows.append(
            {
                "id": mid,
                "vendor": mid.split("/", 1)[0],
                "created": int(model.get("created") or 0),
                "name": model.get("name") or mid,
            }
        )
    rows.sort(key=lambda r: (r["vendor"], -r["created"], r["id"]))
    return rows


def catalog_mainstream() -> List[str]:
    return [row["id"] for row in catalog_records()]


def prefer_latest(records: Sequence[dict], per_vendor: int = 2, limit: Optional[int] = None) -> List[str]:
    """Keep the newest 1-2 tool-capable models per vendor."""
    by_vendor: Dict[str, List[dict]] = {}
    for row in records:
        by_vendor.setdefault(row["vendor"], []).append(row)
    ordered: List[str] = []
    for vendor in MAJOR_VENDORS:
        bucket = sorted(by_vendor.get(vendor) or [], key=lambda r: (-r["created"], r["id"]))
        for row in bucket[:per_vendor]:
            ordered.append(row["id"])
    if limit is None:
        return ordered
    return ordered[: max(0, int(limit))]


def prefer_diverse(model_ids: Sequence[str], limit: Optional[int] = None) -> List[str]:
    # Back-compat name used by the matrix runner; now means latest-per-vendor.
    records = [{"id": mid, "vendor": mid.split("/", 1)[0], "created": 0} for mid in model_ids]
    return prefer_latest(records, per_vendor=2, limit=limit)


def probe_model(model_id: str, timeout_s: float = 25.0) -> dict:
    api_key, base = _key_and_base()
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply with a ping tool call."}],
        "tools": [PROBE_TOOL],
        "tool_choice": "auto",
        "max_tokens": 32,
        "provider": {"require_parameters": True, "allow_fallbacks": True},
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=data,
        headers=_headers(api_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode())
        err = payload.get("error")
        if err:
            return {"id": model_id, "ok": False, "error": str(err.get("message") or err)}
        return {"id": model_id, "ok": True, "error": ""}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        raw = b""
        if hasattr(exc, "read"):
            try:
                raw = exc.read()  # type: ignore[misc]
            except Exception:
                raw = b""
        if raw:
            try:
                parsed = json.loads(raw.decode())
                msg = str(parsed.get("error", {}).get("message") or parsed)
            except Exception:
                msg = raw.decode(errors="replace")[:300]
        return {"id": model_id, "ok": False, "error": msg[:300]}


def probe_models(model_ids: Iterable[str], timeout_s: float = 25.0) -> List[dict]:
    return [probe_model(mid, timeout_s=timeout_s) for mid in model_ids]
