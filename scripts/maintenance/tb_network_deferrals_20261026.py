"""Explicit task dependency deferrals; immutable outcomes and bills stay intact."""
import copy
import hashlib
import json
from pathlib import Path
import re

TASK = 'filter-js-from-html'
REASON = 'official_verifier_github_attack_fixture_download_unavailable'
NETWORK_ERROR = re.compile(r'urllib\.error\.URLError:.*(?:Connection refused|timed out|Temporary failure in name resolution|Network is unreachable)', re.I)


def verifier_network_evidence(row):
    if row.get('task') != TASK or row.get('state') not in ('completed', 'infrastructure_error'):
        return None
    path = Path(row['result_path'])
    result = json.loads(path.read_text())
    if result.get('success') or result.get('official_tests_rc') != 1:
        return None
    log = path.parent / 'verifier.stdout.log'
    if not log.exists():
        return None
    text = log.read_text(errors='replace')
    if not ('in download_attack_vectors' in text and 'urllib.request.urlretrieve(url, zip_path)' in text):
        return None
    error = NETWORK_ERROR.search(text)
    if error is None:
        return None
    return dict(reason=REASON, result_path=str(path), result_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source=str(log), source_sha256=hashlib.sha256(log.read_bytes()).hexdigest(), error=error.group(0),
        independent_task_assertion_failure='AssertionError:' in text)


def annotate(rows, task_registry):
    for row in rows:
        mark = task_registry.get(row['task'])
        if mark:
            row['dispatch_deferral'] = dict(reason=mark['reason'], registry=mark['registry'],
                task_scope='all_harnesses_and_policies', existing_attempt_action='finish_and_preserve')
        if row.get('state') in ('completed', 'infrastructure_error') and row['task'] == TASK:
            evidence = verifier_network_evidence(row)
            if evidence:
                row['verifier_network_exclusion'] = evidence


def analysis_rows(rows):
    """Exclude demonstrated verifier dependency failures, retaining their costs."""
    result = copy.deepcopy(rows)
    for row in result:
        if row.get('verifier_network_exclusion'):
            row['state'] = 'infrastructure_error'
            row['issue'] = REASON
    return result


def deferral_report(cells, retries, registry):
    rows = cells + retries
    affected = [r for r in rows if r.get('verifier_network_exclusion')]
    known = 0
    incomplete = 0
    for row in affected:
        usage = json.loads(Path(row['result_path']).read_text()).get('api_usage') or {}
        known += usage.get('total_tokens') or 0
        incomplete += not bool(usage.get('complete'))
    return dict(tasks=registry, pending_first=sum(r['state']=='pending' and r['task'] in registry for r in cells),
        pending_retries=sum(r['state']=='pending' and r['task'] in registry for r in retries),
        existing_running=sum(r['state']=='running' and r['task'] in registry for r in rows),
        demonstrated_verifier_network_attempts=len(affected), preserved_known_main_replay_tokens=known,
        incomplete_bills=incomplete, exclusions=[r['verifier_network_exclusion'] for r in affected],
        definition='Dispatch deferral is task-wide across both harnesses/all policies. Only demonstrated dependency failures are excluded from outcome analysis; all raw outcomes and original+retry costs remain. Pending cells stay in the registered plan denominator.')
