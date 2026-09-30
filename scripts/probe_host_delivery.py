#!/usr/bin/env python3
"""Run fixed offline host-contract probes on caller-supplied records/data.

No model subprocess. JSON input selects usage, commands, policy, argv, gates,
case, exposure or native_turns; this executable emits observations only. The
validator keeps expected results separately. A JSONL replay preserves each turn
and deduplicates response receipts across those turns.
"""
import argparse
import json
from pathlib import Path
import re
import compare_host_delivery as host
from restore_delivery_archives import absolute, load, save, sha, connect, table_hashes


def probe(value):
    operation = value['operation']
    if operation == 'provider_accounting':
        from contextlib import closing
        case=load(absolute(value['case']));result=load(absolute(value['result']));inventory=load(absolute(value['inventory']))
        rows=[];read_hashes={};lookup_calls=0;native_lookups=set()
        for lane in ('baseline','candidate'):
            work=absolute(value['pair_output'])/lane
            stages=result['lanes'][lane]['steps'];thread=next(iter(stages.values()))['thread_id']
            records,errors=host.native_records(work.parent/(lane+'-codex'),thread)
            if errors:raise ValueError('Native lookup accounting unavailable: '+lane)
            wanted={s['native_policy']['contexts'][0]['turn_id'] for s in stages.values()}
            contexts=[r['payload'] for r in records if r.get('type')=='turn_context']
            if not wanted <= {c.get('turn_id') for c in contexts}:raise ValueError('Native stage prefix missing: '+lane)
            _,selected=host.current_native_turn(records,{c['turn_id'] for c in contexts if c['turn_id'] not in wanted})
            for record in selected:
                p=record.get('payload',{})
                if record.get('type')=='response_item' and p.get('type') in ('function_call','custom_tool_call') and re.search(
                        r'read\.py\s+lookup(?:\s|$)',str(p.get('input',p.get('arguments','')))):
                    native_lookups.add((lane,p.get('call_id') or p.get('id')))
            for name,stage in result['lanes'][lane]['steps'].items():
                path=work/(name+'-reads.jsonl');read_hashes[str(path)]=sha(path)
                lookup_calls+=sum(json.loads(line)['kind']=='lookup' for line in path.read_text().splitlines())
            for step in case['steps']:
                cfg=load(step['source_bindings'][lane]['profile']);journal=absolute(cfg['home'])/'stores'/cfg['repo_id']/'journal.sqlite'
                original=next(row for row in inventory['rows'] if row['lane']==lane and row['stage']==step['name'])
                with closing(connect(journal)) as db:tables=table_hashes(db,['calls','decisions'])
                rows.append({'lane':lane,'stage':step['name'],'journal':str(journal),'tables':tables,
                    'provider_rows_unchanged':all(tables[k]==original['tables'][k] for k in tables)})
        unchanged=all(row['provider_rows_unchanged'] for row in rows)
        return {'provider_rows_unchanged_from_prepared_archives':unchanged,'observed_lookup_reads':lookup_calls,
            'observed_native_lookup_attempts':len(native_lookups),
            'optional_provider_calls_confirmed':0 if unchanged else None,
            'optional_provider_usage':{'input_tokens':0,'output_tokens':0} if unchanged else None,
            'rows':rows,'read_log_hashes':read_hashes,
            'scope':'Read-only clone/archive calls and decisions comparison; changed rows require receipt accounting, never guessed zero.'}
    if operation == 'usage':
        seen = dict(value.get('seen', {})); result = host.usage_audit(value['records'], seen, value['turn_complete'])
        return {'audit': result, 'seen': seen}
    if operation == 'commands':
        return host.command_outcomes(value['records'], value.get('cli_rows', []))
    if operation == 'policy':
        return host.policy_audit(value['contexts'], absolute(value['work']), value['roots'], value['settings'])
    if operation == 'argv':
        return {'argv': host.consumer_argv(absolute(value['work']), value['roots'], absolute(value['answer']), value['settings'], value.get('thread_id'))}
    if operation == 'gates':
        return {'failures': host.stage_failures(value['stage'])}
    if operation == 'case':
        return {'stages': len(host.validate_case(value['case']))}
    if operation == 'exposure':
        return host.model_visible_pages(Path('/unused'), 'unused', value['pages'], set(), records=value['records'])
    if operation == 'native_turns':
        records = value['records']; seen = {}; turns = []; contexts = [r['payload'] for r in records if r.get('type') == 'turn_context']
        for context in contexts:
            selected_contexts, selected = host.current_native_turn(records, {c['turn_id'] for c in contexts if c['turn_id'] != context['turn_id']})
            complete = any(r.get('type') == 'event_msg' and r.get('payload', {}).get('type') == 'task_complete' for r in selected)
            turns.append({'turn_id': context['turn_id'], 'usage': host.usage_audit(selected, seen, complete),
                'commands': host.command_outcomes(selected), 'policy': host.policy_audit(selected_contexts,
                    absolute(context['cwd']), value['roots'], value['settings'])})
        return {'turns': turns}
    raise ValueError('Unknown host probe operation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--records-jsonl', help='Replace input.records with a saved JSONL; data never echoed')
    args = parser.parse_args(); source = absolute(args.input); value = load(source)
    inputs = {str(source): sha(source)}
    if args.records_jsonl:
        path = absolute(args.records_jsonl); inputs[str(path)] = sha(path)
        value['records'] = [json.loads(line) for line in path.read_text().splitlines()]
    try:
        result = {'execution_pass': True, 'observation': probe(value)}
    except (ValueError, KeyError, TypeError) as error:
        result = {'execution_pass': False, 'rejected': {'type': type(error).__name__, 'message': str(error)}}
    result['input_hashes'] = inputs
    save(args.output, result)
    print(json.dumps({'execution_pass': result['execution_pass']}))
    return 0 if result['execution_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
