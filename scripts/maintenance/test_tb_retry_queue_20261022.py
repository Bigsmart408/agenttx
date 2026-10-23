import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('retry_queue',Path(__file__).with_name('run_tb_retry_queue_20261022.py'))
queue=importlib.util.module_from_spec(spec);spec.loader.exec_module(queue)

class RetryQueueTests(unittest.TestCase):
    def row(self,root,task,policy,tokens,success,state='completed',complete=True,attempt=0):
        p=root/f'{task}-{policy}-{attempt}.json'
        p.write_text(json.dumps(dict(success=success,api_usage=dict(total_tokens=tokens,complete=complete))))
        return dict(task=task,harness='sweagent',policy=policy,state=state,
            expected_context='compact',result_path=str(p))

    def test_single_retry_per_failed_cell_preserves_original(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);bad=self.row(p,'bad','causal',100,False);good=self.row(p,'good','causal',50,True)
            raw=Path(bad['result_path']).read_bytes();retries=[]
            queue.add_retries([bad,good],retries);self.assertEqual(len(retries),1)
            retries[0]['state']='completed'
            queue.add_retries([bad,good],retries);self.assertEqual(len(retries),1)
            self.assertEqual(Path(bad['result_path']).read_bytes(),raw)
            self.assertEqual(retries[0]['original_result_sha256'],queue.sha(bad['result_path']))

    def test_fair_selection_between_first_and_retry(self):
        first=[dict(state='pending',task='new')];retry=[dict(state='pending',task='retry')]
        active={1:dict(retry=True),2:dict(retry=True),3:dict(retry=True),4:dict(retry=True)}
        self.assertEqual(queue.select(first,retry,active),(first[0],False))
        self.assertEqual(queue.select(first,retry,{}),(retry[0],True))

    def test_cumulative_cost_and_incomplete_attempt_exclusion(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            first=[self.row(p,'a','causal',100,False),self.row(p,'a','clean_recovery',200,True),
                self.row(p,'b','causal',1000,False,complete=False,state='infrastructure_error'),
                self.row(p,'b','clean_recovery',2000,True)]
            retry=[self.row(p,'a','causal',50,True,attempt=1),self.row(p,'b','causal',500,True,attempt=1)]
            report=queue.retry_report(first,retry);pair=report['cumulative_paired_success']['sweagent']['clean_recovery']
            self.assertEqual(pair['both_success_pairs'],1)
            self.assertEqual(pair['causal_cumulative_tokens'],150)
            self.assertEqual(pair['saving_pct'],25)
            self.assertEqual(report['known_retry_tokens'],550)
            self.assertEqual(report['known_cumulative_tokens'],3850)

    def test_latest_outcome_is_used_without_best_attempt_selection(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);first=[self.row(p,'a','causal',100,True),self.row(p,'a','clean_recovery',200,True)]
            retry=[self.row(p,'a','causal',50,False,attempt=1)]
            report=queue.retry_report(first,retry)
            self.assertEqual(report['cumulative_paired_success']['sweagent']['clean_recovery']['both_success_pairs'],0)

if __name__=='__main__':unittest.main()
