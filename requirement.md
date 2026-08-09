# AgentTX Requirements

This document lists dependencies required by the current codebase.
Install only what you need for the workload you are running.
All development and runtime work is expected on Linux (VM); the core
isolation path will not run on Windows.

Versions below are the ones currently validated in the `agenttx` conda
environment on the research VM. Pins are lower bounds unless noted.

## 1. Platform / system packages

| Dependency | Why | Used by |
|---|---|---|
| Linux (user namespaces + overlayfs) | Shared semisolate / `try` sandbox | `src/agenttx/semisolate.py`, `third_party/try` |
| `strace` | Default automatic READ / NEGATIVE dependency tracing | `src/agenttx/trace.py`, `SharedSemisolate` |
| `git` | Bootstrap / clone `try`, some experiment helpers | `scripts/bootstrap.sh` |
| `make` / build tools | Building vendored `try` if the binary is missing | `third_party/try` |
| `python >= 3.11` | Runtime language | whole repo |

Optional but commonly present on the VM:

- `conda` / `miniconda` — environment management
- `ipython` — running motivation notebooks via `%run`

Bootstrap:

```bash
./scripts/bootstrap.sh
# ensure third_party/try is built and on PATH, e.g. via scripts/try-wrapper.sh
command -v strace
```

`README.md` notes that default sessions fail closed when `strace` is
missing. Experiments that intentionally measure the untraced mode can
start with `agenttx begin --no-trace-reads`.

## 2. Core runtime (AgentTX library)

The core package under `src/agenttx/` is **stdlib-only** for ledger,
semisolate, WAL, policy, harness, and CLI paths. No `pip` packages are
required to import and run:

- `agenttx.runtime`, `agenttx.ledger`, `agenttx.semisolate`
- `agenttx.harness`, `agenttx.policy`, `agenttx.trace`, `agenttx.commit_wal`

Install the package itself on `PYTHONPATH` (editable / path install):

```bash
export PYTHONPATH=/home/bfq/agenttx/src:$PYTHONPATH
# or: pip install -e .   # if/when a pyproject is added
```

## 3. LLM coding agent (optional)

Needed only for the OpenAI-compatible agent and Aider comparison baseline.

| Package | Version | Why |
|---|---|---|
| `openai` | `>=1.0` (validated `2.20.0`) | Lazy-imported by `src/agenttx/agents/llm_agent.py` |
| `aider-chat` | `>=0.86` (validated `0.86.2`) | Baseline in `experiments/scripts/bench_refactor_compare.py` |
| `litellm` | `>=1.80` (validated `1.81.10`) | Pulled by the agent tooling stack / `requirements-agent.txt` |

Install:

```bash
pip install -r requirements-agent.txt
```

Environment variables for the LLM agent:

- `OPENAI_API_KEY` or `OPENROUTER_API_KEY`
- optional `OPENAI_BASE_URL` / agent `--api-base` (DeepSeek / OpenRouter compatible endpoints)

## 4. Experiments and tests

| Package | Version | Why |
|---|---|---|
| `pytest` | `>=8` (validated `9.1.1`) | `tests/` suite |

```bash
pip install pytest
pytest -q
```

Experiment scripts under `experiments/scripts/` otherwise rely on the
stdlib plus the AgentTX package on `PYTHONPATH`. The Aider comparison
additionally needs Section 3.

## 5. Motivation notebooks / figures

Needed to execute notebooks under `motivation/` and regenerate
`FIG-Motivation-*.{pdf,png}`.

| Package | Version | Why |
|---|---|---|
| `matplotlib` | `>=3.8` (validated `3.11.1`) | Plotting style / figure export |
| `pandas` | `>=2.0` (validated `3.0.5`) | CSV / JSON result tables |
| `numpy` | `>=1.26` (validated `1.26.4`) | Array helpers in plot notebooks |
| `ipython` | `>=8` (validated `9.16.1`) | `%run plot*.ipynb` |
| `nbformat` | `>=5` (validated `5.11.0`) | Notebook structure checks / tests |

Recommended system font for the FAST-style notebooks:

- `Nimbus Roman` (or another Times-compatible serif available to matplotlib)

```bash
pip install matplotlib pandas numpy ipython nbformat
cd motivation
MPLBACKEND=Agg ipython -c "%run plot.ipynb"
```

## 6. Suggested install profiles

### A. Minimal correctness path (core + tests)

```bash
# system
sudo apt-get install -y strace   # or distro equivalent
./scripts/bootstrap.sh
# python
pip install pytest
export PYTHONPATH=$PWD/src:$PYTHONPATH
pytest -q
```

### B. Real LLM agent / Aider baseline

```bash
pip install -r requirements-agent.txt
export OPENAI_API_KEY=...
```

### C. Motivation figures

```bash
pip install matplotlib pandas numpy ipython nbformat
```

### D. Full research VM (matches current `agenttx` conda env)

```bash
pip install -r requirements-agent.txt
pip install pytest matplotlib pandas numpy ipython nbformat
```

## 7. Non-Python / vendored dependencies

| Item | Location | Notes |
|---|---|---|
| `binpash/try` | `third_party/try` | Vendored / cloned by `scripts/bootstrap.sh`; required for sandbox execution |
| Overlay filesystem support | kernel | Required by `try` upperdir / shared overlay |
| User namespaces | kernel / sysctl | Required by `try` unprivileged isolation |

## 8. Out of scope for this file

- Model API credits / keys (not installable packages)
- Paper / LaTeX toolchain
- Windows runtime support (explicitly unsupported for AgentTX isolation)

## 9. Source of truth for pip pins already in-repo

- `requirements-agent.txt` — agent / baseline LLM packages
- this file (`requirement.md`) — full dependency map across core, system,
  experiments, and notebooks
