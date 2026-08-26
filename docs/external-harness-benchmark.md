# External harness contract for application benchmarks

The application benchmark uses the same workload set for every agent stack:
SWE-Bench Lite and Terminal-Bench.  AgentTX does not emulate an agent loop for
these runs.  It starts the selected harness as a black-box process in the
protected workspace and records the resulting filesystem effects through the
normal transaction ledger.

Supported harnesses are:

* `deepseek_harness`: the DeepSeek Harness headless profile
  (`dsh --profile headless <task>`), with the model defaulting to
  `deepseek-v4-flash`;
* `codex`: the official Codex CLI (`codex exec --model gpt-5.6-luna
  --dangerously-bypass-approvals-and-sandbox --json <task>`).  The CLI's
  inner sandbox is bypassed because AgentTX supplies the outer transaction
  sandbox; this also avoids an interactive approval protocol in batch runs.

For this evaluation round the defaults are fixed to the inexpensive tiers:
`deepseek-v4-flash` and `gpt-5.6-luna`. An explicit `--model` is reserved for
an intentionally separate model sweep.

The benchmark driver has no implicit legacy fallback.  A missing binary,
missing API key, missing harness checkout, or failed proxy preflight is a
configuration error.  Run `--preflight-only` before spending model calls.

## Environment and proxy

`agenttx.providers.load_provider_env()` reads, in order, an explicit
`AGENTTX_ENV_FILE`, the repository's `.agent.env`/`.env`, the user's
`.agenttx_llm.env`, and `/home/pengpeng/.agenttx_llm.env`.  It uses
`setdefault`, so an explicitly exported variable wins.  The file contents are
never logged.  `DEEPSEEK_API_KEY` and `DEEPSEEK_MODEL` configure DeepSeek;
`OPENAI_API_KEY` and `OPENAI_MODEL` configure Codex.

If `agentTX-clash` is present, the adapters invoke every external process as
`agentTX-clash run -- ...`, which supplies the HTTP/SOCKS proxy without putting
proxy logic in the harness implementation.  Set `AGENTTX_CLASH_COMMAND` to a
different launcher for another machine.

## Boundary and metrics

The adapter intentionally exposes one `external_task` transaction boundary:
the selected harness owns all internal turns, tool calls, retries, and final
answer semantics.  The ledger therefore remains sound for filesystem effects,
but it cannot yet claim per-turn causal edges from a harness that only exposes
final text. Codex's JSON stream is parsed when available. DeepSeek's headless
profile persists usage in compressed or plain session JSONL; the adapter reads
those files from the protected upperdir and de-duplicates chunk/message copies
per turn and step. It reports input plus cache-read/cache-write tokens as
prompt usage and output tokens separately. p50/p95/p99 aggregates are written
to `official_token_summary.csv`, together with the contributing
`usage_sources`. This distinction is encoded in every row as
`harness_backend` and `execution_boundary` instead of being hidden in a legacy
result directory.

The next extension point is `ExternalHarness.run_in_transaction`: an event
bridge can map harness turn events to `tx.run_tool` calls while retaining the
same adapter and benchmark schema.
