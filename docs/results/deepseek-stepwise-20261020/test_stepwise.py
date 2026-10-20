"""Unique changed critical paths for author stepwise control; controlled HTTP only."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('step_under_test',HERE/'stepwise.py')
s=importlib.util.module_from_spec(spec); spec.loader.exec_module(s)

class StepTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.previous=s.OUT
        s.OUT=Path(self.temp.name); s.STOP.clear()
        self.case='BFCL/fixture.json'
        self.row={'case':self.case,'prefixes':[{'index':i,'source_step':10+i,'request_path':f'requests/{i}.json'} for i in range(1,4)]}
        for p in self.row['prefixes']:
            s.save(s.OUT/p['request_path'],{'messages':[{'role':'user','content':f'prefix through {p["index"]}'}],'thinking':{'type':'disabled'},'max_tokens':1024})
            p['request_sha256']=s.sha(s.OUT/p['request_path'])
        self.policy={'endpoint':'http://controlled.invalid','timeout_seconds_per_request':1}

    def tearDown(self):
        s.OUT=self.previous; s.STOP.clear(); self.temp.cleanup()

    def rec(self,obj): return {'decoded':obj}

    def response(self,content):
        return 200,json.dumps({'model':'fixture','choices':[{'message':{'content':content},'finish_reason':'stop'}],
                               'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}}).encode()

    def test_first_true_stops_and_uses_current_source_not_model_step(self):
        with patch.object(s,'query',side_effect=[self.rec({'is_hallucination':False}),self.rec({'is_hallucination':True,'hallucination_step':999,'hallucination_reason':'x'})]) as q:
            result=s.run_case(self.row,self.policy,'fixture-key')
        self.assertEqual(q.call_count,2)
        self.assertEqual(result['prediction']['hallucination_step'],12)
        self.assertEqual(result['prefix_indices'],[1,2])
        with patch.object(s,'query',side_effect=AssertionError('must not rerun')):
            self.assertEqual(s.run_case(self.row,self.policy,'fixture-key'),result)

    def test_invalid_intermediate_advances_final_false_clears_reason(self):
        with patch.object(s,'query',side_effect=[self.rec(None),self.rec({}),self.rec({'is_hallucination':False,'hallucination_reason':'ignored'})]):
            result=s.run_case(self.row,self.policy,'fixture-key')
        self.assertEqual(result['prediction'],{'is_hallucination':False,'hallucination_step':None,'hallucination_reason':None})
        self.assertEqual(result['prefix_indices'],[1,2,3])

    def test_unusable_final_does_not_invent_clean(self):
        with patch.object(s,'query',side_effect=[self.rec({'is_hallucination':False}),self.rec({'is_hallucination':'true'}),self.rec(None)]):
            result=s.run_case(self.row,self.policy,'fixture-key')
        self.assertIsNone(result['prediction'])
        self.assertEqual(result['state'],'unresolved')

    def test_step_response_requires_no_step_field_and_retry_identical_payload(self):
        with patch('detector.provider._isolated_transport',side_effect=[self.response('invalid'),self.response('{"is_hallucination":false,"hallucination_reason":null}')]) as transport:
            result=s.query(self.case,self.row['prefixes'][0],self.policy,'fixture-key')
        self.assertEqual(result['state'],'decoded')
        self.assertIs(result['decoded']['is_hallucination'],False)
        self.assertEqual(len(result['attempts']),2)
        self.assertEqual(transport.call_args_list[0].kwargs['body'],transport.call_args_list[1].kwargs['body'])
        self.assertEqual(sum(x['usage']['total_tokens'] for x in result['attempts']),60)
        with patch('detector.provider._isolated_transport',side_effect=AssertionError('no replay')):
            self.assertEqual(s.query(self.case,self.row['prefixes'][0],self.policy,'fixture-key'),result)

    def test_decoded_missing_fields_not_regenerated(self):
        with patch('detector.provider._isolated_transport',return_value=self.response('{}')) as p:
            result=s.query(self.case,self.row['prefixes'][0],self.policy,'fixture-key')
        self.assertEqual(p.call_count,1)
        self.assertEqual(result['decoded'],{})

    def test_quota_error_stops_and_preserves_missing_usage(self):
        with patch('detector.provider._isolated_transport',return_value=(402,b'{"error":"quota"}')) as p:
            result=s.query(self.case,self.row['prefixes'][0],self.policy,'fixture-key')
        self.assertEqual(p.call_count,1); self.assertTrue(s.STOP.is_set())
        self.assertTrue(result['attempts'][0]['usage_missing'])

if __name__=='__main__': unittest.main()
