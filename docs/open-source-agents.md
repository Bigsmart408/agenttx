# Open-source agents on AgentTX

## Installed

- **Aider 0.86.2** in conda env `agenttx` (Python 3.11)
- **AgentTX-native LLM tool agent** (`src/agenttx/agents/llm_agent.py`) — tools go through harness

## Setup

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate agenttx
export OPENAI_API_KEY=...
# optional OpenRouter:
# export OPENAI_BASE_URL=https://openrouter.ai/api/v1
# export AGENTTX_MODEL=openai/gpt-4o-mini
```

## Intercepted agent (for experiments)

```bash
cd /home/bfq/agenttx
./scripts/agenttx-agent llm --workdir /tmp/ws --task "add hello.py" --dump-ledger /tmp/ledger.json
```

## Aider baseline (not intercepted)

```bash
./scripts/agenttx-agent aider --workdir /tmp/ws --task "add a README"
```