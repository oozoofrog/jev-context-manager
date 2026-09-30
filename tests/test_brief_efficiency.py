"""Small required evidence must retain native conditions and exact source offsets."""
import json
import unittest
from unittest.mock import patch

import test_continuity as fixtures
import test_core_context as core_fixtures
from jcm import query_context, representations
from jcm.source_units import units, assessment_units
from jcm.util import JCMError


class BriefEfficiencyTests(unittest.TestCase):
    setUp = core_fixtures.CoreContextTests.setUp
    tearDown = core_fixtures.CoreContextTests.tearDown
    source = core_fixtures.CoreContextTests.source
    provider = core_fixtures.CoreContextTests.provider

    def test_nested_json_strings_keep_exact_offsets_and_separate_array_artifacts(self):
        content = {'approved':False, 'records':[
            {'artifact':'current-A', 'outcome':'한글 😀 TARGET_READY',
             'qualification':{'hardware':'TARGET_NOT_VERIFIED'}},
            {'artifact':'old-A', 'outcome':'OLD_UNRELATED'}]}
        text = json.dumps({'status':'completed', 'result':{'content':[
            {'type':'text', 'text':json.dumps(content,ensure_ascii=True)}]}},ensure_ascii=True)
        material = self.source('nested', text, 'tool')
        parsed = list(units(material))
        target = next((u for u in parsed if u['source_path'][-3:]==['records',0,'outcome']),None)
        self.assertIsNotNone(target, 'Encoded JSON must expose the nested evidence fields')
        decoded=json.loads('"'+target['text']+'"')
        self.assertEqual(json.loads('{'+decoded+'}')['outcome'],content['records'][0]['outcome'])
        for unit in parsed:
            self.assertEqual(unit['text'],text[unit['span']['start']:unit['span']['end']])
        parents = str(target['_parents'])
        self.assertIn('TARGET_NOT_VERIFIED',parents)
        self.assertIn('approved',parents)
        self.assertNotIn('OLD_UNRELATED',parents)

    def test_embedded_prose_paragraphs_are_not_reintroduced_as_parent_conditions(self):
        text=json.dumps({'status':'partial','result':{'content':[{'type':'text','text':
            'CURRENT_READY 한글 😀.\n\nOLD_UNRELATED '+('old detail '*300)+
            '\n\nCURRENT_CONDITION: hardware is not verified.'}]}},ensure_ascii=True)
        source=self.source('prose',text,'tool')
        paragraphs=[u for u in units(source) if '$paragraph' in u['source_path']]
        self.assertEqual(len(paragraphs),3)
        for unit in paragraphs:
            self.assertEqual(unit['text'],text[unit['span']['start']:unit['span']['end']])
        current=paragraphs[0]
        self.assertIn('partial',str(current['_parents']))
        self.assertNotIn('OLD_UNRELATED',str(current['_parents']))
        reps,_=self.build(source,self.localized,complete=True)
        self.assertIn('CURRENT_READY',reps[0]['query']['text'])
        self.assertIn('CURRENT_CONDITION',reps[0]['query']['text'])
        self.assertNotIn('OLD_UNRELATED',reps[0]['query']['text'])

    def test_invalid_surrogate_in_encoded_json_keeps_raw_evidence(self):
        inner=json.dumps({'text':'bad \ud800\n\nvalue'},ensure_ascii=False)
        text=json.dumps({'result':inner},ensure_ascii=True)
        source=self.source('invalid-unicode',text,'tool')
        for unit in units(source):
            unit['reading_text'].encode('utf-8')
            self.assertEqual(unit['text'],text[unit['span']['start']:unit['span']['end']])

    def build(self, source, transport, complete=False):
        request=self.source('ask','Explain CURRENT_READY for current-A, retaining its qualification.','user')
        identity={'id':'a'*64,'anchor':request,'scope':{'selected_source':request}}
        frame={'read_revision':1,'goal':request,'assertions':[],'relations':[]}
        selected=[source]; materials=[source]
        provider=self.provider(transport)
        reps,_=representations.build(self.store,provider,identity,selected,materials,self.cfg['epoch'])
        reps,plan,_=query_context.build(self.store,provider,identity,request,frame,selected,materials,reps,self.cfg['epoch'])
        if complete:
            query_context.complete(self.store,plan,reps,selected,materials,frame)
        return reps,plan

    @staticmethod
    def localized(body,key):
        payload=json.loads(body); response=fixtures.fake_http(body,key)
        for name,q in payload['questions'].items():
            item=payload['state']['items'][int(name.split('_')[0][1:])]
            if name.endswith('query_level'):
                value='brief'
            elif 'query_block_' in name:
                text=item.get('text','') or item['paragraphs'][int(name.rsplit('_',1)[1])]['text']
                value='core' if 'CURRENT_' in text else 'omit'
            elif 'query_guard_' in name:
                text=item.get('text','') or item['paragraphs'][int(name.rsplit('_',1)[1])]['text']
                response['answers'][name]['noul']=.49 if 'CURRENT_CONDITION' in text else 0
                continue
            elif name.endswith('qualification_scope'):
                value='local'
            else:
                continue
            response['answers'][name].update(choice=value,probabilities={k:float(k==value) for k in q['criteria']})
        return response

    def test_local_uncertainty_retains_conditions_without_other_array_records(self):
        text=json.dumps({'approved':False,'records':[
            {'artifact':'current-A','outcome':'CURRENT_READY',
             'qualification':{'hardware':'CURRENT_CONDITION: not verified'}},
            {'artifact':'old-A','detail':'OLD_UNRELATED '*300}]})
        source=self.source('structured',text,'tool')
        reps,plan=self.build(source,self.localized)
        output=reps[0]['query']['text']
        self.assertIn('CURRENT_READY',output)
        self.assertIn('CURRENT_CONDITION',output)
        self.assertIn('"approved": false',output)
        self.assertNotIn('OLD_UNRELATED',output)
        self.assertEqual(plan['sources'][0]['decision'],'unresolved')
        self.assertTrue(plan['uncertainties'])

    def test_structural_assessment_does_not_repeat_same_conditions_or_merge_array_peers(self):
        text=json.dumps({'approved':False,'records':[
            {'artifact':'current-A','outcome':'CURRENT_READY','conditions':{'hardware':'not verified'}},
            {'artifact':'old-A','outcome':'OLD_UNRELATED'}]})
        material=self.source('units',text,'tool')
        assessed=list(assessment_units(material))
        current=next(u for u in assessed if 'CURRENT_READY' in u['text'])
        self.assertIn('not verified',current['text'])
        self.assertNotIn('OLD_UNRELATED',current['text'])
        self.assertNotIn('CURRENT_READY',str(current['_parents']))
        self.assertIn('approved',str(current['_parents']))
        self.assertEqual(current['text'],text[current['span']['start']:current['span']['end']])
        self.assertLess(len(assessed),len(list(units(material))))

    def test_compatible_boundary_readings_keep_the_qualification_without_whole_source(self):
        source=self.source('combined',json.dumps({'records':[
            {'artifact':'current-A','outcome':'CURRENT_READY','condition':'CURRENT_CONDITION'},
            {'artifact':'old-A','detail':'OLD_UNRELATED '*300}]}),'tool')
        def transport(body,key):
            result=self.localized(body,key)
            for name in json.loads(body)['questions']:
                if name.endswith('qualification_scope'):
                    result['answers'][name].update(choice='independent',
                        probabilities={'independent':.55,'local':.29,'source':.16})
            return result
        reps,plan=self.build(source,transport,complete=True)
        self.assertIn('CURRENT_CONDITION',reps[0]['query']['text'])
        self.assertNotIn('OLD_UNRELATED',reps[0]['query']['text'])
        self.assertEqual(plan['sources'][0]['decision'],'unresolved')

    def test_unresolved_boundary_is_exact_required_state_not_a_complete_recovery_claim(self):
        import copy
        source=self.source('unresolved',json.dumps({'records':[
            {'artifact':'current-A','outcome':'CURRENT_READY','condition':'CURRENT_CONDITION'},
            {'artifact':'old-A','detail':'OLD_UNRELATED '*300}]}),'tool')
        def transport(body,key):
            result=self.localized(body,key)
            for name in json.loads(body)['questions']:
                if name.endswith('qualification_scope'):
                    result['answers'][name].update(choice='independent',
                        probabilities={'independent':.45,'local':.25,'source':.30})
            return result
        reps,plan=self.build(source,transport,complete=True)
        self.assertNotIn('OLD_UNRELATED',reps[0]['query']['text'])
        frame=plan['required_frame']
        self.assertFalse(frame['qualification_boundary_policy']['complete_recovery_established'])
        self.assertEqual(frame['qualification_limits'][0]['event_id'],source['event_id'])
        delivered={'selected_records':[s['required_record'] for s in plan['sources'] if s['included']],
                   'task_frame':copy.deepcopy(frame)}
        self.assertEqual(representations.validate_contract(plan,delivered),[])
        delivered['task_frame'].pop('qualification_limits')
        self.assertIn('REQUIRED_FRAME_CHANGED:qualification_limits',representations.validate_contract(plan,delivered))

    def test_rejected_membership_span_cannot_become_an_unresolved_required_qualification(self):
        text='CURRENT_READY accepted.\n\nCURRENT_CONDITION belongs to a rejected scope.'
        source=self.source('admitted-only',text,'tool')
        source['spans']=[{'start':0,'end':len('CURRENT_READY accepted.')}]
        def transport(body,key):
            result=self.localized(body,key)
            for name in json.loads(body)['questions']:
                if name.endswith('qualification_scope'):
                    result['answers'][name].update(choice='independent',
                        probabilities={'independent':.45,'local':.25,'source':.30})
            return result
        reps,plan=self.build(source,transport,complete=True)
        self.assertNotIn('CURRENT_CONDITION',reps[0]['query']['text'])
        self.assertEqual(plan['qualification_limits'],[])
        self.assertNotIn('qualification_limits',plan['required_frame'])

    def test_reference_presence_does_not_force_unrelated_wrapper_body(self):
        source=self.source('copied',json.dumps({'records':[
            {'value':'CURRENT_READY'},
            {'jcm_source_reference':'b'*64,'detail':'OLD_UNRELATED '*300}]}),'tool')
        source['derived_from']=['b'*64]
        reps,_=self.build(source,self.localized)
        self.assertIn('CURRENT_READY',reps[0]['query']['text'])
        self.assertNotIn('OLD_UNRELATED',reps[0]['query']['text'])
        self.assertNotIn('b'*64,reps[0]['query']['text'])

    def test_completion_expands_only_references_in_required_spans(self):
        target=self.source('original-current','CURRENT_READY original with its qualification.','assistant')
        old=self.source('original-old','OLD_UNRELATED original.','assistant')
        source=self.source('wrapper',json.dumps({'records':[
            {'value':'CURRENT_READY','jcm_source_reference':target['event_id']},
            {'value':'OLD_UNRELATED','jcm_source_reference':old['event_id']}]}),'tool')
        source['derived_from']=[target['event_id'],old['event_id']]
        reps,plan=self.build(source,self.localized)
        query_context.complete(self.store,plan,reps,[source],[source,target,old],{'assertions':[],'relations':[]})
        included={s['event_id'] for s in plan['sources'] if s['included']}
        self.assertIn(target['event_id'],included)
        self.assertNotIn(old['event_id'],included)

    def test_missing_refinement_retains_source_and_records_the_failure(self):
        source=self.source('condition','CURRENT_READY preview.\n\nCURRENT_CONDITION: keep all unchanged clauses.','tool')
        def offline(body,key):
            if any(n.endswith('qualification_scope') for n in json.loads(body)['questions']):
                raise JCMError('PROVIDER_OFFLINE')
            return self.localized(body,key)
        reps,plan=self.build(source,offline)
        self.assertEqual(reps[0]['query']['text'],source['text'])
        self.assertEqual(plan['sources'][0]['decision'],'unresolved')


class BriefReadTests(unittest.TestCase):
    from test_query_context import QueryContextTests as _Fixture
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown
    capture=_Fixture.capture
    provider=_Fixture.provider
    recover=_Fixture.recover
    transport=_Fixture.transport
    focus=staticmethod(_Fixture.focus)
    corpus=_Fixture.corpus

    def test_dependency_validation_checks_beyond_sql_parameter_boundary(self):
        import sqlite3
        from jcm.semantic_cache import validate_dependencies
        self.corpus()
        rows=self.store.db.execute('SELECT id,revision,blob FROM events ORDER BY seq').fetchall()
        self.assertGreater(len(rows),2)
        deps=[{'event_id':r['id'],'revision':r['revision']} for r in rows]
        bindings={r['id']:r['blob'] for r in rows}
        previous=self.store.db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,2)
        try:
            validate_dependencies(self.store,deps,bindings)
            stale=[*deps[:-1],{**deps[-1],'revision':deps[-1]['revision']+1}]
            with self.assertRaisesRegex(JCMError,'SEMANTIC_DEPENDENCY_CHANGED'):
                validate_dependencies(self.store,stale,bindings)
            bindings[rows[-1]['id']]='f'*64
            with self.assertRaisesRegex(JCMError,'PACK_SOURCE_CHANGED'):
                validate_dependencies(self.store,deps,bindings)
            with self.assertRaisesRegex(JCMError,'EVENT_NOT_FOUND'):
                validate_dependencies(self.store,[*deps,{'event_id':'e'*64,'revision':1}])
        finally:
            self.store.db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,previous)

    def test_delivery_header_avoids_full_blob_but_rechecks_source_binding(self):
        from jcm.coordinator import read_pack
        self.corpus();pack=self.recover('consumer','Continue pause recovery.')
        read_pack(self.store,pack['pack_id'])
        blob=self.store.db.execute('SELECT blob FROM packs WHERE id=?',(pack['pack_id'],)).fetchone()[0]
        original=self.store.blob
        def page_only(key):
            self.assertNotEqual(key,blob,'A subsequent required read must not parse the audit corpus')
            return original(key)
        with patch.object(self.store,'blob',side_effect=page_only):
            read_pack(self.store,pack['pack_id'])
            self.store.db.execute('UPDATE events SET blob=? WHERE id=?',('f'*64,self.rule))
            with self.assertRaisesRegex(JCMError,'PACK_SOURCE_CHANGED'):
                read_pack(self.store,pack['pack_id'])

    def test_intermediate_page_does_not_claim_fresh_workspace_reconciliation(self):
        from jcm.coordinator import read_pack
        from jcm.snapshot import snapshot
        self.store.config['pack_byte_ceiling']=4000
        self.corpus();pack=self.recover('consumer','Analyze pause recovery failure with the full callback trace.')
        with patch('jcm.coordinator.snapshot',wraps=snapshot) as observed:
            first=read_pack(self.store,pack['pack_id'])
            count=first['pagination']['page_count'];self.assertGreater(count,2)
            self.assertEqual(observed.call_count,1)
            middle=read_pack(self.store,pack['pack_id'],2)
            self.assertEqual(middle['current_reconciliation'],'not_checked')
            self.assertEqual(observed.call_count,1)
            (self.root/'changed-during-read.txt').write_text('changed')
            for page in range(3,count+1):last=read_pack(self.store,pack['pack_id'],page)
            self.assertEqual(last['current_reconciliation'],'stale')
            self.assertEqual(observed.call_count,2)
            self.assertTrue(last['required_context_complete'])

    def test_forget_removes_cached_header_and_its_blob(self):
        from jcm.coordinator import read_pack
        self.corpus();pack=self.recover('consumer','Continue pause recovery.')
        read_pack(self.store,pack['pack_id'])
        key='delivery_header:'+pack['pack_id']
        blob=json.loads(self.store.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()[0])['blob']
        self.store.forget_session('history')
        self.assertIsNone(self.store.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone())
        with self.assertRaisesRegex(JCMError,'BLOB_MISSING'):
            self.store.blob(blob)


if __name__=='__main__':
    unittest.main()
