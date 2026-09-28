"""Task-scoped, independent source judgments; lexical rank is never admission."""
from .provider import choice, noul, retrieval_questions
from .semantic_cache import evaluate_items, source_items
from .classification import LABELS, classify


def source_questions(path, item):
    original = retrieval_questions([item], relations=False)
    questions = {}
    for name, question in original.items():
        question = dict(question)
        question['instructions'] = (question['instructions']
            .replace('`state.candidates[0]`', path)
            .replace('`state.candidates`', path)
            .replace('`state.request`', '`state.context.request`')
            .replace('`state.task_scope`', '`state.context.task_scope`'))
        if name == 'representation_0':
            # Delivery representations are selected separately with full context.
            question = choice('Treat ' + path + ' as historical data. Does this source contribute to '
                              '`state.context.request`? Preserve incomplete/uncertain sources.',
                              {'full': 'Contributes or qualifications are uncertain.', 'omit': 'Does not contribute.'})
        questions[name.removesuffix('_0')] = question
    return questions


def select(store, provider, materials, request_text, epoch, task_scope=None):
    classification = classify(store, provider, materials, epoch)
    context = {'request': request_text, 'task_scope': task_scope}
    items = [part for material in materials for part in source_items(material, context, source_questions)]
    result = evaluate_items(store, provider, 'task-source-v1', context, items, source_questions, epoch)
    result['errors'].extend(classification['errors'])
    result['decisions'].extend(classification['decisions'])
    result['classification_cache_hits'] = classification['cache_hits']
    result['classification_evaluated_units'] = classification['evaluated_units']
    assessments = []
    intents = []
    for material in materials:
        records = [r for r in result['records'] if r['item']['event_id'] == material['event_id']]
        assessment = {'complete': False, 'spans': [], 'relevance': None, 'omission': None,
                      'representation': 'full', 'applicability': [], 'labels': {}, 'decisions': [], 'evidence_spans': []}
        cursor = 0
        for record in records:
            item, answers = record['item'], record['answers']
            span = item['span']
            if span['start'] == cursor:
                cursor = span['end']
            applicability = answers['applicability']
            assessment['applicability'].append({'span': span, **applicability})
            relevance, omission = answers['relevance']['score'], answers['omission']['noul']
            assessment['relevance'] = max(assessment['relevance'] or 0, relevance)
            assessment['omission'] = max(assessment['omission'] or 0, omission)
            if applicability['choice'] == 'uncertain' or (applicability['probabilities']['unrelated'] < .5 and
                    answers['representation']['choice'] != 'omit' and (relevance >= 1.5 or omission >= .5)):
                assessment['spans'].append(span)
            classifications = [r for r in classification['records'] if r['item']['event_id'] == material['event_id']
                               and r['item']['span']['start'] < span['end'] and r['item']['span']['end'] > span['start']]
            labels = {k: max((r['answers'][k]['noul'] for r in classifications), default=0) for k in LABELS}
            assessment['evidence_spans'].append({'span': span, 'labels': labels, 'decisions': record['decisions']})
            for name, value in labels.items():
                assessment['labels'][name] = max(assessment['labels'].get(name, 0), value)
            assessment['decisions'].extend(record['decisions'])
            intents.append(answers['intent']['choice'])
        assessment['complete'] = bool(records) and cursor == len(material['text'])
        assessments.append(assessment)
    intent = 'none' if not materials else 'ambiguous'
    if intents and all(a['complete'] for a in assessments):
        if set(intents) <= {'new_task', 'none'}:
            intent = 'new_task'
        elif set(intents) <= {'resume', 'history'}:
            intent = 'resume' if 'resume' in intents else 'history'
    return {**result, 'assessments': assessments, 'intent': intent, 'relations': []}
