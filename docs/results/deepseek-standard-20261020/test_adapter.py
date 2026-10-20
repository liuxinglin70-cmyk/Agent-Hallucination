"""Controlled offline adapter checks: no real endpoint or credentials."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('paper_baseline', HERE/'baseline.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = b.OUT
        b.OUT = Path(self.temp.name)
        b.STOP.clear()
        self.case = 'BFCL/000.json'
        self.body = {'messages': [{'role':'user','content':'frozen author prompt'}], 'max_tokens':1024, 'thinking':{'type':'disabled'}}
        b.save(b.OUT/'requests'/self.case, self.body)
        self.row = {'case':self.case,'request_sha256':b.sha(b.OUT/'requests'/self.case)}
        self.policy = {'endpoint':'http://controlled.invalid','timeout_seconds_per_request':1}

    def tearDown(self):
        b.OUT = self.previous
        b.STOP.clear()
        self.temp.cleanup()

    def response(self, content='{"is_hallucination":true,"hallucination_step":3,"hallucination_reason":"fixture"}', **message):
        return 200, json.dumps({'model':'fixture','choices':[{'message':{'content':content,**message},'finish_reason':'stop'}],
                                'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}}).encode()

    def test_exact_author_lenient_parser(self):
        obj=b.parse('```json\n{"is_hallucinated": True, "hallucination_step": Step 3, "hallucination_reason": "x"}\n```')
        self.assertIs(obj['is_hallucination'], True)
        self.assertEqual(obj['hallucination_step'],3)

    def test_json_retry_keeps_exact_payload_and_all_usage(self):
        with patch('detector.provider._isolated_transport',side_effect=[self.response('bad'),self.response()]) as p:
            r=b.run_case(self.row,self.policy,'fixture-secret')
        self.assertEqual(r['state'],'accepted')
        self.assertEqual(len(r['attempts']),2)
        self.assertEqual(p.call_args_list[0].kwargs['body'],p.call_args_list[1].kwargs['body'])
        self.assertEqual(sum(x['usage']['total_tokens'] for x in r['attempts']),60)
        with patch('detector.provider._isolated_transport',side_effect=AssertionError('must not replay')):
            self.assertEqual(b.run_case(self.row,self.policy,'fixture-secret'),r)

    def test_incompatible_reasoning_stops_dispatch(self):
        with patch('detector.provider._isolated_transport',return_value=self.response(reasoning_content='vendor ignored disabled')):
            r=b.run_case(self.row,self.policy,'fixture-secret')
        self.assertTrue(b.STOP.is_set())
        self.assertEqual(r['state'],'unresolved')
        self.assertTrue((b.OUT/'attention.json').exists())

    def test_parseable_missing_fields_not_retried_per_author(self):
        with patch('detector.provider._isolated_transport',return_value=self.response('{}')) as p:
            r=b.run_case(self.row,self.policy,'fixture-secret')
        self.assertEqual(p.call_count,1)
        self.assertEqual(r['state'],'unresolved')

    def test_quota_error_stops_without_retries_or_invented_usage(self):
        with patch('detector.provider._isolated_transport',return_value=(402,b'{"error":"quota"}')) as p:
            r=b.run_case(self.row,self.policy,'fixture-secret')
        self.assertEqual(p.call_count,1)
        self.assertTrue(b.STOP.is_set())
        self.assertTrue(r['attempts'][0]['usage_missing'])
        self.assertEqual((b.OUT/'attempts/BFCL/000/1-1/response.bin').read_bytes(),b'{"error":"quota"}')

    def test_uncommitted_attempt_not_silently_replayed(self):
        (b.OUT/'attempts/BFCL/000/interrupted').mkdir(parents=True)
        with patch('detector.provider._isolated_transport',side_effect=AssertionError('must not replay')):
            self.assertIsNone(b.run_case(self.row,self.policy,'fixture-secret'))
        self.assertTrue(b.STOP.is_set())

if __name__=='__main__':
    unittest.main()
