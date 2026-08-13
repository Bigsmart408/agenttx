from pathlib import Path

from agenttx.providers import configured_provider, resolve_provider


def test_named_provider_profiles_are_independent(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-key")
    monkeypatch.setenv("OPENAI_API_KEY", "open-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test")
    monkeypatch.setenv("OPENAI_MODEL", "openai-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "router-test")

    deepseek = resolve_provider("deepseek")
    openai = resolve_provider("openai")
    router = resolve_provider("openrouter")

    assert (deepseek.api_key, deepseek.model) == ("deep-key", "deepseek-test")
    assert (openai.api_key, openai.model) == ("open-key", "openai-test")
    assert (router.api_key, router.model) == ("router-key", "router-test")
    assert configured_provider("deepseek")
    assert configured_provider("openai")
    assert configured_provider("openrouter")


def test_provider_env_file_is_loaded_without_printing_or_requiring_home(
    monkeypatch, tmp_path: Path
):
    envfile = tmp_path / "providers.env"
    envfile.write_text(
        "export DEEPSEEK_API_KEY=file-deep\n"
        "OPENAI_MODEL=file-open-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTTX_ENV_FILE", str(envfile))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    assert resolve_provider("deepseek").api_key == "file-deep"
    assert resolve_provider("openai").model == "file-open-model"
