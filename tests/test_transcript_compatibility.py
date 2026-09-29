"""Compatibility is determined by public record structure, not host versions."""
import json
import unittest

import test_continuity as fixtures
from jcm.adapter import register_transcript
from jcm.bootstrap import existing
from jcm.entry import session_items
from jcm.request_source import current_request
from jcm.util import JCMError, encode


class TranscriptCompatibilityTests(unittest.TestCase):
    setUp = fixtures.ContinuityTests.setUp
    tearDown = fixtures.ContinuityTests.tearDown
    transcript = fixtures.ContinuityTests.transcript
    user_line = fixtures.ContinuityTests.user_line

    def test_host_version_does_not_change_capture_or_request_identity(self):
        tokens = set()
        for version in ('0.155.0-alpha.9.2', '0.158.0-alpha.2.1', '999.0-future', '', None):
            with self.subTest(version=version):
                path = self.transcript()
                meta = json.loads(path.read_bytes())
                if version is None:
                    del meta['payload']['cli_version']
                else:
                    meta['payload']['cli_version'] = version
                path.write_bytes(encode(meta) + b'\n' + self.user_line('preserve this request'))
                result = existing(self.store, 'prior', path, install=False, follow=False)
                self.assertEqual(result['stage'], 'captured')
                self.assertEqual(result['request_status'], 'linked')
                self.assertEqual(result['session_event_count'], 1)
                self.assertEqual(result['backlog_bytes'], 0)
                self.assertNotIn('UNSUPPORTED_TRANSCRIPT_VERSION', result['gaps'])
                tokens.add(result['request_token'])
        self.assertEqual(len(tokens), 1)

    def test_metadata_still_requires_session_and_absolute_project_identity(self):
        path = self.transcript()
        cases = [([], 'SCHEMA'), ({'type': 'session_meta', 'payload': []}, 'SCHEMA'),
                 ({'type': 'session_meta', 'payload': {'id': 'prior', 'cwd': None}}, 'SCHEMA'),
                 ({'type': 'session_meta', 'payload': {'id': 'prior', 'cwd': '.'}}, 'SCHEMA')]
        for record, code in cases:
            with self.subTest(record=record):
                path.write_bytes(encode(record) + b'\n')
                with self.assertRaisesRegex(JCMError, code):
                    register_transcript(self.store, path, 'prior')
        self.assertEqual(self.store.events(), [])

    def test_unrecognized_record_preserves_reference_and_prevents_old_request_fallback(self):
        path = self.transcript()
        future = {'type': 'event_msg', 'payload': {'type': 'item_completed', 'turn_id': 'future',
                  'item': {'type': 'FuturePublicMessage', 'id': 'future-id',
                           'content': [{'type': 'text', 'text': 'UNKNOWN-CONTENT-MUST-NOT-BE-INFERRED'}]}}}
        path.write_bytes(path.read_bytes() + self.user_line('old request') + encode(future) + b'\n')
        result = existing(self.store, 'prior', path, install=False, follow=False)
        self.assertEqual(result['stage'], 'captured')
        self.assertEqual(result['backlog_bytes'], 0)
        self.assertEqual(result['request_status'], 'unsupported')
        self.assertIsNone(result['request_token'])
        self.assertIn('UNSUPPORTED_PUBLIC_ITEM', result['gaps'])
        markers = [e for e in self.store.events() if e['role'] == 'unsupported_request']
        self.assertEqual(len(markers), 1)
        marker = self.store.blob(markers[0]['blob'])
        self.assertEqual(marker['source']['path'], str(path))
        self.assertGreater(marker['source']['offset'], 0)
        self.assertNotIn('UNKNOWN-CONTENT', str(marker))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)
        items, gaps = session_items(self.cfg, 'prior', path, requests_only=True)
        with self.assertRaisesRegex(JCMError, 'UNSUPPORTED_CURRENT_REQUEST'):
            current_request(items)
        self.assertIn('UNSUPPORTED_PUBLIC_ITEM', gaps)
        with path.open('ab') as stream:
            stream.write(self.user_line('new supported request', 'new'))
        retry = existing(self.store, 'prior', path, install=False, follow=False)
        self.assertEqual(retry['request_status'], 'linked')
        request = self.store.resolve_request(retry['request_token'])
        self.assertEqual(self.store.material(self.store.event(request['event_id']))['text'],
                         'new supported request')

    def test_malformed_public_payload_does_not_crash_or_reuse_old_request(self):
        bad_records = [
            {'type': 'event_msg', 'payload': []},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': []}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'turn_id': 'broken',
                'item': {'type': 'UserMessage', 'id': 'bad', 'content': [{'type': 'text', 'text': 1}]}}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'turn_id': 'broken',
                'item': {'type': 'UserMessage', 'id': 'bad', 'content': [None]}}},
        ]
        for record in bad_records:
            with self.subTest(record=record):
                path = self.transcript()
                path.write_bytes(path.read_bytes() + self.user_line('old request') + encode(record) + b'\n')
                items, gaps = session_items(self.cfg, 'prior', path, requests_only=True)
                self.assertTrue(gaps)
                with self.assertRaisesRegex(JCMError, 'UNSUPPORTED_CURRENT_REQUEST'):
                    current_request(items)

    def test_incomplete_or_invalid_tail_cannot_select_previous_request(self):
        for tail, gap in ((b'{"type":', 'TRANSCRIPT_PARTIAL_LINE'),
                          (b'{bad json}\n', 'TRANSCRIPT_MALFORMED_LINE')):
            with self.subTest(gap=gap):
                path = self.transcript()
                path.write_bytes(path.read_bytes() + self.user_line('old request') + tail)
                items, gaps = session_items(self.cfg, 'prior', path, requests_only=True)
                self.assertIn(gap, gaps)
                with self.assertRaisesRegex(JCMError, 'UNSUPPORTED_CURRENT_REQUEST'):
                    current_request(items)

    def test_known_public_tool_shapes_do_not_obscure_current_user_request(self):
        path = self.transcript()
        records = [
            {'type': 'FileChange', 'id': 'patch', 'status': 'completed',
             'changes': {'a.py': {'diff': '+preserve output'}}},
            {'type': 'CollabAgentToolCall', 'id': 'agent-call', 'tool': 'wait_agent', 'status': 'completed'},
            {'type': 'Extension', 'id': 'search', 'kind': 'web.search', 'query': 'source evidence',
             'results': [{'type': 'image', 'data': 'INLINE-BYTES-MUST-BE-OMITTED'}]},
            {'type': 'SubAgentActivity', 'id': 'activity', 'kind': 'completed', 'agent_thread_id': 'child'},
        ]
        with path.open('ab') as stream:
            stream.write(self.user_line('current request'))
            for item in records:
                stream.write(encode({'type': 'event_msg', 'payload': {
                    'type': 'item_completed', 'turn_id': 't1', 'item': item}}) + b'\n')
        result = existing(self.store, 'prior', path, install=False, follow=False)
        self.assertEqual(result['request_status'], 'linked')
        self.assertFalse(result['gaps'])
        bodies = str([self.store.blob(e['blob']) for e in self.store.events()])
        self.assertIn('preserve output', bodies)
        self.assertIn('source evidence', bodies)
        self.assertNotIn('INLINE-BYTES-MUST-BE-OMITTED', bodies)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 4)


if __name__ == '__main__':
    unittest.main()
