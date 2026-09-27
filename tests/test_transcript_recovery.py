"""Regression cases from the paginated Blender session, with synthetic bodies."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_continuity as fixtures
from jcm.adapter import hook, public_item, recover_source
from jcm.bootstrap import existing
from jcm.util import JCMError, digest, encode


class TranscriptRecoveryTests(unittest.TestCase):
    setUp = fixtures.ContinuityTests.setUp
    tearDown = fixtures.ContinuityTests.tearDown
    transcript = fixtures.ContinuityTests.transcript
    user_line = fixtures.ContinuityTests.user_line

    def test_segment_discovery_preserves_supported_history_and_reports_old_version(self):
        old = self.transcript().rename(self.logs / 'rollout-old-prior.jsonl')
        old.write_text(old.read_text().replace('0.158.0-alpha.2.1', '0.155.0-alpha.16.4'))
        for ordinal, suffix in [(100, 'a'), (200, 'b')]:
            path = self.transcript()
            meta = json.loads(path.read_bytes())
            meta['payload']['history_base'] = {'thread_id': 'prior', 'end_ordinal_exclusive': ordinal}
            path.write_bytes(encode(meta) + b'\n' + self.user_line(suffix, suffix))
            path.rename(self.logs / ('rollout-date-prior_' + suffix + '.jsonl'))
        result = existing(self.store, 'prior', install=False, follow=False)
        self.assertEqual(result['new_events'], 2)
        self.assertTrue(result['source']['path'].endswith('_b.jsonl'))
        self.assertIn('UNSUPPORTED_TRANSCRIPT_VERSION', result['gaps'])
        self.assertEqual(result['coverage'], 'partial')
        self.assertEqual(existing(self.store, 'prior', install=False, follow=False)['new_events'], 0)

    def test_large_file_streams_without_read_bytes_and_detects_prefix_rewrite(self):
        path = self.transcript()
        with path.open('ab') as f:
            padding = encode({'type': 'token_usage_record', 'padding': 'x' * 999_950}) + b'\n'
            for _ in range(34):
                f.write(padding)
            f.write(self.user_line('preserve latest'))
        self.assertGreater(path.stat().st_size, 32_000_000)
        original_read = Path.read_bytes
        def read_bytes(p):
            if p == path:
                raise AssertionError('Transcript must be streamed')
            return original_read(p)
        with patch.object(Path, 'read_bytes', read_bytes):
            result = existing(self.store, 'prior', path, install=False, follow=False)
        self.assertEqual(result['new_events'], 1)
        self.assertEqual(result['source']['offset'], path.stat().st_size)
        self.assertEqual(result['backlog_bytes'], 0)
        with path.open('r+b') as f:
            f.seek(1024); f.write(b'y')
        recover_source(self.store, result['source'])
        row = self.store.db.execute('SELECT * FROM sources').fetchone()
        self.assertEqual(row['generation'], 1)
        self.assertEqual(len(self.store.events()), 1)

    def test_mcp_and_mixed_user_content_retain_text_without_image_bytes(self):
        path = self.transcript()
        user = json.loads(self.user_line('keep nearby text'))
        user['payload']['item']['content'] += [{'type': 'local_image', 'path': '/tmp/reference.png'},
            {'type': 'image', 'image_url': 'data:image/png;base64,BINARY-MARKER'}]
        mcp = {'type': 'event_msg', 'payload': {'type': 'item_completed', 'turn_id': 't', 'item': {
            'type': 'McpToolCall', 'id': 'mcp1', 'server': 'blender', 'tool': 'render',
            'arguments': {'frame': 42}, 'status': 'completed', 'result': {'isError': False, 'content': [
                {'type': 'text', 'text': 'rendered frame 42'},
                {'type': 'image', 'data': 'BINARY-MARKER' * 100_000, 'mimeType': 'image/png'}]}}}}
        # The real Node REPL transcript wraps an image-bearing result in JSON text.
        mcp['payload']['item']['result'] = {'content': [{'type': 'text',
            'text': json.dumps(mcp['payload']['item']['result'])}]}
        with path.open('ab') as f:
            f.write(encode(user) + b'\n' + encode(mcp) + b'\n')
        result = existing(self.store, 'prior', path, install=False, follow=False)
        self.assertEqual(result['new_events'], 2)
        bodies = str([self.store.blob(e['blob']) for e in self.store.events()])
        for text in ('keep nearby text', '/tmp/reference.png', 'rendered frame 42', 'blender'):
            self.assertIn(text, bodies)
        self.assertNotIn('BINARY-MARKER', bodies)
        self.assertEqual(public_item(mcp)['identity'], 'tool:mcp1')

    def test_failed_scan_reports_durable_cursor_and_does_not_start_follower(self):
        path = self.transcript()
        with path.open('ab') as f:
            f.write(self.user_line('valid'))
            f.write(b'{bad json}\n')
        result = existing(self.store, 'prior', path, install=False, follow=False)
        self.assertEqual(result['stage'], 'blocked')
        self.assertEqual(result['source']['status'], 'malformed_line')
        self.assertGreater(result['backlog_bytes'], 0)
        self.assertIn('TRANSCRIPT_MALFORMED_LINE', result['gaps'])
        saved = self.store.db.execute("SELECT value FROM meta WHERE key='bootstrap_existing:prior'").fetchone()
        self.assertEqual(json.loads(saved[0])['stage'], 'blocked')

    def test_oversized_line_is_bounded_and_does_not_acknowledge_tail(self):
        path = self.transcript()
        offset = path.stat().st_size
        with path.open('ab') as f:
            f.write(b'x' * 4096 + b'\n')
            f.write(self.user_line('not reached'))
        with patch('jcm.adapter.MAX_LINE_BYTES', 2048):
            result = existing(self.store, 'prior', path, install=False, follow=False)
        self.assertEqual(result['stage'], 'blocked')
        self.assertEqual(result['source']['offset'], offset)
        self.assertIn('TRANSCRIPT_LINE_TOO_LARGE', result['gaps'])
        self.assertEqual(result['new_events'], 0)

    def test_hook_mcp_media_has_same_admission_boundary(self):
        hook(self.store, {'hook_event_name': 'PostToolUse', 'session_id': 'prior',
            'turn_id': 't', 'cwd': str(self.root), 'tool_use_id': 'mcp1', 'tool_name': 'render',
            'tool_response': {'content': [{'type': 'text', 'text': 'render complete'},
                {'type': 'image', 'mimeType': 'image/png', 'data': 'BINARY' * 200_000}]}})
        blob = self.store.blob(self.store.events()[0]['blob'])
        self.assertIn('render complete', str(blob))
        self.assertNotIn('BINARY', str(blob))

    def test_discovery_still_refuses_ambiguous_or_foreign_segments(self):
        self.transcript().rename(self.logs / 'rollout-one-prior_a.jsonl')
        other = self.transcript().rename(self.logs / 'rollout-two-prior_b.jsonl')
        with self.assertRaisesRegex(JCMError, 'AMBIGUOUS'):
            existing(self.store, 'prior', install=False, follow=False)
        meta = json.loads(other.read_bytes())
        meta['payload']['cwd'] = str(self.base)
        other.write_bytes(encode(meta) + b'\n')
        with self.assertRaisesRegex(JCMError, 'PROJECT_MISMATCH'):
            existing(self.store, 'prior', install=False, follow=False)
        self.assertEqual(self.store.events(), [])

    def test_failed_event_is_reported_and_retry_catches_up_from_committed_offset(self):
        path = self.transcript()
        with path.open('ab') as f:
            f.write(self.user_line('first', 't1'))
            f.write(self.user_line('second', 't2'))
        original = self.store.capture
        def capture(**args):
            if args['turn'] == 't2':
                raise JCMError('TEST_CAPTURE_FAILURE')
            return original(**args)
        with patch.object(self.store, 'capture', capture):
            result = existing(self.store, 'prior', path, install=False, follow=False)
        self.assertEqual(result['stage'], 'blocked')
        self.assertEqual(result['new_events'], 1)
        self.assertIn('TEST_CAPTURE_FAILURE', result['gaps'])
        self.assertGreater(result['backlog_bytes'], 0)
        retry = existing(self.store, 'prior', path, install=False, follow=False)
        self.assertEqual(retry['stage'], 'captured')
        self.assertEqual(retry['new_events'], 1)
        self.assertEqual(retry['backlog_bytes'], 0)
        self.assertTrue(all(e['revision'] == 1 for e in self.store.events()))

    def test_parser_upgrade_replays_skipped_items_using_new_cursor(self):
        path = self.transcript()
        with path.open('ab') as f:
            f.write(self.user_line('missed by previous parser'))
        legacy = digest([str(path), 'prior', 'codex-0.158-public-items-v1'])
        self.store.set_cursor({'key': legacy, 'session': 'prior', 'path': str(path),
            'generation': 0, 'offset': path.stat().st_size, 'prefix_hash': digest(path.read_bytes()),
            'inode': path.stat().st_ino, 'status': 'read_to_offset'})
        result = existing(self.store, 'prior', path, install=False, follow=False)
        self.assertNotEqual(result['source']['key'], legacy)
        self.assertEqual(result['new_events'], 1)
        self.assertEqual(result['backlog_bytes'], 0)
