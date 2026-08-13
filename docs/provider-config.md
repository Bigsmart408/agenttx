# Multi-provider LLM configuration

Store all provider credentials in `/home/pengpeng/.agenttx_llm.env` (mode 600).
The agent never prints or serializes the key. Select the provider per run:

```bash
source /home/pengpeng/.agenttx_llm.env
python experiments/scripts/bench_token_end_to_end.py --provider deepseek
python experiments/scripts/bench_token_end_to_end.py --provider openai
python experiments/scripts/bench_token_end_to_end.py --provider openrouter
```

Profiles are independent:

| provider | key | default model | default base URL |
|---|---|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-chat` | `https://api.deepseek.com` |
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | OpenAI SDK default |
| `openrouter` | `OPENROUTER_API_KEY` | `deepseek/deepseek-chat` | `https://openrouter.ai/api/v1` |

Use `--model` to override a profile's default. Benchmark result rows include
the selected provider and model, so multi-model sweeps remain distinguishable.
