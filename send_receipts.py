"""Content-free intent ledger and read-only compatibility with older receipts."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from operation_summary import compact_receipt


def error(code, operation_id=None, thread_id=None):
    delivery='unknown' if code in ('RECEIPT_CONFLICT','OPERATION_NOT_FOUND','RECEIPT_STORE_UNAVAILABLE') else 'not-sent'
    return {'ok':False,'operation_id':operation_id,'thread_id':thread_id,'state':'rejected',
            'delivery_operation_id':None,'deliveryState':delivery,'delivery_state':delivery,
            'errorCode':code,'automaticRetrySafe':False,'executionComplete':False,
            'summary':{'delivery':delivery,'task_completion':'not_established'}}


def normalize(result, operation_id, thread_id=None):
    result=dict(result)
    delivery=result.get('delivery_state') or result.get('deliveryState') or 'not_started'
    result.update(operation_id=operation_id,thread_id=result.get('thread_id') or result.get('threadId') or thread_id,
                  deliveryState=delivery,delivery_state=delivery,automaticRetrySafe=False,executionComplete=False)
    result.setdefault('delivery_operation_id',operation_id)
    result.setdefault('state','delivery_'+delivery.replace('-','_'))
    result.setdefault('ok',delivery in ('accepted','not_started'))
    result.setdefault('summary',{'delivery':delivery,'task_completion':'not_established'})
    result.setdefault('created_at',result.get('observedAt'))
    result.setdefault('updated_at',result.get('observedAt'))
    return result


def read_row(path, sql, values):
    if not path.exists(): return None
    db=sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True,timeout=2)
    db.row_factory=sqlite3.Row
    try:
        row=db.execute(sql,values).fetchone()
        return dict(row) if row else None
    finally: db.close()


class SendReceipts:
    def __init__(self,root):
        self.root=Path(root)
        self.path=self.root/'send-intents.sqlite'

    @contextmanager
    def db(self):
        self.root.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=2)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, thread_id TEXT, result TEXT)')
                yield db
        finally: db.close()

    def get(self,operation_id):
        row=read_row(self.path,'SELECT thread_id,result FROM intents WHERE id=?',(operation_id,))
        return json.loads(row['result']) if row else None

    def parent(self,delivery_id):
        row=read_row(self.path,"SELECT result FROM intents WHERE json_extract(result,'$.delivery_operation_id')=?",(delivery_id,))
        return json.loads(row['result']) if row else None

    def reserve(self,operation_id,thread_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT result FROM intents WHERE id=?',(operation_id,)).fetchone()
            if row: return json.loads(row[0]),False
            if db.execute('SELECT count(*) FROM intents').fetchone()[0]>=10000:
                return error('RECEIPT_CAPACITY',operation_id,thread_id),False
            pending=normalize({'state':'reserved','deliveryState':'unknown',
                'errorCode':'DELIVERY_UNCONFIRMED','created_at':time.time(),'updated_at':time.time()},operation_id,thread_id)
            db.execute('INSERT INTO intents VALUES (?,?,?)',(operation_id,thread_id,json.dumps(pending)))
            return pending,True

    def save(self,result):
        # A whitelist prevents native text, titles or prompts entering this ledger.
        keys=('operation_id','thread_id','delivery_operation_id','state','deliveryState','delivery_state','summary',
              'errorCode','created_at','updated_at','requestId','turnBaseline','route','owner_pid')
        public={k:v for k,v in result.items() if k in keys}
        with self.db() as db:
            db.execute('UPDATE intents SET result=? WHERE id=?',(json.dumps(public),result['operation_id']))

    def legacy(self,operation_id):
        job=read_row(self.root/'compact-jobs.sqlite',
            'SELECT * FROM jobs WHERE operation_id=? OR delivery_operation_id=?',(operation_id,operation_id))
        delivery=read_row(self.root/'codex-delivery.sqlite','SELECT result,binding FROM receipts WHERE id=?',(operation_id,))
        if job and delivery and job['operation_id']==operation_id:
            return error('RECEIPT_CONFLICT',operation_id)
        if job: return compact_receipt(job)
        if delivery:
            result=json.loads(delivery['result'])
            binding=json.loads(delivery['binding'])
            return normalize(result,operation_id,binding[1] if isinstance(binding,list) and len(binding)==2 else None)
        return None
