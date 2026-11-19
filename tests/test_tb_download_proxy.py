import copy
import os
import urllib.request

import pytest
import yaml

from experiments.scripts import tb_download_proxy as p
from experiments.scripts import tb_task_services as services


def config():
    return yaml.safe_load(p.CONFIG.read_text())


def test_rules_allow_only_download_domains_and_direct_8080():
    c=config();p.validate_config(c)
    assert c['rules'][-1]=='MATCH,DIRECT'
    for change in [dict(mode='global'),dict(tun=dict(enable=True)),dict(**{'redir-port':7892}),dict(rules=['MATCH,DOWNLOAD_RETRY'])]:
        altered=copy.deepcopy(c);altered.update(change)
        with pytest.raises(AssertionError):p.validate_config(altered)


def test_model_api_bypasses_explicit_proxy_and_local_services(monkeypatch):
    for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy','NO_PROXY','no_proxy'):
        monkeypatch.delenv(name,raising=False)
    for k,v in p.environment().items():monkeypatch.setenv(k,v)
    assert urllib.request.proxy_bypass('10.249.44.127')
    assert urllib.request.proxy_bypass('localhost') and urllib.request.proxy_bypass('127.0.0.1')
    assert not urllib.request.proxy_bypass('github.com')
    from agenttx.agents.external import _direct_network_enabled
    assert not _direct_network_enabled()


def test_transport_options_override_direct_default_in_own_network():
    helper=p.RetryDownloadProxy('container:owned','/tmp','image')
    c=['docker','run','--network','container:owned','-e','AGENTTX_NO_PROXY=1','--entrypoint','/python','image','inside']
    out=helper.apply(c)
    assert out.index('AGENTTX_NO_PROXY=0')>out.index('AGENTTX_NO_PROXY=1')
    assert 'NO_PROXY=localhost,127.0.0.1,::1,10.249.44.127' in out
    assert out[-4:]==c[-4:] and c[-4:]==['--entrypoint','/python','image','inside']
    with pytest.raises(AssertionError):p.RetryDownloadProxy('host','/tmp','image')


def test_ordinary_task_never_starts_proxy(monkeypatch):
    monkeypatch.delenv('AGENTTX_DOWNLOAD_PROXY_RETRY',raising=False)
    stack=object.__new__(services.TaskServices)
    stack.evidence=None;stack.network='container:own';stack.client_id=None;stack.client={}
    monkeypatch.setattr(p.RetryDownloadProxy,'start',lambda self:(_ for _ in ()).throw(AssertionError('Unexpected proxy')))
    assert 'HTTP_PROXY' not in ' '.join(stack.apply(['docker','run','--network','none','--entrypoint','/python','image']))
