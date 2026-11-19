"""Preserve selective download routing across the rendering task's sudo calls."""
import hashlib
import os
from pathlib import Path

TASK = 'unprivileged-headless-pyrender'
CONTRACT = 'private-task-sudo-preserves-download-proxy-and-osmesa-input/v2'
VARIABLES = ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'NO_PROXY', 'no_proxy')
PRESERVED_VARIABLES = VARIABLES + ('PYOPENGL_PLATFORM',)
CONTENTS = 'Defaults env_keep += "' + ' '.join(PRESERVED_VARIABLES) + '"\n'


def stripped_environment_failure(text):
    """Require a working OSMesa prerequisite before attributing a GLX failure."""
    if 'Downloading Duck model' in text and 'ConnectionResetError' in text:
        return 'github-download-without-sudo-proxy'
    osmesa_passed = 'PASSED ../../tests/test_outputs.py::test_osmesa_is_runnable' in text
    wrong_backend = ('ImportError: Unable to load OpenGL or GL library' in text
                     or ('OpenGL/platform/glx.py' in text and 'glXGetCurrentContext' in text))
    if osmesa_passed and wrong_backend:
        return 'explicit-osmesa-input-stripped-before-glx-import'
    return None


def configure(task_id, directory=Path('/etc/sudoers.d')):
    if task_id != TASK or os.environ.get('AGENTTX_DOWNLOAD_PROXY_RETRY') != '1':
        return None
    from experiments.scripts.tb_download_proxy import environment
    expected = environment()
    if any(os.environ.get(key) != expected[key] for key in VARIABLES):
        raise ValueError('Task sudo proxy setup requires the validated selective proxy environment')
    if os.geteuid() != 0:
        raise PermissionError('Task sudo proxy setup must precede the task-user transition')
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/'agenttx-download-proxy'
    if target.exists():
        if target.read_text() != CONTENTS:
            raise ValueError('Refusing to replace an unrelated sudo environment rule')
    else:
        target.write_text(CONTENTS)
    target.chmod(0o440)
    return dict(contract=CONTRACT, variables=list(PRESERVED_VARIABLES), path=str(target),
                sha256=hashlib.sha256(CONTENTS.encode()).hexdigest(),
                host_forwarding_modified=False, task_sources_modified=False)
