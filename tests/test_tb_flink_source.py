import hashlib
import pytest
from experiments.scripts.repair_tb_flink_source import original_commit,COMMIT,TREE


def metadata():
    return dict(verification=dict(reason='unsigned'),tree=dict(sha=TREE),
        parents=[dict(sha='4c85ea51270873d99714b42108052474a3b0ec3f')],
        author=dict(name='Zhu Zhu',email='zhuzh@apache.org',date='2020-09-09T14:19:03Z'),
        committer=dict(name='Zhu Zhu',email='zhuzh@apache.org',date='2020-09-09T14:19:03Z'),
        message='Commit for release 1.11.2')


def test_restored_commit_matches_published_git_object_identity():
    raw=original_commit(metadata())
    assert hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==COMMIT
    assert b'1599661143 +0800\n' in raw


def test_modified_metadata_cannot_be_installed_as_the_upstream_commit():
    obj=metadata();obj['message']='modified source'
    with pytest.raises(RuntimeError,match='refuse substitute history'):original_commit(obj)
