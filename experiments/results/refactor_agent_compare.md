# AgentTX-LLM vs Aider (multi-file refactor)

| mode | wall_s | tool_calls | polluted_before_commit | commit_ok | tests_rc | timed_out |
|---|---:|---:|---|---|---:|---|
| agenttx_llm | 14.4 | 15 | False | True | 0 | False |
| aider_baseline | 40.7 | None | True | True | 2 | False |

## Notes

- Same DeepSeek chat model for both paths.
- AgentTX: speculative edits in shared semisolate; host markers absent until policy-gated commit; pytest passed after commit.
- Aider: `--yes-always --no-git --no-stream --map-tokens 0` (previous `--yes` run hung on interactive git init). Host polluted immediately; apply ended with `list index out of range`; pytest failed (`tests_rc=2`).
- CSV: `refactor_agent_compare.csv`; AgentTX ledger: `refactor_agenttx_ledger.json`.

