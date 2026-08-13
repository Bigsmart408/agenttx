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
cd /home/pengpeng/agenttx
./scripts/agenttx-agent llm --workdir /tmp/ws --task "add hello.py" --dump-ledger /tmp/ledger.json
```

## Aider baseline (not intercepted)

```bash
./scripts/agenttx-agent aider --workdir /tmp/ws --task "add a README"
```

## Live result (DeepSeek)

`experiments/scripts/demo_live_agent.py` ran successfully:
- tool calls intercepted into AgentTX ledger
- host unchanged until `try commit`
- after commit, `mul` appeared on host

```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate agenttx
. ~/.agenttx_llm.env
cd /home/pengpeng/agenttx
PYTHONPATH=src python experiments/scripts/demo_live_agent.py
```

## Refactor comparison

`experiments/scripts/bench_refactor_compare.py` runs AgentTX-LLM vs Aider on a multi-file calc refactor.

```bash
source ~/.agenttx_llm.env
export PATH="$HOME/miniconda3/envs/agenttx/bin:$PATH"
AIDER_TIMEOUT_S=180 python experiments/scripts/bench_refactor_compare.py
```

Results land in `experiments/results/refactor_agent_compare.{csv,md}`.
