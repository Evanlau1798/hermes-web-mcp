"""Observable agent assessments, not a final-answer hook or a skill writer."""
import copy
import time
from agent.redact import redact_sensitive_text


RESULTS = ['no_new_learning', 'covered_by_existing_skill', 'new_reusable_learning']
SCHEMA = {'type':'object', 'required':['action'], 'properties':{
    'action':{'enum':['read','submit']}, 'result':{'enum':RESULTS},
    'reason':{'type':'string','minLength':1,'maxLength':400},
    'skill_ids':{'type':'array','maxItems':8,'uniqueItems':True,
                 'items':{'type':'string','minLength':1,'maxLength':300}},
    'evidence':{'type':'array','maxItems':8,'items':{'type':'string','minLength':1,'maxLength':300}},
    'expected_version':{'type':'integer','minimum':0,'maximum':2147483647}},
    'oneOf':[{'properties':{'action':{'const':'submit'}},'required':['result','reason']},
             {'properties':{'action':{'const':'read'}},
              'not':{'anyOf':[{'required':[key]} for key in ('result','reason','skill_ids','evidence','expected_version')]}}]}


def pending():
    return {'state':'pending','version':0,'result':None}


def review(client, args):
    if client is None:
        return {'success':False,'errorCode':'bootstrap_required','executionStarted':False}
    current = client['task_review']
    if args['action'] == 'read':
        return copy.deepcopy(current)
    data = {'result':args['result'], 'reason':redact_sensitive_text(args['reason']),
            'skill_ids':[redact_sensitive_text(s) for s in args.get('skill_ids',[])],
            'evidence':[redact_sensitive_text(s) for s in args.get('evidence',[])]}
    if any(not value.strip() for value in [data['reason'],*data['skill_ids'],*data['evidence']]):
        return {'success':False,'errorCode':'REVIEW_EMPTY_FIELD','error':'理由、Skill ID 與驗證依據不可只有空白。'}
    if data['result'] != 'no_new_learning' and (not data['skill_ids'] or not data['evidence']):
        return {'success':False,'errorCode':'REVIEW_EVIDENCE_REQUIRED',
                'error':'請提供相關 Skill ID 與已驗證依據；Review 不會自動更新技能。'}
    if all(current.get(key) == value for key,value in data.items()):
        return {**copy.deepcopy(current),'deduplicated':True}
    if args.get('expected_version',0) != current['version']:
        return {'success':False,'errorCode':'REVIEW_VERSION_CONFLICT','version':current['version'],
                'error':'先 read 核對最新判斷，再以 expected_version 明確提交修正版。'}
    client['task_review'] = {'state':'recorded','version':current['version']+1,
                            'recorded_at':time.time(),**data}
    return copy.deepcopy(client['task_review'])
