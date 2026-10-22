"""Safe diagnostic facts, never arbitrary native text, credentials or prompts."""
import json
import re

AUTH='Your access token could not be refreshed because you have since logged out or signed in to another account. Please sign in again.'


def error_details(value, stage, turn_id=None):
    if isinstance(value,BaseException):
        prior=getattr(value,'details',{})
        data={**(prior if isinstance(prior,dict) else {}),'errorCode':getattr(value,'code',None)}
    else:
        data=value if isinstance(value,dict) else {}
    if isinstance(data.get('error_details'),dict):
        data={**data,**data['error_details']}
    error=data.get('error')
    nested=error if isinstance(error,dict) else {}
    message=data.get('nativeMessage') or data.get('message') or nested.get('message') or (error if isinstance(error,str) else '')
    message=message if isinstance(message,str) else ''
    code=data.get('errorCode') or data.get('code') or nested.get('code')
    if re.search(r'access token could not be refreshed|refresh_token_(?:reused|expired)|logged out or signed in to another account',message,re.I):
        code='AUTHENTICATION_REQUIRED'
    elif re.search(r'active.writer|open in another app',message,re.I):
        code='BACKEND_WRITER_CONFLICT'
    if not isinstance(code,str) or not re.fullmatch('[A-Z][A-Z0-9_]{1,80}',code):
        code='NATIVE_ERROR_UNSPECIFIED'
    result={'stage':stage,'errorCode':code}
    if data.get('requestPurpose') in ('observation','operation'):
        result['requestPurpose']=data['requestPurpose']
    if isinstance(value,BaseException):
        result['nativeException']=type(value).__name__[:80]
    if code=='AUTHENTICATION_REQUIRED':
        if 'logged out or signed in to another account' in message or data.get('reasonCode')=='AUTH_ACCOUNT_CHANGED':
            result['nativeMessage']=('Error running remote compact task: ' if 'Error running remote compact task:' in message else '')+AUTH
            result['reasonCode']='AUTH_ACCOUNT_CHANGED'
        else:
            result['nativeMessage']='Codex authentication requires sign-in; inspect the owner account state.'
    elif code=='BACKEND_WRITER_CONFLICT':
        result['nativeMessage']='Codex reports another active writer; ownership is not transferred.'
    for key,pattern in {'requestId':r'[A-Za-z0-9_:.-]{1,160}', 'source':r'[A-Za-z0-9_-]{1,64}',
                        'nativeMethod':r'[A-Za-z0-9_/.-]{1,80}', 'nativeStage':r'[A-Za-z0-9_/.-]{1,64}',
                        'causeFingerprint':r'[a-f0-9]{16,64}', 'relatedTurnId':r'[A-Za-z0-9_-]{1,256}',
                        'nativeException':r'[A-Za-z0-9_]{1,80}'}.items():
        candidate=data.get(key)
        if isinstance(candidate,str) and re.fullmatch(pattern,candidate):
            result[key]=candidate
    if data.get('stage') and data['stage']!=stage and isinstance(data['stage'],str) and re.fullmatch(r'[a-z_/.-]{1,64}',data['stage']):
        result['nativeStage']=data['stage']
    native=data.get('nativeCode',nested.get('code'))
    if type(native) is int or isinstance(native,str) and re.fullmatch('[A-Z][A-Z0-9_]{1,80}',native):
        result['nativeCode']=native
    if isinstance(turn_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}',turn_id):
        result['relatedTurnId']=turn_id
    return result


def stored_details(raw):
    try:
        data=json.loads(raw) if isinstance(raw,str) else raw
        if isinstance(data,dict):
            return error_details(data,data.get('stage') if data.get('stage') in (
                'status','compact_start','compact_poll','native_turn','observe_turn','worker') else 'worker')
    except (ValueError,TypeError):
        pass
    return None
