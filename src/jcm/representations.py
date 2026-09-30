"""Source-grounded representations. Jev selects existing spans; it generates no prose."""
import copy
import json
import shlex

from .semantic_cache import validate_dependencies
from .util import digest, encode

VERSION = 'source-structure-v5'


def build(store, provider, identity, selected, materials, epoch):
    """Cache exact source structure. Current-question judgments own delivery."""
    originals = {m['event_id']: m for m in materials}
    output, cached = [], 0
    for source in selected:
        material = originals[source['event_id']]
        key = digest([VERSION, material, source.get('spans')])
        row = store.db.execute('SELECT data FROM representations WHERE key=?', (key,)).fetchone()
        if row:
            value = json.loads(row[0]); cached += 1
        else:
            full = {'text': material['text'], 'spans': [{'start': 0, 'end': len(material['text'])}]}
            detail = {'text': source['text'], 'spans': source.get('spans', full['spans'])}
            value = {k: material[k] for k in ('event_id', 'revision', 'basis', 'role')}
            value.update(source_hash=digest(material['text']), creator='exact_source', version=VERSION,
                         brief=detail, detail=detail, full=full)
            store.db.execute('BEGIN IMMEDIATE')
            try:
                store.policy(epoch)
                validate_dependencies(store, [material])
                store.db.execute('INSERT OR REPLACE INTO representations VALUES (?,?,?,?,?)',
                    (key, identity['id'], material['event_id'], material['revision'], encode(value).decode()))
                store.db.execute('COMMIT')
            except BaseException:
                store.db.execute('ROLLBACK'); raise
        value['expand_command'] = shlex.join(store.config['cli_argv'] + ['inspect', '--record', material['event_id']])
        output.append(value)
    return output, {'errors': [], 'decisions': [], 'representation_cache_hits': cached}


def working_context(pack, frame, representations, level='brief'):
    records = []
    original = {s['event_id']: s for s in pack.get('task_records', pack['selected_records'])}
    for representation in representations:
        selected = representation.get('query', representation['brief']) if level == 'brief' else representation[level]
        if not selected['spans']:
            continue
        records.append({k: representation[k] for k in ('event_id', 'revision', 'source_hash', 'basis', 'role', 'creator', 'version', 'expand_command')}
                       | {k: original[representation['event_id']].get(k) for k in ('reconciliation', 'implementation_status', 'verification_currently_applicable', 'pending_retrieval_judgment')}
                       | {k: original[representation['event_id']].get(k) for k in ('session', 'kind', 'observed_at', 'seq')}
                       | {'representation': level, **selected})
    assertions = [{k: a[k] for k in ('id', 'event_id', 'revision', 'span', 'categories', 'state', 'implementation_status', 'role', 'basis', 'source_kind', 'source_session')}
                  for a in frame['assertions']]
    compact_frame = {**frame, 'assertions': assertions,
                     'relations': [{k: r[k] for k in ('id', 'older', 'newer', 'kind', 'status', 'review_command')} for r in frame['relations']]}
    # Full decision payloads, provider batches, capture inventory and exclusions
    # remain available through the audit view, without counting toward required reads.
    required_keys = ('origin', 'pack_id', 'session_id', 'request', 'dispatch', 'quality', 'semantic_error',
                     'source_use_policy', 'verification', 'next_read', 'journal_read_revision', 'policy_epoch', 'selected_task',
                     'snapshot', 'reconciliation', 'coverage')
    result = {k: pack[k] for k in required_keys}
    result['coverage'] = {'state': pack['coverage']['state'], 'gaps': pack['coverage']['gaps']}
    if pack.get('detail_coverage', {}).get('deferred_sources'):
        result['detail_coverage'] = pack['detail_coverage']
    if 'query_context' in pack:
        result['query_context'] = {k: v for k, v in pack['query_context'].items() if k not in ('sources', 'source_dependencies', 'required_frame', 'binding', 'record_order', 'required_metadata')}
    result.update(task_frame=compact_frame, selected_records=records, representation_level=level,
                  optional_audit_command=pack['audit_command'], metrics=pack['metrics'])
    return result


def compact_brief(brief, frame):
    """A query view of the graph, without changing the canonical task state."""
    # The query planner completed and froze this frame. Rendering never chooses
    # relation relevance or filters a selected source again.
    relations = frame['relations']
    linked = {r[k] for r in relations for k in ('older', 'newer')}
    assertions = [{k: a[k] for k in ('id', 'event_id', 'revision', 'span', 'text',
        'surrounding_text', 'state', 'role', 'basis', 'source_kind', 'source_session')}
        for a in frame['assertions'] if a['id'] in linked or a['state'] != 'active_evidence']
    brief['task_frame'] = {k: v for k, v in frame.items() if k not in ('assertions', 'relations')}
    brief['task_frame'].update(assertions=assertions,
        relations=[{k: r[k] for k in ('id', 'older', 'newer', 'kind', 'status', 'review_command')} for r in relations],
        assertion_delivery={'default_state': 'active_evidence', 'implementation_status': 'not_established',
            'scope': 'Selected passages and both endpoints of their relations; complete graph in detail/audit.'})
    records = brief['selected_records']
    defaults = brief.setdefault('record_defaults', {})
    for field in ('creator', 'version', 'basis', 'implementation_status', 'verification_currently_applicable',
                  'pending_retrieval_judgment', 'reconciliation'):
        if records and all(r.get(field) == records[0].get(field) for r in records):
            value = records[0].get(field)
            if value is not None:
                defaults[field] = value
            for record in records:
                record.pop(field, None)
    for record in records:
        # Original hash and the rendering provenance remain in the immutable
        # audit representation; expansion validates the pack's source revision.
        for field in ('source_hash', 'creator', 'version', 'expand_command', 'seq', 'observed_at'):
            record.pop(field, None)
    brief.pop('metrics', None)  # operational counters belong in status/audit
    return brief


def validate_contract(plan, brief):
    """Deterministic downstream preservation; not a semantic recall oracle."""
    errors = list(plan.get('gaps', []))
    records = {r['event_id']: {**brief.get('record_defaults', {}), **r}
               for r in brief.get('selected_records', [])}
    if plan.get('record_order', list(records)) != list(records):
        errors.append('REQUIRED_RECORD_ORDER_CHANGED')
    if len(records) != len(brief.get('selected_records', [])):
        errors.append('DUPLICATE_DELIVERY_SOURCE')
    for source in plan['sources']:
        if not source['included']:
            continue
        expected = source['required_record']; actual = records.get(source['event_id'])
        if actual is None:
            errors.append('MISSING_REQUIRED_SPAN:' + source['event_id'])
        elif any(actual.get(k) != v for k,v in expected.items()):
            errors.append('REQUIRED_EVIDENCE_CHANGED:' + source['event_id'])
    frame = brief.get('task_frame', {})
    required = plan['required_frame']
    for key,value in required.items():
        if key not in ('assertions','relations') and frame.get(key) != value:
            errors.append('REQUIRED_FRAME_CHANGED:' + key)
    for key,value in plan.get('required_metadata', {}).items():
        if brief.get(key) != value:
            errors.append('REQUIRED_METADATA_CHANGED:' + key)
    relations = {r['id']:r for r in frame.get('relations', [])}
    linked = {r[k] for r in required['relations'] for k in ('older','newer')}
    for relation in required['relations']:
        if any(relations.get(relation['id'], {}).get(k) != relation[k] for k in ('older','newer','kind','status')):
            errors.append('REQUIRED_RELATION_CHANGED:' + relation['id'])
    assertions = {a['id']:a for a in frame.get('assertions', [])}
    for assertion in required['assertions']:
        actual = assertions.get(assertion['id'])
        if actual is None and assertion['id'] not in linked and assertion['state'] == 'active_evidence':
            defaults = frame.get('assertion_delivery', {})
            if defaults.get('default_state') == 'active_evidence' and defaults.get('implementation_status') == 'not_established':
                continue  # exact required source text and provenance checked above
        if (actual is not None and actual.get('implementation_status', frame.get('assertion_delivery', {}).get('implementation_status'))
                != assertion.get('implementation_status', 'not_established')):
            errors.append('REQUIRED_STATE_CHANGED:' + assertion['id'])
        if actual is None or any(actual.get(k) != assertion.get(k) for k in
                ('event_id','revision','span','text','surrounding_text','state','role','basis','source_kind','source_session')):
            errors.append('REQUIRED_STATE_CHANGED:' + assertion['id'])
    return sorted(set(errors))


def validate_plan_sources(store, plan, epoch):
    """Check the frozen plan against current admission and exact source bytes."""
    from .util import JCMError
    store.policy(epoch)
    if plan['binding']['policy_epoch'] != epoch:
        raise JCMError('DELIVERY_PLAN_POLICY_CHANGED')
    needed = [s for s in plan['sources'] if s['included']]
    validate_dependencies(store, [{'event_id':s['event_id'], 'revision':s['revision']} for s in needed])
    for source in needed:
        original = store.material(store.event(source['event_id']))
        expected = source['required_record']
        spans = expected['spans']
        if any(type(s['start']) is not int or type(s['end']) is not int or
               not 0 <= s['start'] < s['end'] <= len(original['text']) for s in spans):
            raise JCMError('INVALID_REQUIRED_SOURCE_SPAN:' + source['event_id'])
        text = '\n\n[... optional detail ...]\n\n'.join(original['text'][s['start']:s['end']] for s in spans)
        if text != expected['text'] or any(expected[k] != original[k] for k in ('event_id','revision','role','basis','kind','session')):
            raise JCMError('REQUIRED_SOURCE_CHANGED:' + source['event_id'])


def enforce_contract(store, plan, brief, epoch):
    """Repair from admitted exact spans once; invalid sources never resurrect."""
    from .util import JCMError
    errors = validate_contract(plan, brief)
    try:
        validate_plan_sources(store, plan, epoch)
    except JCMError as error:
        errors.append(str(error))
    if not errors:
        brief['delivery_contract'] = {'version':1, 'status':'validated'}
        return brief, []
    fallback = copy.deepcopy(brief)
    try:
        validate_plan_sources(store, plan, epoch)
        needed = [s for s in plan['sources'] if s['included']]
        validate_dependencies(store, [{'event_id':s['event_id'], 'revision':s['revision']} for s in needed])
        records = []
        for source in needed:
            expected = source['required_record']
            text = store.material(store.event(source['event_id']))['text']
            exact = '\n\n[... optional detail ...]\n\n'.join(text[s['start']:s['end']] for s in expected['spans'])
            if exact != expected['text']:
                raise JCMError('REQUIRED_SOURCE_CHANGED:' + source['event_id'])
            records.append(copy.deepcopy(expected))
        ordered = {r['event_id']:r for r in records}
        fallback['selected_records'] = [ordered[eid] for eid in plan.get('record_order', list(ordered))]
        fallback.pop('record_defaults', None)
        fallback['task_frame'] = copy.deepcopy(plan['required_frame'])
        fallback.update(copy.deepcopy(plan.get('required_metadata', {})))
        remaining = validate_contract(plan, fallback)
    except JCMError as error:
        remaining = [str(error)]
    fallback['quality'] = 'degraded'
    fallback['semantic_error'] = 'DELIVERY_CONTRACT_VIOLATION'
    fallback.setdefault('coverage', {}).setdefault('gaps', []).extend(['DELIVERY_CONTRACT_VIOLATION'] + errors + remaining)
    fallback['delivery_contract'] = {'version':1, 'status':'blocked' if remaining else 'exact_span_fallback', 'failures':errors + remaining}
    if remaining:
        fallback['dispatch'] = 'blocked'
    return fallback, ['DELIVERY_CONTRACT_VIOLATION'] + errors + remaining
