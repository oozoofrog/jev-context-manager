"""Final API delivery, incremental reads and exact evidence boundaries."""
import json
import shlex
import unittest
from unittest.mock import patch

import test_continuity as fixtures
import test_query_context as query_fixtures
from jcm.coordinator import read_pack
from jcm.evidence import lookup
from jcm.metrics import delivery_metrics
from jcm.source_read import inspect_source
from jcm.util import JCMError, encode


class CapsuleDeliveryTests(unittest.TestCase):
    setUp = query_fixtures.QueryContextTests.setUp
    tearDown = query_fixtures.QueryContextTests.tearDown
    capture = query_fixtures.QueryContextTests.capture
    provider = query_fixtures.QueryContextTests.provider
    transport = query_fixtures.QueryContextTests.transport
    focus = staticmethod(query_fixtures.QueryContextTests.focus)
    recover = query_fixtures.QueryContextTests.recover
    corpus = query_fixtures.QueryContextTests.corpus

    def test_delta_requires_retained_same_session_context_and_keeps_correction(self):
        self.corpus()
        base = self.recover('consumer', 'Explain the exception in pause recovery.')
        read = read_pack(self.store, base['pack_id'])
        self.assertTrue(read['required_context_complete'])
        handle = read['context_handle']
        next_pack = self.recover('consumer', 'Describe the special case in pause recovery.')
        delta = read_pack(self.store, next_pack['pack_id'], retained_context=handle)
        self.assertTrue(delta['required_context_complete'])
        self.assertLess(len(encode(delta)), len(encode(read_pack(self.store, next_pack['pack_id']))))
        self.assertNotIn(self.rule, {r['event_id'] for r in delta['pack']['selected_records']})
        corrected = self.recover('consumer', 'Correction: use delay 8 seconds. Explain the exception in pause recovery.')
        change = read_pack(self.store, corrected['pack_id'], retained_context=handle)['pack']
        self.assertTrue(any('8 seconds' in r['text'] for r in change['selected_records']))
        self.assertEqual(change['task_frame']['assertion_delivery']['implementation_status'], 'not_established')
        fresh = self.recover('new-consumer', 'Explain the exception in pause recovery.')
        with self.assertRaisesRegex(JCMError, 'RETAINED_CONTEXT_NOT_APPLICABLE'):
            read_pack(self.store, fresh['pack_id'], retained_context=handle)
        full = read_pack(self.store, fresh['pack_id'])['pack']
        self.assertTrue(any(r['event_id'] == self.rule for r in full['selected_records']))
        with self.assertRaisesRegex(JCMError, 'RETAINED_CONTEXT_NOT_APPLICABLE'):
            read_pack(self.store, corrected['pack_id'], retained_context=base['pack_id'] + ':' + '0' * 64)

    def test_lookup_and_generated_expansion_are_bound_and_metered(self):
        self.corpus()
        pack = self.recover('consumer', 'Explain the exception in pause recovery.')
        read = read_pack(self.store, pack['pack_id'])
        self.assertIn('lookup', read['pack']['evidence_lookup_command'])
        result = lookup(self.store, pack['pack_id'], 'protocol 47')
        source = next(r for r in result['matches'] if r['event_id'] == self.rule)
        self.assertFalse(self.store.db.execute('SELECT 1 FROM meta WHERE key=?', ('source_read:' + self.rule,)).fetchone())
        from jcm.cli import parser, run
        args = shlex.split(source['expand_command'])[len(self.store.config['cli_argv']):]
        value = run(parser().parse_args(['--home', str(self.home), '--repo', str(self.root)] + args))
        self.assertIn('Never resume automatically', value['source']['text'])
        metrics = delivery_metrics(self.store, pack['pack_id'])
        self.assertGreater(metrics['source_expansion_bytes'], 0)
        self.assertGreater(metrics['evidence_lookup_bytes'], 0)
        semantic = lookup(self.store, pack['pack_id'], 'synonym-without-lexical-match', provider=self.provider())
        self.assertEqual(semantic['method'], 'semantic')
        self.assertIn('does not establish absence', semantic['coverage'])
        extra = self.capture('extra', 'new', 'Never admitted into the pack.')
        with self.assertRaisesRegex(JCMError, 'SOURCE_NOT_IN_PACK'):
            inspect_source(self.store, extra, pack_id=pack['pack_id'])
        self.store.db.execute('UPDATE events SET blob=? WHERE id=?', (self.store.put_blob({'text':'tampered'}), self.rule))
        for operation in (lambda: inspect_source(self.store, self.rule, pack_id=pack['pack_id']),
                          lambda: lookup(self.store, pack['pack_id'], 'protocol'),
                          lambda: read_pack(self.store, pack['pack_id'])):
            with self.assertRaisesRegex(JCMError, 'PACK_SOURCE_CHANGED'):
                operation()

    def test_delta_removes_obsolete_fields_and_replaces_whole_records(self):
        from jcm.delivery import delta
        self.corpus()
        base = self.recover('consumer', 'Explain the exception in pause recovery.')
        handle = read_pack(self.store, base['pack_id'])['context_handle']
        old = base['context_views']['brief']
        old['evidence_delivery'] = {'mode': 'continuation'}
        import copy
        old['selected_records'][0]['obsolete_optional_metadata'] = 'old'
        current = copy.deepcopy(old)
        current['selected_records'][0].pop('obsolete_optional_metadata')
        current.pop('evidence_delivery')
        current['selected_records'].reverse()
        updated = copy.deepcopy(base)
        updated['context_views']['brief'] = current
        updated['query_context']['record_order'] = [r['event_id'] for r in current['selected_records']]
        with patch('jcm.source_read.bound_pack', return_value=base):
            changes = delta(self.store, updated, handle)
        self.assertIn('evidence_delivery', changes['removed_fields'])
        records = {r['event_id']: r for r in old['selected_records']}
        records.update({r['event_id']: r for r in changes['selected_records']})
        for event_id in changes['removed_record_ids']:
            records.pop(event_id)
        self.assertEqual([records[eid] for eid in changes['record_order']], current['selected_records'])
        from jcm.delivery import apply_delta
        self.assertEqual(apply_delta(old, changes), {k:v for k,v in current.items() if k != 'page_manifest'})
        legacy = copy.deepcopy(base)
        legacy['query_context'].pop('contract_version')
        with patch('jcm.source_read.bound_pack', return_value=legacy):
            with self.assertRaisesRegex(JCMError, 'RETAINED_CONTEXT_CONTRACT_UNVERIFIED'):
                delta(self.store, updated, handle)

    def test_structural_json_selection_keeps_parent_conditions_and_last_leaf(self):
        from jcm import query_context, representations
        from jcm.source_units import leaves, units
        text = json.dumps({'approved':False, 'scope':'fixture only', 'records':[
            {'detail':'unrelated narration'}, {'outcome':'TARGET_READY', 'qualification':{'hardware':{'exception':'TARGET_NOT_VERIFIED', 'scope':'TARGET_FIXTURE_ONLY'}}}]}, ensure_ascii=False)
        source = self.capture('history', 'json', text, 'tool')
        request_id = self.capture('consumer', 'ask', 'Explain target readiness.')
        material = self.store.material(self.store.event(source))
        request = self.store.material(self.store.event(request_id))
        fragments = leaves(text)
        self.assertTrue(all(text[f['start']:f['end']] for f in fragments))
        self.assertTrue(any(u['source_path'] == ['records', 1, 'qualification', 'hardware', 'exception'] for u in units(material)))
        selected = [{**material, 'reason_codes':[], 'text':text}]
        identity = {'id':'a'*64, 'anchor':request, 'scope':{'selected_source':request}}
        def transport(body, key):
            response = fixtures.fake_http(body, key)
            payload = json.loads(body)
            for name, question in payload['questions'].items():
                if 'query_' not in name:
                    continue
                if question['type'] == 'noul':
                    response['answers'][name]['noul'] = 0
                    continue
                item = payload['state']['items'][int(name.split('_')[0][1:])]
                selected = 'brief' if name.endswith('query_level') else ('core' if 'TARGET_READY' in item['text'] else 'omit')
                response['answers'][name].update(choice=selected, probabilities={k:float(k==selected) for k in question['criteria']})
            return response
        provider = self.provider(transport)
        reps, _ = representations.build(self.store, provider, identity, selected, [material], self.cfg['epoch'])
        with patch('jcm.batching.context_fits', return_value=False):
            reps, _, result = query_context.build(self.store, provider, identity, request, {'read_revision':1},
                                                  selected, [material], reps, self.cfg['epoch'])
        self.assertFalse(result['errors'])
        output = reps[0]['query']['text']
        self.assertIn('TARGET_READY', output)
        self.assertIn('TARGET_NOT_VERIFIED', output)
        self.assertIn('TARGET_FIXTURE_ONLY', output)
        self.assertIn('"approved": false', output)
        self.assertIn('fixture only', output)
        self.assertNotIn('unrelated narration', output)
        self.assertEqual(self.store.material(self.store.event(source))['text'], text)

    def test_trusted_launcher_lineage_and_wrapper_keep_mixed_commands(self):
        from jcm.adapter import internal_command
        old = shlex.join(self.cfg['cli_argv'] + ['status'])
        new = self.store.change_policy(cli_argv=['/new/launcher', '--home', self.cfg['home'], '--repo', self.cfg['root']])
        self.assertTrue(internal_command(new, old))
        self.assertTrue(internal_command(new, 'env PYTHONDONTWRITEBYTECODE=1 ' + old))
        self.assertFalse(internal_command(new, 'env PYTHONPATH=/untrusted ' + old))
        self.assertFalse(internal_command(new, old + ' && cat product.log'))
        self.assertFalse(internal_command(new, old.replace(self.cfg['cli_argv'][0], '/forged/launcher', 1)))

    def test_inconsistent_relation_answers_recheck_partial_correction(self):
        old = self.capture('history', 'old', 'Use delay 5 seconds and preserve protocol 47.')
        original = self.transport
        def transport(body, key):
            response = original(body, key)
            if any(name.endswith('_same_property') for name in response['answers']):
                for name, answer in response['answers'].items():
                    if name.endswith('_relation'):
                        answer.update(choice='unrelated', probabilities={k:float(k=='unrelated') for k in answer['probabilities']})
                    elif name.endswith('_target_scope'):
                        answer.update(choice='partial', probabilities={'whole':.1,'partial':.74,'uncertain':.16})
            return response
        self.transport = transport
        self.recover('consumer', 'Continue pause recovery.')
        pack = self.recover('consumer', 'Correction: use delay 8 seconds. Preserve all other requirements.')
        self.assertTrue(any(a['event_id'] == old and a['state'] == 'disputed' for a in pack['task_frame']['assertions']))
        delivered = read_pack(self.store, pack['pack_id'])['pack']['selected_records']
        self.assertTrue(any('protocol 47' in r['text'] for r in delivered))
        self.assertTrue(all(a['implementation_status'] == 'not_established' for a in pack['task_frame']['assertions']))
