"""Named LLM provider profiles for repeatable agent runs."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    api_key: str
    base_url: Optional[str]
    model: str


_DEFAULTS = {
    "deepseek": {
        "key": "DEEPSEEK_API_KEY",
        "base": "DEEPSEEK_BASE_URL",
        "model": "DEEPSEEK_MODEL",
        "default_base": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
    },
    "openai": {
        "key": "OPENAI_API_KEY",
        "base": "OPENAI_BASE_URL",
        "model": "OPENAI_MODEL",
        "default_base": "https://api.openai.com/v1",
        "default_model": "gpt-5.6-luna",
    },
    "openrouter": {
        "key": "OPENROUTER_API_KEY",
        "base": "OPENROUTER_BASE_URL",
        "model": "OPENROUTER_MODEL",
        "default_base": "https://openrouter.ai/api/v1",
        "default_model": "deepseek/deepseek-v4-flash",
    },
    "anthropic": {
        "key": "ANTHROPIC_API_KEY",
        "base": "ANTHROPIC_BASE_URL",
        "model": "ANTHROPIC_MODEL",
        "default_base": "https://jojocode.com",
        "default_model": "claude-fable-5",
    },
    "jojocode": {
        "key": "JOJOCODE_GPT_API_KEY",
        "base": "JOJOCODE_GPT_BASE_URL",
        "model": "JOJOCODE_GPT_MODEL",
        "default_base": "https://jojocode.com/v1",
        "default_model": "gpt-5.6-sol",
    },
}


def provider_names() -> tuple[str, ...]:
    return tuple(_DEFAULTS)


def load_provider_env(root: Optional[Path] = None) -> Optional[Path]:
    """Load the shared profile file without printing or persisting secrets."""
    candidates = []
    if os.environ.get("AGENTTX_ENV_FILE"):
        candidates.append(Path(os.environ["AGENTTX_ENV_FILE"]))
    project_root = Path(root or Path.cwd()).resolve()
    candidates.extend((project_root / ".agent.env", project_root / ".env"))
    candidates.append(Path.home() / ".agenttx_llm.env")
    candidates.append(Path("/home/pengpeng/.agenttx_llm.env"))
    for envfile in candidates:
        if not envfile.is_file():
            continue
        for line in envfile.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export ") :]
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))
        return envfile
    return None


def resolve_provider(name: Optional[str] = None, root: Optional[Path] = None) -> ProviderProfile:
    load_provider_env(root)
    selected = (name or os.environ.get("AGENTTX_PROVIDER") or "deepseek").lower()
    if selected not in _DEFAULTS:
        raise ValueError(
            f"unknown provider {selected!r}; choose one of {', '.join(provider_names())}"
        )
    config = _DEFAULTS[selected]
    key = os.environ.get(config["key"], "")
    if selected == "openrouter" and not key:
        key = os.environ.get("OPENROUTER_KEY", "")
    if selected == "anthropic" and not key:
        key = os.environ.get("JOJOCODE_API_KEY", "") or os.environ.get("NEW_API_KEY", "")
    base = os.environ.get(config["base"], config["default_base"])
    if selected == "openai" and not base:
        base = os.environ.get("OPENAI_API_BASE") or config["default_base"]
    model = os.environ.get(config["model"], config["default_model"])
    return ProviderProfile(selected, key, base, model)


def configured_provider(name: Optional[str] = None) -> bool:
    return bool(resolve_provider(name).api_key)


def provider_result_dir(root: Path, name: Optional[str] = None) -> Path:
    return Path(root) / "experiments" / "results" / resolve_provider(name).name
