import importlib.util,json,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('queue',Path(__file__).with_name('run_tb_network_resume_20261023.py'));q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
class Tests(unittest.TestCase):
 def row(self,p,attempt=0,success=False,complete=False):
  path=p/str(attempt)/'host-result.json';path.parent.mkdir();path.write_text(json.dumps(dict(success=success,api_usage=dict(total_tokens=100,complete=complete))))
  return dict(task='a',harness='sweagent',policy='causal',state='completed' if complete else 'infrastructure_error',expected_context='compact',result_path=str(path),finished_at='2026-10-23T02:00:00+00:00',attempt=attempt)
 def test_incident_gets_one_new_attempt_preserving_two_previous(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);first=self.row(p);retry=self.row(p,1);retries=[retry];before=[Path(r['result_path']).read_bytes() for r in [first,retry]]
   q.add_network_retries([first],retries);self.assertEqual(len(retries),2);self.assertEqual(retries[1]['attempt'],2)
   q.add_network_retries([first],retries);self.assertEqual(len(retries),2)
   self.assertEqual(before,[Path(r['result_path']).read_bytes() for r in [first,retry]])
 def test_pending_original_retry_is_reused_not_duplicated(self):
  with tempfile.TemporaryDirectory() as d:
   first=self.row(Path(d));retry=dict(first,state='pending',attempt=1);retries=[retry]
   q.add_network_retries([first],retries);self.assertEqual(len(retries),1);self.assertEqual(retry['network_repair_cohort'],q.NAME)
 def test_latest_success_not_retried(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);first=self.row(p);retry=self.row(p,1,True,True);retries=[retry]
   q.add_network_retries([first],retries);self.assertEqual(len(retries),1)
 def test_cumulative_bill_includes_every_attempt_excludes_unknown(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);first=self.row(p);retry=self.row(p,1);repair=self.row(p,2,True,True);repair['network_repair_cohort']=q.NAME
   report=q.report([first],[retry,repair]);self.assertEqual(report['known_cumulative_tokens'],300);self.assertEqual(report['attempts_with_incomplete_usage'],2);self.assertEqual(report['network_repair_successful'],1)
 def test_network_repair_priority_before_new_tasks(self):
  row=dict(state='pending',network_repair_cohort=q.NAME);self.assertEqual(q.select([dict(state='pending')],[row],{}),(row,True))
if __name__=='__main__':unittest.main()
