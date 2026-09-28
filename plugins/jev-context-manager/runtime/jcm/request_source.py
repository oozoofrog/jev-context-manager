"""Recognize host-delivered requests, never instructions quoted in tool results."""
import re

from .util import JCMError


def delegated_request(payload, session):
    item = payload.get('item', {})
    if (item.get('type') != 'FunctionCallOutput' or item.get('namespace') != 'codex_app' or
            item.get('name') != 'send_message_to_thread' or not session or
            payload.get('thread_id') != session or not payload.get('turn_id') or
            not isinstance(item.get('id'), str) or not item['id'].startswith('fco')):
        raise JCMError('UNSUPPORTED_CURRENT_REQUEST')
    output = item.get('output')
    if not isinstance(output, str):
        raise JCMError('UNSUPPORTED_CURRENT_REQUEST')
    match = re.fullmatch(r'<codex_delegation>\s*<source_thread_id>([A-Za-z0-9_-]{1,128})</source_thread_id>\s*<input>(.*)</input>\s*</codex_delegation>', output, re.S)
    if not match or match[1] == session or not match[2].strip():
        raise JCMError('UNSUPPORTED_CURRENT_REQUEST')
    return match[2], {'kind': 'host_delegation', 'source_thread': match[1], 'target_thread': session,
                     'turn': payload['turn_id'], 'item_id': item['id'],
                     'authority': 'current_user_authorization_still_required'}


def current_request(items):
    requests = [i for i in items if i['role'] in ('user', 'unsupported_request')]
    if not requests:
        raise JCMError('CURRENT_REQUEST_NOT_OBSERVABLE')
    if requests[-1]['role'] != 'user':
        raise JCMError('UNSUPPORTED_CURRENT_REQUEST')
    return requests[-1]
