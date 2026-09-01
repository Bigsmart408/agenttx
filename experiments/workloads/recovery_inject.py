"""Shared inject–policy–repair DAG for application-workload recovery.

Official SWE-Bench / Terminal-Bench instances provide the workspace and the
utility predicate.  This module overlays an *agent logical fault*, not an
infrastructure SIGKILL: independent notes before the fault, one faulty
producer, later independent notes that do not depend on the fault, a derived
artifact, and a failing verification command.  Process crashes (WAL / worker /
SIGKILL) are a different suite.  Causal, temporal, and whole-branch policies
then diverge.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from agenttx.conversation import record_tool_record


RECOVERY_MANIFEST_PATH = ".agenttx/recovery_manifest.json"


def recovery_context_variant() -> str:
    """Return the external-agent recovery handoff projection."""
    value = os.environ.get("AGENTTX_RECOVERY_CONTEXT", "full").strip().lower()
    return "compact" if value in {"1", "true", "yes", "on", "compact"} else "full"


INJECT_SYSTEM = (
    "You are a coding agent in an AgentTX-protected workspace. Use tools for edits. "
    "Call finish when done."
)


def bind_injected_step(agent, record, args: Optional[Mapping[str, object]] = None, *, task_name: str) -> None:
    """Record one injected tool call so later rewind can drop invalidated spans."""
    tx = getattr(getattr(agent, "harness", None), "tx", None)
    if tx is None or record is None:
        return
    conversation = tx.conversation
    if conversation.is_empty():
        conversation.seed(
            INJECT_SYSTEM,
            (
                f"You are mid-session on {task_name}. Independent notes, a producer, "
                "and derived artifacts were written; recovery may follow."
            ),
        )
    record_tool_record(conversation, record, args=args)
    persist = getattr(tx, "_persist", None)
    if callable(persist):
        persist()


def _call_tool(agent, name: str, args: dict, *, task_name: str, bind: bool):
    record = agent.harness.call_tool(name, args)
    if bind:
        bind_injected_step(agent, record, args, task_name=task_name)
    return record



@dataclass(frozen=True)
class DocSpec:
    path: str
    prefix: str
    lines: int


def document_content(prefix: str, lines: int, task_name: str) -> str:
    title = prefix.title().replace("_", " ")
    rows = [f"# {title}"]
    for index in range(1, lines + 1):
        rows.append(
            f"{prefix.upper()}-{index:03d}: verified {task_name} work item "
            f"{index:03d} records a repository decision."
        )
    return "\n".join(rows) + "\n"


POST_CRASH_PATH = "recovery_notes/post_crash.md"
POST_CRASH_PREFIX = "postcrash"


def midcrash_docs(docs: Sequence[DocSpec]) -> Tuple[Tuple[DocSpec, ...], Tuple[DocSpec, ...]]:
    """Split independent notes into a pre-crash prefix and a post-crash sibling.

    Temporal rollback from the fault keeps the prefix and drops the suffix.
    Causal keeps both. Whole-branch abort drops both.  A single-document task
    gets an extra post-crash note so the three policies still diverge.
    """
    docs = tuple(docs)
    extra_lines = docs[0].lines if docs else 16
    extra = DocSpec(POST_CRASH_PATH, POST_CRASH_PREFIX, extra_lines)
    if len(docs) >= 2:
        n_before = max(1, len(docs) // 2)
        return docs[:n_before], docs[n_before:]
    if docs:
        return docs, (extra,)
    return (DocSpec("recovery_notes/design.md", "design", extra_lines),), (extra,)


def all_midcrash_docs(docs: Sequence[DocSpec]) -> Tuple[DocSpec, ...]:
    before, after = midcrash_docs(docs)
    return before + after


def document_valid(path: Path, prefix: str, lines: int) -> bool:
    if not path.exists():
        return False
    return document_text_valid(path.read_text(encoding="utf-8"), prefix, lines)


def document_text_valid(content: str, prefix: str, lines: int) -> bool:
    """Validate the note contract without requiring a host-visible path."""
    content_lines = content.splitlines()
    expected = [f"{prefix.upper()}-{index:03d}:" for index in range(1, lines + 1)]
    entries = [
        line
        for line in content_lines[1:]
        if line.startswith(prefix.upper() + "-")
    ]
    return len(entries) == lines and all(
        line.startswith(label) for line, label in zip(entries, expected)
    )


def all_documents_valid(workdir: Path, docs: Sequence[DocSpec]) -> bool:
    return all(
        document_valid(Path(workdir) / spec.path, spec.prefix, spec.lines)
        for spec in docs
    )


def _step_failed(step) -> bool:
    code = getattr(step, "exit_code", None)
    if code is None:
        code = getattr(step, "returncode", 1)
    return int(code) != 0


def _write_docs(agent, specs: Sequence[DocSpec], task_name: str, *, bind: bool) -> list:
    steps = []
    for spec in specs:
        args = {
            "path": spec.path,
            "content": document_content(spec.prefix, spec.lines, task_name),
        }
        steps.append(_call_tool(agent, "write_file", args, task_name=task_name, bind=bind))
    return steps


def inject_recovery_dag(
    agent,
    *,
    docs: Sequence[DocSpec],
    task_name: str,
    prefix_writes: Sequence[Tuple[str, str]] = (),
    faulty_path: str,
    faulty_content: str,
    derived_cmd: str,
    test_cmd: str,
    bind_conversation: bool = True,
) -> dict:
    """Inject a crash in the middle of independent work.

    Order: optional prefix writes, pre-crash notes, faulty producer, post-crash
    notes that do not feed derived state, derived artifact, failing tests.

    ``bind_conversation`` records each injected tool call on the native
    conversation log so rollback can rewind logical state with the overlay.
    """
    before_specs, after_specs = midcrash_docs(docs)
    prefix_steps = []
    for path, content in prefix_writes:
        args = {"path": path, "content": content}
        prefix_steps.append(
            _call_tool(agent, "write_file", args, task_name=task_name, bind=bind_conversation)
        )
    before_steps = _write_docs(agent, before_specs, task_name, bind=bind_conversation)
    faulty = _call_tool(
        agent,
        "write_file",
        {"path": faulty_path, "content": faulty_content},
        task_name=task_name,
        bind=bind_conversation,
    )
    after_steps = _write_docs(agent, after_specs, task_name, bind=bind_conversation)
    independent_steps = before_steps + after_steps
    derived = _call_tool(
        agent,
        "run_shell",
        {"cmd": derived_cmd},
        task_name=task_name,
        bind=bind_conversation,
    )
    failing = _call_tool(
        agent,
        "run_tests",
        {"cmd": test_cmd},
        task_name=task_name,
        bind=bind_conversation,
    )
    return {
        "prefix_steps": [step.step_id for step in prefix_steps],
        "root_step": faulty.step_id,
        "faulty_path": faulty_path,
        "docs_before": [spec.path for spec in before_specs],
        "docs_after": [spec.path for spec in after_specs],
        "independent_before_steps": [step.step_id for step in before_steps],
        "independent_after_steps": [step.step_id for step in after_steps],
        "independent_steps": [step.step_id for step in independent_steps],
        "derived_step": derived.step_id,
        "derived_paths": sorted(
            {"recovery_build/derived.txt"}
            | {
                str(Path(effect.path).resolve().relative_to(agent.harness.workdir))
                for effect in getattr(derived, "effects", [])
                if str(
                    getattr(
                        getattr(effect, "kind", ""),
                        "value",
                        getattr(effect, "kind", ""),
                    )
                )
                in {"W", "D"}
                and _is_under_workspace(effect.path, agent.harness.workdir)
            }
        ),
        "test_run_step": failing.step_id,
        "root_is_parent_of_derived": faulty.step_id in derived.parents,
        "root_is_parent_of_tests": faulty.step_id in failing.parents,
        "independent_is_parent_of_derived": any(
            step.step_id in derived.parents for step in independent_steps
        ),
        "tests_failed": _step_failed(failing),
    }


def dag_is_valid(injected: dict) -> bool:
    before = list(injected.get("independent_before_steps") or ())
    after = list(injected.get("independent_after_steps") or ())
    root = injected.get("root_step")
    if root is None or not before or not after:
        return False
    return bool(
        injected.get("tests_failed")
        and injected.get("root_is_parent_of_derived")
        and injected.get("root_is_parent_of_tests")
        and not injected.get("independent_is_parent_of_derived")
        and max(before) < int(root) < min(after)
    )


def _is_under_workspace(path: str, workdir: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(workdir).resolve())
        return True
    except ValueError:
        return False


def _relative_path(path: str, workdir: Path) -> Optional[str]:
    candidate = Path(path)
    if not candidate.is_absolute():
        return candidate.as_posix().lstrip("./")
    try:
        return candidate.resolve().relative_to(Path(workdir).resolve()).as_posix()
    except ValueError:
        return None


def _step_by_id(ledger, step_id: int):
    for step in getattr(ledger, "steps", []):
        if getattr(step, "step_id", None) == step_id:
            return step
    return None


def _latest_active_writer(ledger, path: str, workdir: Path) -> Optional[int]:
    for step in reversed(list(getattr(ledger, "steps", []))):
        if getattr(step, "status", "") == "rolled_back":
            continue
        for effect in getattr(step, "effects", []):
            kind = str(getattr(getattr(effect, "kind", ""), "value", getattr(effect, "kind", "")))
            if kind not in {"W", "D"}:
                continue
            if _relative_path(getattr(effect, "path", ""), workdir) == path:
                return int(step.step_id)
    return None


def read_recovery_documents(agent, docs: Sequence[DocSpec]) -> Dict[str, str]:
    """Read retained artifacts through the merged transaction view.

    These are trusted runtime verification reads performed before the new LLM
    session. They intentionally become ledger steps so the manifest has an
    auditable evidence chain, but they are excluded from agent reopen metrics.
    """
    contents: Dict[str, str] = {}
    for spec in docs:
        record = agent.harness.call_tool("read_file", {"path": spec.path})
        if int(getattr(record, "returncode", 1)) == 0:
            contents[spec.path] = str(getattr(record, "stdout", ""))
    return contents


def build_recovery_manifest(
    *,
    policy: str,
    ledger,
    injected: Mapping[str, object],
    docs: Sequence[DocSpec],
    document_contents: Mapping[str, str],
    workdir: Path,
    rollback_targets: Sequence[int],
    path_exists: Mapping[str, bool],
) -> dict:
    """Build the machine-verifiable handoff state for a fresh agent session."""
    independent_steps = list(injected.get("independent_steps") or ())
    retained = []
    recreate_required = []
    manifest_mismatch = False
    for index, spec in enumerate(docs):
        original_step_id = int(independent_steps[index]) if index < len(independent_steps) else -1
        original_step = _step_by_id(ledger, original_step_id)
        original_active = bool(
            original_step is not None and getattr(original_step, "status", "") != "rolled_back"
        )
        content = document_contents.get(spec.path)
        valid = content is not None and document_text_valid(content, spec.prefix, spec.lines)
        producer_step = _latest_active_writer(ledger, spec.path, workdir)
        if original_active and producer_step == original_step_id:
            origin = "retained_by_causal_recovery"
        elif not original_active and content is not None and producer_step is not None:
            origin = "regenerated_after_recovery"
        elif content is not None:
            origin = "unexplained_workspace_state"
        else:
            origin = "discarded"
        item = {
            "path": spec.path,
            "state": "complete-protected" if valid else "state-mismatch",
            "origin": origin,
            "original_step": original_step_id,
            "producer_step": producer_step,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()
            if content is not None
            else None,
            "contract": f"{spec.prefix.upper()}-001..{spec.prefix.upper()}-{spec.lines:03d}",
            "contract_valid": bool(valid),
        }
        if valid and origin in {
            "retained_by_causal_recovery",
            "regenerated_after_recovery",
        }:
            retained.append(item)
        elif (
            content is None
            and not original_active
            and policy in {"temporal_checkpoint", "whole_branch_abort"}
        ):
            # A coarse rollback is allowed to discard independent notes.  It
            # is an authoritative state, not a mismatch; the fresh session
            # may recreate only these explicitly listed paths.
            item["state"] = "discarded"
            item["recovery_action"] = "recreate_if_missing"
            recreate_required.append(item)
        else:
            # A missing or invalid path under causal recovery, or an
            # unexplained workspace file, must still fail closed.
            manifest_mismatch = True

    invalidated = []
    faulty_path = str(injected.get("faulty_path") or "").lstrip("./")
    if faulty_path:
        invalidated.append(
            {
                "path": faulty_path,
                "reason": "fault_producer_rolled_back",
                "current_state": "restored" if path_exists.get(faulty_path, False) else "absent",
                "must_stay_absent": False,
            }
        )
    for path in sorted({str(item).lstrip("./") for item in injected.get("derived_paths") or ()}):
        invalidated.append(
            {
                "path": path,
                "reason": "fault_dependent_rolled_back",
                "current_state": "present" if path_exists.get(path, False) else "absent",
                "must_stay_absent": True,
            }
        )

    retained_paths = {
        item["path"] for item in (*retained, *recreate_required)
    }
    invalidated_paths = {item["path"] for item in invalidated}
    no_overlap = not bool(retained_paths & invalidated_paths)
    invalid_absent = all(
        not item["must_stay_absent"] or item["current_state"] == "absent"
        for item in invalidated
    )
    authoritative = bool(
        (retained or recreate_required)
        and not manifest_mismatch
        and all(
            item["contract_valid"]
            and item["sha256"]
            and item["origin"]
            in {"retained_by_causal_recovery", "regenerated_after_recovery"}
            for item in retained
        )
        and invalid_absent
        and no_overlap
    )
    payload = {
        "schema": "agenttx.recovery_manifest/v1",
        "policy": policy,
        "authoritative": authoritative,
        "generation": {
            "ledger_steps": len(getattr(ledger, "steps", [])),
            "committed_frontier": int(getattr(ledger, "committed_frontier", -1)),
            "rollback_targets": sorted(int(item) for item in rollback_targets),
        },
        "retained": retained,
        "recreate_required": recreate_required,
        "invalidated": invalidated,
        "pending": ["implement the official task", "run the official verifier"],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        **payload,
        "state_id": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def recovery_manifest_json(manifest: Mapping[str, object]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_recovery_manifest_prompt(manifest: Mapping[str, object]) -> str:
    """Render one fixed prompt schema for every recovery policy."""
    if not manifest.get("authoritative"):
        return (
            "## AgentTX recovery state (machine-generated; STATE MISMATCH)\n"
            f"State ID: {manifest.get('state_id', 'unknown')}\n"
            "The runtime could not verify a context/workspace-aligned recovery state. "
            "Report `AGENTTX_STATE_MISMATCH` and stop; do not repair recovery artifacts."
        )
    if recovery_context_variant() == "compact":
        retained_paths = [
            f"- {item['path']} (complete-protected; verified; do not inspect or rewrite)"
            for item in manifest.get("retained", [])
        ]
        recreate_paths = [
            f"- {item['path']}: recreate only when absent; contract={item['contract']}"
            for item in manifest.get("recreate_required", [])
        ]
        invalidated_paths = [
            f"- {item['path']}: {item['reason']}"
            for item in manifest.get("invalidated", [])
        ]
        return "\n".join(
            [
                "## AgentTX recovery handoff (compact; authoritative)",
                f"State ID: {manifest.get('state_id', 'unknown')}",
                f"Policy: {manifest.get('policy', 'unknown')}",
                "Runtime verified this causal state; the certificate is authoritative.",
                "",
                "COMPLETE-PROTECTED (do not inspect):",
                *(retained_paths or ["- none"]),
                "",
                "RECREATE-REQUIRED (only if absent):",
                *(recreate_paths or ["- none"]),
                "",
                "INVALIDATED (the only task paths to inspect or edit):",
                *(invalidated_paths or ["- none"]),
                "",
                "PENDING:",
                *[f"- {item}" for item in manifest.get("pending", [])],
                "",
                "Three-step fast path: inspect only the invalidated source and named verifier; "
                "make one focused edit; run the named verifier once and stop at the first pass. "
                "Do not inventory, diagnose, rerun successful commands, or touch protected paths. "
                "On a protected-path mismatch, report AGENTTX_STATE_MISMATCH and stop.",
            ]
        )
    retained_lines = []
    for item in manifest.get("retained", []):
        retained_lines.append(
            f"- `{item['path']}`: origin={item['origin']}; "
            f"sha256={str(item['sha256'])[:16]}...; contract={item['contract']} passed"
        )
    recreate_lines = []
    for item in manifest.get("recreate_required", []):
        recreate_lines.append(
            f"- `{item['path']}`: intentionally discarded by recovery; "
            f"contract={item['contract']}; recreate only if absent"
        )
    invalidated_lines = []
    for item in manifest.get("invalidated", []):
        suffix = "; must stay absent" if item.get("must_stay_absent") else ""
        invalidated_lines.append(
            f"- `{item['path']}`: {item['reason']}; current_state={item['current_state']}{suffix}"
        )
    return "\n".join(
        [
            "## AgentTX recovery state (machine-generated; authoritative)",
            f"State ID: {manifest.get('state_id', 'unknown')}",
            f"Policy: {manifest.get('policy', 'unknown')}",
            "",
            "COMPLETE-PROTECTED — already verified outside this LLM session:",
            *retained_lines,
            "",
            "RECREATE-REQUIRED — intentionally discarded by recovery:",
            *(recreate_lines or ["- none"]),
            "",
            "INVALIDATED BY RECOVERY:",
            *(invalidated_lines or ["- none"]),
            "",
            "PENDING:",
            *[f"- {item}" for item in manifest.get("pending", [])],
            "",
            "Do not read, validate, rewrite, or recreate COMPLETE-PROTECTED paths. ",
            "Their hashes and contracts were verified by AgentTX after recovery. ",
            "Recreate only RECREATE-REQUIRED paths if they are absent. ",
            "If a tool reports a mismatch, do not repair these paths; report ",
            "`AGENTTX_STATE_MISMATCH` and stop touching them.",
        ]
    )


def retained_artifact_access(
    steps: Sequence,
    *,
    first_step: int,
    last_step: Optional[int],
    retained_paths: Sequence[str],
    workdir: Path,
) -> dict:
    """Summarize new-session access to retained artifacts from ledger effects."""
    retained = [str(Path(path)).lstrip("./") for path in retained_paths]
    reopened = set()
    modified = set()
    read_effects = 0
    selected = steps[first_step:last_step]
    for step in selected:
        if getattr(step, "status", "") == "rolled_back":
            continue
        for effect in getattr(step, "effects", []):
            relative = _relative_path(getattr(effect, "path", ""), workdir)
            if relative is None:
                continue
            matches = [
                path
                for path in retained
                if relative == path
                or relative.startswith(path.rstrip("/") + "/")
                or path.startswith(relative.rstrip("/") + "/")
            ]
            if not matches:
                continue
            kind = str(getattr(getattr(effect, "kind", ""), "value", getattr(effect, "kind", "")))
            if kind == "R":
                read_effects += 1
                reopened.update(matches)
            elif kind in {"W", "D"}:
                modified.update(matches)
    return {
        "retained_paths_reopened": sorted(reopened),
        "retained_read_effects": read_effects,
        "retained_paths_modified": sorted(modified),
    }


def missing_independent_docs(workdir: Path, docs: Sequence[DocSpec], agent=None) -> List[DocSpec]:
    """Return independent notes absent from the live overlay, not the host lowerdir.

    Host ``Path(workdir)`` does not see kept upperdir files after a partial
    rollback. Token accounting must follow the session the agent actually sees.
    """
    missing: List[DocSpec] = []
    tx = getattr(getattr(agent, "harness", None), "tx", None)
    for spec in docs:
        path = Path(workdir) / spec.path
        if tx is not None:
            exists = tx.path_exists(path)
            if not exists:
                missing.append(spec)
                continue
            record = agent.harness.call_tool("read_file", {"path": spec.path})
            text = str(getattr(record, "stdout", "") or "")
            if int(getattr(record, "returncode", 1)) != 0 or not document_text_valid(
                text, spec.prefix, spec.lines
            ):
                missing.append(spec)
            continue
        if not document_valid(path, spec.prefix, spec.lines):
            missing.append(spec)
    return missing


def independent_work_discarded(injected: dict, ledger) -> bool:
    """True when the recovery policy rolled back independent document steps.

    Causal keeps those steps; coarse policies discard them.  Replay-token
    accounting follows the ledger, not overlay internals.
    """
    by_id = {step.step_id: step for step in getattr(ledger, "steps", [])}
    for sid in injected.get("independent_steps") or ():
        step = by_id.get(sid)
        if step is not None and getattr(step, "status", "") == "rolled_back":
            return True
    return False


def doc_replay_prompt(*, docs: Sequence[DocSpec], task_name: str) -> str:
    """Isolated prompt that only regenerates missing independent notes."""
    blocks = []
    for spec in docs:
        body = document_content(spec.prefix, spec.lines, task_name).rstrip("\n")
        blocks.append(
            f"`{spec.path}` must be created with exactly this content:\n```\n{body}\n```"
        )
    joined = "\n\n".join(blocks)
    return f"""The recovery policy removed independent notes. Recreate only the files below. Do not modify repository sources, do not run the official tests, and do not write `recovery_build/derived.txt`.

{joined}

Write each listed file, then stop. Do not inspect other paths.
"""


def recovery_prompt(
    *,
    title: str,
    context: str,
    instruction: str,
    docs: Sequence[DocSpec],
    test_cmd: str,
    extra_rules: Iterable[str] = (),
    mode: str = "causal",
    recovery_manifest: Optional[Mapping[str, object]] = None,
) -> str:
    if recovery_manifest is not None:
        policy = (
            "AgentTX completed the selected recovery policy and generated a "
            "context-aligned state certificate for this fresh session. Treat "
            "the certificate as authoritative: do not reopen, verify, or "
            "rewrite any retained path listed as complete-protected."
        )
        if recovery_context_variant() == "compact":
            policy = (
                "AgentTX verified the recovery state in the control plane. "
                "Use the compact handoff below; do not inspect retained paths."
            )
        if recovery_manifest.get("policy") == "causal":
            policy += (
                " This is the causal fast path: the retained work is already "
                "complete, so spend the remaining context only on the pending "
                "official fix."
            )
        note_step = render_recovery_manifest_prompt(recovery_manifest)
        work_step = (
            "Implement the pending official task using one focused edit. Inspect only the "
            "invalidated source path and the named verifier; if the invalidated path is the "
            "requested output, create it directly from the official instruction. Run the exact "
            f"verifier once with `{test_cmd}` and stop immediately when it passes."
        )
    elif mode == "causal":
        policy = (
            "The causal recovery policy retained the independent recovery notes. "
            "They are already complete and valid."
        )
        doc_lines = "\n".join(
            f"- `{spec.path}` is already complete. Do not open, verify, or rewrite it."
            for spec in docs
        )
        note_step = (
            "Do not open, inspect, verify, or rewrite anything under `recovery_notes/`. "
            "Treat those files as finished work.\n"
            f"{doc_lines}"
        )
        work_step = (
            "Complete the official task in repository sources and run the official verifier with "
            f"`{test_cmd}`. Do not inventory `recovery_notes/`."
        )
    elif mode == "temporal_checkpoint":
        before_specs, after_specs = midcrash_docs(docs)
        policy = (
            "The temporal checkpoint policy restored the workspace to the crash "
            "timestamp. Independent notes written before the crash remain; notes "
            "written after it were lost."
        )
        kept = "\n".join(
            f"- `{spec.path}` was written before the crash and is already complete. "
            "Do not open, verify, or rewrite it."
            for spec in before_specs
        )
        lost = "\n".join(
            f"- `{spec.path}` was lost. If it is absent, recreate a title followed by exactly {spec.lines} "
            f"ordered entries `{spec.prefix.upper()}-001:` through `{spec.prefix.upper()}-{spec.lines:03d}:`. "
            "Each line after the title must start with that label and must not use a `1. ` numbering prefix. "
            "If the file is already present, leave it untouched."
            for spec in after_specs
        )
        doc_lines = "\n".join(part for part in (kept, lost) if part)
        note_step = (
            "Keep pre-crash notes. Recreate only the post-crash notes that are missing.\n"
            f"{doc_lines}"
        )
        work_step = (
            "After the notes exist, complete the official task in repository sources and run the official verifier with "
            f"`{test_cmd}`."
        )
    elif mode == "whole_branch_abort":
        policy = (
            "The whole-branch abort policy discarded the uncommitted branch, "
            "so both pre-crash and post-crash independent notes were lost."
        )
        doc_lines = "\n".join(
            f"- `{spec.path}` was lost. If it is absent, recreate a title followed by exactly {spec.lines} "
            f"ordered entries `{spec.prefix.upper()}-001:` through `{spec.prefix.upper()}-{spec.lines:03d}:`. "
            "Each line after the title must start with that label and must not use a `1. ` numbering prefix. "
            "If the file is already present, leave it untouched."
            for spec in docs
        )
        note_step = (
            "Recreate missing independent recovery notes with the required format. "
            "If a listed note is already present, do not open, verify, or rewrite it.\n"
            f"{doc_lines}"
        )
        work_step = (
            "After the notes exist, complete the official task in repository sources and run the official verifier with "
            f"`{test_cmd}`."
        )
    elif mode == "clean_recovery":
        policy = (
            "This is a clean recovery-context control. No crash was injected and "
            "no rollback was performed; the recovery-shaped prompt is present only "
            "to measure prompt/context overhead."
        )
        note_step = (
            "There are no retained, invalidated, or recreate-required recovery "
            "artifacts. Do not create recovery notes or generated build artifacts."
        )
        work_step = (
            "Complete the official task in repository sources and run the official verifier with "
            f"`{test_cmd}`."
        )
    elif mode == "crash_no_rollback":
        policy = (
            "A synthetic crash was injected before this fresh session. This control "
            "intentionally performs no rollback, so the injected workspace state is "
            "still present and must be handled as actual task state."
        )
        note_step = (
            "Do not create unrelated recovery notes or generated artifacts. Inspect "
            "only task-relevant paths and preserve the injected state unless changing "
            "it is necessary to complete the official task."
        )
        work_step = (
            "Complete the official task in the current workspace and run the official verifier with "
            f"`{test_cmd}`."
        )
    elif mode == "no_fault":
        policy = (
            "This is an ordinary clean official-task run. The workspace contains only "
            "the task inputs and the files needed for the requested solution."
        )
        note_step = (
            "Do not create auxiliary notes or generated build artifacts; keep changes "
            "limited to files required by the official task."
        )
        work_step = (
            "Complete the official task in repository sources and run the official verifier with "
            f"`{test_cmd}`."
        )
    else:
        raise ValueError(mode)

    common_step = (
        "Use the direct workspace tools provided by this session (especially the shell "
        "and file-edit tools) yourself. The outer AgentTX runtime already supplies the "
        "workspace isolation; do not delegate the task, ask for approval, or conclude "
        "that tools are unavailable based on a delegated agent's report. If a direct "
        "tool returns an error, use its actual output to diagnose and retry. If it returns "
        "a successful result, do not repeat that command. After a verifier failure, inspect "
        "the actual failure, make one focused edit, rerun the named verifier, and stop once "
        "it passes. Keep every read bounded (targeted symbol/range only); never dump a whole "
        "source file, dependency tree, virtualenv, or generated log into the context. "
        "This is a single-agent benchmark: no research subagent, delegation, "
        "background agent, or waiting for another agent is available or allowed; never "
        "invoke, queue, or wait for one. The current "
        "working directory is the official `/app` root: use relative paths such as "
        "`task_file/...`, and never search `/`, `/tmp`, `/home`, `/usr`, or parent "
        "directories."
    )
    if mode not in {"no_fault", "crash_direct"}:
        common_step += (
            " Do not inventory `.dsh`, `.agents`, `.sessions`, or `.agenttx`; those are "
            "harness metadata, not task sources."
        )
    if recovery_context_variant() == "compact" and recovery_manifest is not None:
        common_step = (
            "Use direct workspace tools. The compact recovery certificate is authoritative: "
            "never inspect or modify protected recovery artifacts. Use one bounded inspection "
            "of the invalidated task source, one focused edit, and one run of the named verifier; "
            "stop at the first pass. Do not inventory files, run diagnostics, repeat successful "
            "commands, or delegate."
        )
    if mode == "no_fault":
        steps = [common_step, work_step, *list(extra_rules)]
        numbered = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))
        return f"""{policy}

Task: {title}
{context}

Official instruction:
{instruction.strip()}

Execution rules:
{numbered}

Stay inside the workspace.
"""
    artifact_step = (
        "Ensure `recovery_build/derived.txt` is absent before finishing."
        if mode in {"clean_recovery", "causal", "temporal_checkpoint", "whole_branch_abort"}
        else "Do not create unrelated build artifacts; this no-rollback control intentionally preserves the injected workspace state."
    )
    steps = [
        common_step,
        note_step,
        work_step,
        artifact_step,
        *list(extra_rules),
        "Do not call any rollback tool: the comparison policy has already run.",
        "Call `finish` with `commit=false` and a one-sentence summary when the official verifier passes.",
    ]
    numbered = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    if mode == "clean_recovery":
        intro = "This is a clean recovery-context calibration session. No fault was injected."
    elif mode == "crash_no_rollback":
        intro = "A synthetic crash was injected before this fresh session; this control intentionally performs no rollback."
    else:
        intro = "A previous attempt at this official benchmark task introduced a faulty producer in this protected coding session."
    return f"""{intro}  {policy}

Task: {title}
{context}

Official instruction:
{instruction.strip()}

Recovery protocol:
{numbered}

Stay inside the workspace.  Do not use shell loops, Python generators, or bulk
file-copy commands to manufacture the documents.  Official-task API tokens are
counted separately from independent-document replay tokens.
"""
