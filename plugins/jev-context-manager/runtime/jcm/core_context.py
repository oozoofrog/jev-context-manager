"""Request depth and deterministic presentation of the query planner's evidence.

No role, native type or assistant-report judgment may remove a selected source.
"""
from .provider import choice
from .semantic_cache import evaluate_items
from .task_state import scope_context


def questions(path, item):
    return {'delivery': choice(
        'Read '+path+'.request as the CURRENT question and its goal as historical context. '
        'Does the user need specific historical evidence now, or are they resuming/planning work '
        'using the reported task state? Diagnostic questions, exact errors/logs/code, proof of '
        'completion, test results, permission verification, source comparison and detailed history '
        'require evidence depth. General task continuation uses concise orientation with every '
        'applicable blocker and qualification retained by the current-question planner. '
        'This selects presentation depth only, never source relevance, correctness or approval.',
        {'evidence': 'Specific evidence is needed now, or uncertain.',
         'continuation': 'General task continuation or planning; concise orientation with required evidence.'})}


def intent(store, provider, identity, request, epoch):
    item = {'request': request['text'], 'goal': identity['anchor']['text']}
    return evaluate_items(store, provider, 'continuation-delivery-v2', {}, [item], questions, epoch, splittable=False)


def continuation(result):
    records = result['records']
    return bool(records and records[0]['answers']['delivery']['probabilities']['continuation'] >= .9 and not result['errors'])


def plan(store, provider, identity, request, frame, selected, materials, epoch, request_intent=None):
    result = request_intent or intent(store, provider, identity, request, epoch)
    is_continuation = bool(scope_context(identity['scope']) and continuation(result))
    deferred = []
    anchor = identity['anchor']
    following_user = store.db.execute("SELECT MIN(seq) FROM events WHERE session=? AND kind='user_message' AND seq>?",
                                      (anchor['session'],anchor['seq'])).fetchone()[0]
    following_user = following_user if following_user is not None else float('inf')
    interval = {m['event_id'] for m in materials if m['session'] == anchor['session'] and
                anchor['seq'] <= m['seq'] < following_user}
    focused = [anchor['event_id']] + [r['event_id'] for r in scope_context(identity['scope'])]
    prior_reports = [m['event_id'] for m in materials if m['session'] == anchor['session'] and
                     m['kind'] == 'assistant_final' and m['seq'] < anchor['seq']]
    anchors = {}
    for source in selected:
        event = store.event(source['event_id'])
        matches = store.db.execute("SELECT id FROM events WHERE session=? AND turn IS ? AND kind='user_message'",
                                   (event['session'], event['turn'])).fetchall() if event['turn'] else []
        if len(matches) == 1:
            anchors[event['id']] = matches[0]['id']
    return {'mode': 'continuation' if is_continuation else 'evidence', 'deferred': deferred,
            'goal_source_ids': focused, 'goal_interval_ids': sorted(interval),
            'prior_report_ids': prior_reports, 'historical_request_ids': anchors,
            'source_expansion_argv': store.config['cli_argv'] + ['inspect','--record','{event_id}'],
            'decisions': result['decisions'], 'errors': result['errors']}


def apply(brief, plan):
    if plan['mode'] != 'continuation':
        return brief
    records = brief['selected_records']
    focus = {eid: i for i,eid in enumerate(plan['goal_source_ids'])}
    interval = set(plan['goal_interval_ids'])
    prior = set(plan['prior_report_ids'])
    records.sort(key=lambda r: (0, focus[r['event_id']]) if r['event_id'] in focus else
                              (1, r.get('seq',0)) if r['event_id'] in interval else
                              (2,-r.get('seq',0)) if r['event_id'] in prior else (3,r.get('seq',0)))
    for record in records:
        record['source_location'] = ('selected_goal_or_report' if record['event_id'] in focus else
                                     'selected_goal_interval' if record['event_id'] in interval else
                                     'same_session_prior_report' if record['event_id'] in prior else 'other_history')
        record['historical_request_id'] = plan['historical_request_ids'].get(record['event_id'])
    defaults = {}
    for field in ('version','implementation_status','verification_currently_applicable',
                  'pending_retrieval_judgment','representation','reconciliation'):
        if records and all(r.get(field) == records[0].get(field) for r in records):
            defaults[field] = records[0].get(field)
            for record in records:
                record.pop(field,None)
    for record in records:
        record.pop('expand_command',None)
    brief['record_defaults'] = defaults
    brief['source_expansion_argv'] = plan['source_expansion_argv']
    brief['current_goal'] = brief['task_frame']['goal']
    brief['evidence_delivery'] = {'mode': 'continuation',
        'claim_boundary': 'Core user statements remain historical user statements; agent reports remain reports. '
            'Included records carry exact selected evidence. Optional sources may be unread; expand them only '
            'when needed to answer a remaining question. Historical observations do not establish current '
            'file state or artifact quality; reconcile current files before making such claims. Reused labels such as A/B/C identify no shared artifact without matching '
            'source session and artifact path. Other-history reports do not describe the current goal merely '
            'because labels match. historical_request_id links only an exact same-turn primary request; '
            'null means that binding is not established. Broad task relevance is not artifact identity or '
            'current authorization. A brief read serves historical evidence; it does not attest current verification.',
        'expansion_index': brief['optional_audit_command'],
        'reported_state_frontier': brief['journal_read_revision']}
    return brief
