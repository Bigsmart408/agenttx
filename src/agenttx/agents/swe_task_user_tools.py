"""Make the bash-only SWE-agent submit tool usable by the original task user.

This runs inside the disposable task container, never on the host tool account.
Tool code remains coordinator-owned; only state and patch output are user-owned.
"""

from __future__ import annotations

import hashlib
import os
import pwd
import shlex
import shutil
import tempfile
from pathlib import Path


TASK_USER_SWE_TOOLS_CONTRACT = "original-task-user-private-swe-submit/v1"
AUTOSUBMIT_COMMAND = "git add -A && git diff --cached > /root/model.patch"


class TaskUserSWETools:
    def __init__(self, user: str, environment: dict[str, str], *,
                 tools_root: Path = Path('/root/tools'),
                 private_parent: Path = Path('/opt')) -> None:
        account = pwd.getpwnam(user)
        if os.geteuid() != 0 or account.pw_uid == 0:
            raise RuntimeError('private SWE tools require a root coordinator and non-root task user')
        installed = [p for p in environment.get('PATH', '').split(os.pathsep)
                     if p.startswith(str(tools_root) + '/')]
        if installed != [str(tools_root / 'submit/bin')]:
            raise RuntimeError('non-root SWE integration currently supports only the submit bundle')
        source = tools_root / 'submit/bin/submit'
        body = source.read_text()
        if not all(s in body for s in ('/root/test.patch', '/root/model.patch',
                                      '<<SWE_AGENT_SUBMISSION>>')):
            raise RuntimeError('unsupported SWE-agent submit implementation')
        test_patch = tools_root.parent / 'test.patch'
        if test_patch.exists() and test_patch.stat().st_size:
            raise RuntimeError('cannot expose a protected root test.patch to the task user')
        self.root = Path(tempfile.mkdtemp(prefix='.agenttx-swe-tools-', dir=private_parent))
        try:
            self.root.chmod(0o755)
            code = self.root / 'bin'
            code.mkdir(mode=0o755)
            state = self.root / 'state'
            state.mkdir(mode=0o700)
            os.chown(state, account.pw_uid, account.pw_gid)
            self.paths = {f'/root/{name}': state / name for name in
                          ('test.patch', 'model.patch', 'state.json', '.swe-agent-env')}
            for name in ('state.json', '.swe-agent-env'):
                original = tools_root.parent / name
                target = state / name
                target.write_bytes(original.read_bytes() if original.is_file() else b'{}')
                target.chmod(0o600)
                os.chown(target, account.pw_uid, account.pw_gid)
            for original in ('/root/test.patch', '/root/model.patch'):
                body = body.replace(original, str(self.paths[original]))
            executable = code / 'submit'
            executable.write_text(body)
            executable.chmod(0o755)
            environment.update(HOME=account.pw_dir, USER=user, LOGNAME=user,
                               SHELL=account.pw_shell)
            # /run and /tmp are private mounts in try. /opt survives every
            # protected command; root-only PATH entries must not shadow tools.
            remainder = [p for p in environment.get('PATH', '').split(os.pathsep)
                         if p and p != '/root' and not p.startswith('/root/')]
            environment['PATH'] = os.pathsep.join([str(code), *remainder])
            self.proof = dict(contract=TASK_USER_SWE_TOOLS_CONTRACT, task_user=user,
                              source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                              root_permissions_modified=False, tool_code_user_writable=False)
        except BaseException:
            self.close()
            raise

    def path(self, value: str | Path) -> Path:
        return self.paths.get(str(value), Path(value))

    def command(self, value: str) -> str:
        if value == AUTOSUBMIT_COMMAND:
            return 'git add -A && git diff --cached > ' + shlex.quote(str(self.paths['/root/model.patch']))
        return value

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
