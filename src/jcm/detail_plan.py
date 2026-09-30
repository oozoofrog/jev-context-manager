"""Plan optional tool-output expansion without treating metadata as evidence.

All bodies enter the existing cached body-assessment path. Descriptors remain
retrieval hints, never proof of output contents or success.
"""
import json



def incomplete_outcome(value, depth=0):
    """Read recognized result envelopes, not keywords in arbitrary log prose."""
    if depth > 32:
        return True
    if isinstance(value, str):
        if value.strip().startswith(('Script running with cell ID ', 'Process running with session ID ')):
            return True
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            return False
        return incomplete_outcome(parsed, depth+1) if isinstance(parsed, (dict,list)) else False
    if isinstance(value, list):
        return any(incomplete_outcome(v, depth+1) for v in value)
    if not isinstance(value, dict):
        return False
    if not value:
        return True  # An empty wrapper establishes no recognized outcome.
    if any(value.get(k) for k in ('isError','is_error','error','partial','truncated','hasMore','has_more')):
        return True
    if any(value.get(k) is False for k in ('success','pass','ok')):
        return True
    if any(value.get(k) not in (None,False,0,'0') for k in ('failed','failed_tests','failures')):
        return True
    if value.get('status') in ('failed','error','interrupted','cancelled','canceled','running','pending','queued','partial','not_attested'):
        return True
    if value.get('coverage') == 'partial' or value.get('recovery_success') == 'not_attested':
        return True
    if 'exit_code' in value and value['exit_code'] not in (0,None):
        return True
    if (value.get('session_id') is not None and value.get('exit_code') is None and
            (any(k in value for k in ('chunk_id','wall_time_seconds','output')) or
             value.get('status') not in ('completed','success','succeeded'))):
        return True
    # Only wrapper fields are traversed. A historical thread's nested messages
    # are data, not the status of the read_thread invocation that retrieved them.
    for key in ('result','structuredContent','output','content','tests','page','pagination'):
        if key in value and incomplete_outcome(value[key], depth+1):
            return True
    if value.get('type') in ('text','Text') and 'text' in value:
        return incomplete_outcome(value['text'],depth+1)
    return False


def descriptor(payload):
    item = payload.get('public_item', {})
    kind = item.get('type')
    if kind == 'CommandExecution':
        if item.get('status') != 'completed' or item.get('exit_code') != 0 or item.get('stderr'):
            return None
        command = item.get('command')
        if not command:
            return None
        output = item.get('aggregated_output')
        if output == {'jcm_text_field':'aggregated_output'}:
            try:
                output = json.loads(payload['text'])['aggregated_output']
            except (ValueError, KeyError, TypeError):
                return None
        if incomplete_outcome(output):
            return None
        return {'kind': kind, 'command': command, 'cwd': item.get('cwd'),
                'exit_code': 0, 'status': 'completed', 'output_reviewed': False,
                'outcome_scope': 'process_completion_only_not_result_verification'}
    if kind == 'McpToolCall':
        result = item.get('result')
        if result == {'jcm_text_field': 'result'}:
            try:
                result = json.loads(payload['text'])['result']
            except (ValueError, KeyError, TypeError):
                return None
        if isinstance(result, dict) and 'Ok' in result:
            result = result['Ok']
        if (item.get('status') != 'completed' or not item.get('tool') or
                not isinstance(result, dict) or not ('content' in result or 'structuredContent' in result) or
                ('content' in result and not isinstance(result['content'],list)) or incomplete_outcome(result)):
            return None
        return {'kind': kind, 'server': item.get('server'), 'tool': item['tool'],
                'arguments': item.get('arguments'), 'status': 'completed', 'output_reviewed': False,
                'outcome_scope': 'invocation_completion_only_not_result_verification'}
    return None


def plan(store, provider, events, identity, request_text, epoch, protected=()):
    """Preparation hints never exclude bodies from the existing assessment path.

    Worker/source membership and query judgments already batch and cache exact
    bodies. Running a second descriptor classifier here is both redundant and
    unsafe: a successful invocation says nothing about qualifications inside it.
    """
    store.policy(epoch)
    return list(events), [], {'errors': [], 'decisions': [], 'cache_hits': 0,
                             'evaluated_units': 0, 'batches': []}
