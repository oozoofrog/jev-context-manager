"""Exact prepared-stage archive restoration using implementation development data."""
import copy
import json
from pathlib import Path
import sys
import unittest

import test_delivery_comparison as development

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import restore_delivery_archives as archive
import audit_delivery_lane as audit
import probe_host_delivery as probes


class ArchiveDeliveryTests(unittest.TestCase):
    setUp = development.ComparisonTests.setUp
    tearDown = development.ComparisonTests.tearDown
    fixture = development.ComparisonTests.fixture
    prepare = development.ComparisonTests.prepare
    case = development.ComparisonTests.case

    def seed(self):
        result = self.prepare(); self.assertTrue(result['pass'])
        case = self.case(result)
        owned = self.base / 'owned'; owned.mkdir()
        gate = self.base / 'gate.json'; manifest = self.base / 'ready.json'
        scripts = ['restore_delivery_archives.py', 'compare_host_delivery.py', 'audit_delivery_lane.py', 'probe_host_delivery.py']
        archive.save(manifest, {'writing_stopped': True, 'model_settings': case['model_settings'],
            'artifact_files': {str(ROOT / 'scripts' / name): archive.sha(ROOT / 'scripts' / name) for name in scripts}})
        archive.save(gate, {'pass': True, 'writer_turn_completed': True, 'manifest_path': str(manifest), 'manifest_sha256': archive.sha(manifest)})
        result_path = self.base / 'original-result.json'; archive.save(result_path, result)
        case_path = self.base / 'original-case.json'; archive.save(case_path, case)
        closure = archive.close_clones(result_path, gate, self.base, owned / 'initial-closure')
        closure_path = owned / 'initial-closure' / 'fixture-closure.json'
        preflight = archive.inventory(case_path, closure_path, {'baseline': result_path, 'candidate': result_path}, self.base)
        preflight_path = owned / 'inventory.json'; archive.save(preflight_path, preflight)
        return result, case, owned, gate, result_path, case_path, preflight_path

    def test_restore_both_lanes_full_delta_cache_and_close_without_provider(self):
        result, case, owned, gate, original, original_case, preflight = self.seed()
        old_hashes = {str(p): archive.sha(p) for row in archive.load(preflight)['rows'] for p in
            [Path(row['archive_profile']), Path(row['archive_journal']), Path(row['original_live_profile'])]}
        lanes = {}
        for lane in ('baseline', 'candidate'):
            restored = archive.restore_lane(preflight, gate, lane, owned / lane, owned)
            self.assertEqual(restored['prepared_stages_reused'], 2); self.assertEqual(restored['preparation_calls_new'], 0)
            cold, warm = restored['steps']; self.assertEqual(cold['source_binding']['source_snapshot_hash'], warm['source_binding']['source_snapshot_hash'])
            self.assertEqual(cold['request_token'], warm['request_token']); self.assertEqual(warm['delivery'], 'delta')
            self.assertEqual(warm['preparation_metrics']['source_units_evaluated'], 0)
            lanes[lane] = owned / lane / 'result.json'
        derived = archive.derive_case(original_case, lanes['baseline'], lanes['candidate'], gate, self.base)
        self.assertEqual([s['expected'] for s in derived['steps']], [s['expected'] for s in case['steps']])
        self.assertEqual([s['prompt'] for s in derived['steps']], [s['prompt'] for s in case['steps']])
        self.assertTrue(all(Path(s['source_bindings'][lane]['profile']).is_absolute() for s in derived['steps'] for lane in lanes))
        for p, h in old_hashes.items(): self.assertEqual(archive.sha(p), h)
        closed = archive.close_clones(lanes['candidate'], gate, owned, owned / 'candidate-closure')
        self.assertEqual(closed['disabled_profiles'], 2); self.assertTrue(all(r['source_cache_preserved'] for r in closed['profiles']))
        from compare_host_delivery import validate_case
        with self.assertRaisesRegex(ValueError, 'profile changed'): validate_case(derived)

    def test_writer_gate_and_changed_archive_stop_before_restore(self):
        _, _, owned, gate, _, _, preflight = self.seed()
        invalid = self.base / 'pending-gate.json'; value = archive.load(gate); value['writer_turn_completed'] = False; archive.save(invalid, value)
        with self.assertRaisesRegex(ValueError, 'Writer completion'):
            archive.restore_lane(preflight, invalid, 'baseline', owned / 'pending', owned)
        self.assertFalse((owned / 'pending').exists())
        row = archive.load(preflight)['rows'][0]; Path(row['archive_journal']).write_bytes(b'changed archive')
        with self.assertRaisesRegex(ValueError, 'Archive/live input changed'):
            archive.restore_lane(preflight, gate, 'baseline', owned / 'bad', owned)
        self.assertFalse((owned / 'bad' / 'result.json').exists())
        self.assertFalse(archive.load(owned / 'bad' / 'restore-failure.json')['pass'])

    def test_explicit_base_normalizes_legacy_paths_and_output_rejects_unowned(self):
        _, case, owned, gate, original, _, preflight = self.seed()
        changed = copy.deepcopy(case)
        for index, step in enumerate(changed['steps']):
            for lane in ('baseline', 'candidate'):
                step['source_bindings'][lane] = {**step['source_bindings'][lane], 'profile':
                    str(Path(case['steps'][index]['source_bindings'][lane]['profile']).relative_to(self.base))}
                pages = step[lane + '_pages']; step[lane + '_pages'] = [str(Path(p).relative_to(self.base)) for p in pages]
                step['page_hashes'][lane] = {str(Path(p).relative_to(self.base)): h for p, h in step['page_hashes'][lane].items()}
        legacy = self.base / 'legacy-case.json'; archive.save(legacy, changed)
        value = archive.inventory(legacy, owned / 'initial-closure' / 'fixture-closure.json', {'baseline': original, 'candidate': original}, self.base)
        self.assertTrue(value['pass'])
        with self.assertRaisesRegex(ValueError, 'absolute'): archive.inventory(legacy, owned / 'initial-closure' / 'fixture-closure.json', {'baseline': original, 'candidate': original}, 'relative')
        with self.assertRaisesRegex(ValueError, 'owned root'): archive.restore_lane(preflight, gate, 'baseline', self.base / 'outside', owned)
        self.assertFalse((self.base / 'outside').exists())

    def test_fixed_witness_audit_uses_independent_supplied_membership_and_exact_offsets(self):
        _, _, owned, gate, _, _, preflight = self.seed()
        restored = archive.restore_lane(preflight, gate, 'baseline', owned / 'baseline', owned)
        from jcm.store import Store
        from jcm.util import digest
        steps = []; counts = {}; witnesses = {}
        for step in restored['steps']:
            store = Store(archive.load(step['source_binding']['profile']))
            try:
                materials = [store.material(e) for e in store.events()]; counts[step['name']] = len(materials)
                material = next(m for m in materials if m['text'] == step['prompt']); start = material['text'].index('/builds/cedar-A')
                alias = step['name']; witnesses[alias] = {'source_text_sha256': digest(material['text'].encode()),
                    'raw_start': start, 'raw_end': start + len('/builds/cedar-A'), 'raw_text': '/builds/cedar-A'}
                steps.append({'name': alias, 'canonical_request_sha256': step['request_hashes']['planner'], 'witness_ids': [alias]})
            finally: store.close()
        fixture_path = owned / 'dev-fixture.json'; archive.save(fixture_path, self.fixture())
        plan_path = owned / 'dev-plan.json'; archive.save(plan_path, {'steps': steps, 'expected_actual_source_event_counts': counts})
        witnesses_path = owned / 'dev-witnesses.json'; archive.save(witnesses_path, witnesses)
        value = audit.audit('baseline', owned / 'baseline' / 'result.json', plan_path, witnesses_path, fixture_path)
        self.assertTrue(value['pass'], value['rows']); self.assertEqual(value['witness_checks'], 2)
        witnesses['cold']['raw_start'] += 1; bad = owned / 'bad-witnesses.json'; archive.save(bad, witnesses)
        self.assertFalse(audit.audit('baseline', owned / 'baseline' / 'result.json', plan_path, bad, fixture_path)['pass'])


class HostProbeTests(unittest.TestCase):
    def test_data_driven_probe_usage_outcomes_and_initial_resume(self):
        import test_host_delivery as fixtures
        rows = [fixtures.receipt(), fixtures.receipt()]
        value = probes.probe({'operation': 'usage', 'records': rows, 'turn_complete': True})
        self.assertTrue(value['audit']['complete']); self.assertEqual(value['audit']['known_subtotal']['input_tokens'], 100)
        value = probes.probe({'operation': 'commands', 'records': [fixtures.command_call('outer'), fixtures.tool_output('outer', [{'chunk_id': 'chunk', 'exit_code': 1}])]})
        self.assertEqual(value['failed_commands'], 1)
        args = {'operation': 'argv', 'work': '/owned/work', 'answer': '/owned/answer', 'roots': ['/owned/fixture'], 'settings': fixtures.HostDeliveryGateTests.settings}
        initial = probes.probe(args)['argv']; resumed = probes.probe({**args, 'thread_id': 'thread'})['argv']
        configs = lambda a: [a[i+1] for i, word in enumerate(a) if word == '-c']
        self.assertEqual(configs(initial), configs(resumed))
        with self.assertRaisesRegex(ValueError, 'Unknown'): probes.probe({'operation': 'unknown'})
