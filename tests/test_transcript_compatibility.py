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

    def test_native_image_generation_preserves_identity_labels_status_and_path_without_inline_bytes(self):
        path=self.transcript()
        with path.open('ab') as stream:
            stream.write(self.user_line('Propose three character designs.'))
            for label in ('A','C','B'):
                stream.write(encode({'type':'event_msg','payload':{'type':'item_completed','turn_id':'t1',
                    'item':{'type':'Extension','kind':'image_gen.generation','id':'image-'+label,
                        'status':'completed','failure':None,'savedPath':'/images/'+label+'.png',
                        'revisedPrompt':'Design proposal '+label,'transparentBackground':False,
                        'result':'BASE64-MUST-NOT-ENTER-STORAGE'*10000}}})+b'\n')
            stream.write(encode({'type':'event_msg','payload':{'type':'item_completed','turn_id':'t1',
                'item':{'type':'Extension','kind':'image_gen.generation','id':'image-failed','status':'failed',
                        'failure':{'message':'Generation failed'},'savedPath':None,'revisedPrompt':'Retry proposal',
                        'result':None}}})+b'\n')
        result=existing(self.store,'prior',path,install=False,follow=False)
        self.assertEqual(result['request_status'],'linked')
        self.assertFalse(result['gaps'])
        events=[e for e in self.store.events() if e['role']=='tool']
        payloads=[self.store.blob(e['blob']) for e in events]
        self.assertNotIn('BASE64-MUST-NOT-ENTER-STORAGE',str(payloads))
        self.assertEqual([p['public_item']['id'] for p in payloads],['image-A','image-C','image-B','image-failed'])
        self.assertEqual([p['public_item']['savedPath'] for p in payloads[:3]],['/images/A.png','/images/C.png','/images/B.png'])
        self.assertEqual(payloads[-1]['public_item']['status'],'failed')
        self.assertEqual(payloads[-1]['public_item']['failure'],{'message':'Generation failed'})
        self.assertTrue(all('not established' in p['text'] for p in payloads))

    def legacy_image_marker(self):
        from jcm.adapter import unresolved_record
        from jcm.util import digest
        path = self.transcript()
        offset = len(path.read_bytes())
        record = {'type':'event_msg','payload':{'type':'item_completed','turn_id':'t1','item':{
            'type':'Extension','kind':'image_gen.generation','id':'native-image','status':'completed',
            'failure':None,'savedPath':'/images/C.png','revisedPrompt':'Concept C','result':'INLINE-OMIT'}}}
        with path.open('ab') as stream:
            stream.write(encode(record)+b'\n')
        item = unresolved_record(record,'UNSUPPORTED_PUBLIC_ITEM',path,offset,digest(encode(record)))
        eid = self.store.capture(session='prior',source_key='legacy-image',snapshot={},**item)
        self.store.gap('UNSUPPORTED_PUBLIC_ITEM','legacy-image')
        return path, record, eid

    def test_historical_image_repair_preserves_order_identity_lineage_and_is_idempotent(self):
        from jcm.adapter import repair_unsupported_images, public_item
        path, record, eid = self.legacy_image_marker()
        before = self.store.event(eid)
        self.store.db.execute('INSERT INTO packs VALUES (?,?,?,?,?,?,0)',
                              ('previous','token',self.cfg['epoch'],self.store.put_blob({}),'{}','created'))
        self.assertEqual(repair_unsupported_images(self.store),1)
        self.assertEqual(self.store.db.execute("SELECT invalid FROM packs WHERE id='previous'").fetchone()[0],1)
        after = self.store.event(eid)
        self.assertEqual((after['seq'],after['session'],after['turn']),(before['seq'],'prior','t1'))
        self.assertEqual((after['revision'],after['kind'],after['role']),(2,'tool_result','tool'))
        body = self.store.blob(after['blob'])
        self.assertEqual(body['reinterpreted_from']['blob'],before['blob'])
        self.assertNotIn('INLINE-OMIT',str(body))
        self.assertEqual(repair_unsupported_images(self.store),0)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM gaps WHERE code='UNSUPPORTED_PUBLIC_ITEM'").fetchone())
        replay = self.store.capture(session='prior',source_key='later-source',snapshot={},**public_item(record,'prior'))
        self.assertEqual(replay,eid)
        self.assertEqual(len(self.store.events()),1)
        self.assertEqual(self.store.event(eid)['seq'],before['seq'])

    def test_historical_image_repair_rejects_changed_bytes_and_rolls_back_atomically(self):
        from jcm.adapter import repair_unsupported_images
        path, record, eid = self.legacy_image_marker()
        original = path.read_bytes()
        path.write_bytes(original.replace(b'Concept C',b'Concept B'))
        self.assertEqual(repair_unsupported_images(self.store),0)
        self.assertEqual(self.store.event(eid)['role'],'unsupported_request')
        path.write_bytes(original)
        def stop(stage):
            if stage=='before_commit':raise RuntimeError('interrupted publication')
        with self.assertRaisesRegex(RuntimeError,'interrupted publication'):
            repair_unsupported_images(self.store,failpoint=stop)
        self.assertEqual(self.store.event(eid)['revision'],1)
        self.assertTrue(self.store.db.execute("SELECT 1 FROM gaps WHERE code='UNSUPPORTED_PUBLIC_ITEM'").fetchone())
        self.assertFalse(self.store.db.execute("SELECT 1 FROM meta WHERE key LIKE 'event_alias:%'").fetchone())
        self.assertEqual(repair_unsupported_images(self.store),1)

    def test_historical_image_repair_does_not_resurrect_forgotten_or_unadmitted_sources(self):
        from jcm.adapter import repair_unsupported_images
        path, record, eid = self.legacy_image_marker()
        policy = self.store.change_policy(capture_scope={'session':'different','mode':'whole_session'})
        self.assertEqual(repair_unsupported_images(self.store),0)
        self.assertEqual(self.store.event(eid)['role'],'unsupported_request')
        self.store.forget_session('prior')
        self.assertEqual(repair_unsupported_images(self.store),0)
        self.assertEqual(self.store.events(),[])

    def test_forget_between_image_read_and_publication_cannot_resurrect_data(self):
        from jcm.adapter import repair_unsupported_images
        self.legacy_image_marker()
        def forget(stage):
            if stage=='before_publication': self.store.forget_session('prior')
        self.assertEqual(repair_unsupported_images(self.store,failpoint=forget),0)
        self.assertEqual(self.store.events(),[])
        self.assertFalse(self.store.db.execute("SELECT 1 FROM meta WHERE key LIKE 'event_alias:%'").fetchone())


if __name__ == '__main__':
    unittest.main()
