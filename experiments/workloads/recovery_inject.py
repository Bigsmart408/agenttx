"""Shared inject–policy–repair DAG for application-workload recovery.

Official SWE-Bench / Terminal-Bench instances provide the workspace and the
utility predicate.  This module overlays one faulty producer, later independent
documents, a derived artifact, and a failing verification command so causal vs
coarse recovery can be compared on the same ledger.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


RECOVERY_MANIFEST_PATH = ".agenttx/recovery_manifest.json"


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
) -> dict:
    """Write tests/context, a faulty producer, independent docs, derived state, then fail tests."""

    prefix_steps = []
    for path, content in prefix_writes:
        prefix_steps.append(
            agent.harness.call_tool("write_file", {"path": path, "content": content})
        )
    faulty = agent.harness.call_tool(
        "write_file",
        {"path": faulty_path, "content": faulty_content},
    )
    independent_steps = []
    for spec in docs:
        independent_steps.append(
            agent.harness.call_tool(
                "write_file",
                {
                    "path": spec.path,
                    "content": document_content(spec.prefix, spec.lines, task_name),
                },
            )
        )
    derived = agent.harness.call_tool("run_shell", {"cmd": derived_cmd})
    failing = agent.harness.call_tool("run_tests", {"cmd": test_cmd})
    return {
        "prefix_steps": [step.step_id for step in prefix_steps],
        "root_step": faulty.step_id,
        "faulty_path": faulty_path,
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
    return bool(
        injected.get("tests_failed")
        and injected.get("root_is_parent_of_derived")
        and injected.get("root_is_parent_of_tests")
        and not injected.get("independent_is_parent_of_derived")
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
        retained.append(
            {
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
        )

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

    retained_paths = {item["path"] for item in retained}
    invalidated_paths = {item["path"] for item in invalidated}
    no_overlap = not bool(retained_paths & invalidated_paths)
    invalid_absent = all(
        not item["must_stay_absent"] or item["current_state"] == "absent"
        for item in invalidated
    )
    authoritative = bool(
        retained
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
    retained_lines = []
    for item in manifest.get("retained", []):
        retained_lines.append(
            f"- `{item['path']}`: origin={item['origin']}; "
            f"sha256={str(item['sha256'])[:16]}...; contract={item['contract']} passed"
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
            "INVALIDATED BY RECOVERY:",
            *(invalidated_lines or ["- none"]),
            "",
            "PENDING:",
            *[f"- {item}" for item in manifest.get("pending", [])],
            "",
            "Do not read, validate, rewrite, or recreate COMPLETE-PROTECTED paths. ",
            "Their hashes and contracts were verified by AgentTX after recovery. ",
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


def missing_independent_docs(workdir: Path, docs: Sequence[DocSpec]) -> List[DocSpec]:
    """Return independent notes that are absent or invalid on the host workdir."""
    missing: List[DocSpec] = []
    for spec in docs:
        if not document_valid(Path(workdir) / spec.path, spec.prefix, spec.lines):
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
            "context-aligned state certificate for this fresh session."
        )
        note_step = render_recovery_manifest_prompt(recovery_manifest)
        work_step = (
            "Complete only the pending official task in repository sources and run the "
            f"official verifier with `{test_cmd}`. Do not inventory recovery artifacts."
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
        policy = (
            "The temporal checkpoint policy restored an earlier workspace snapshot, "
            "so later independent recovery notes were lost."
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
    elif mode == "whole_branch_abort":
        policy = (
            "The whole-branch abort policy discarded the branch that contained the independent recovery notes, "
            "so those notes were lost."
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
    else:
        raise ValueError(mode)

    steps = [
        work_step,
        note_step,
        "Ensure `recovery_build/derived.txt` is absent before finishing.",
        *list(extra_rules),
        "Do not call any rollback tool: the comparison policy has already run.",
        "Call `finish` with `commit=false` and a one-sentence summary when the official verifier passes.",
    ]
    numbered = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    return f"""A previous attempt at this official benchmark task introduced a faulty producer in this protected coding session.  {policy}

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
