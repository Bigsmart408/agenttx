import pytest

from experiments.scripts.validate_tb_ready_local import classify_junit


@pytest.mark.parametrize('xml,rc,expected', [
    ('<testsuites><testsuite><testcase><failure type="AssertionError"/></testcase></testsuite></testsuites>', 1, 'baseline_failed'),
    ('<testsuites><testsuite><testcase><failure type="ModuleNotFoundError"/></testcase></testsuite></testsuites>', 1, 'test_dependency_missing'),
    ('<testsuites><testsuite><testcase><error message="fixture unavailable"/></testcase></testsuite></testsuites>', 2, 'test_setup_or_collection_error'),
    ('<testsuites><testsuite><testcase/></testsuite></testsuites>', 0, 'baseline_passed'),
    ('<testsuites><testsuite><testcase><skipped/></testcase></testsuite></testsuites>', 0, 'no_executed_tests'),
    ('<testsuites/>', 124, 'timeout'),
])
def test_baseline_task_failures_remain_distinct_from_environment_failures(tmp_path, xml, rc, expected):
    path = tmp_path/'tests.xml'
    path.write_text(xml)
    assert classify_junit(path, rc)[0] == expected


def test_missing_report_cannot_establish_test_environment_readiness(tmp_path):
    assert classify_junit(tmp_path/'missing.xml', 0)[0] == 'no_test_report'
