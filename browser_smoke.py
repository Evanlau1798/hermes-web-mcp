"""Opt-in real Ego/MCP acceptance against an isolated loopback fixture."""
import asyncio
import base64
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import runtime
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


HTML = '''<!doctype html><meta charset="utf-8"><title>Browser acceptance</title>
<style>body{font:24px sans-serif;margin:40px}img{width:320px;height:100px}</style>
<label>訊息<input aria-label="訊息"></label><button id="advance">載入新記錄</button>
<button class="duplicate">重複</button><button class="duplicate">重複</button>
<button id="duplicate-record">切換重複記錄</button>
<main><article data-record-id="old" data-ready="true">舊內容</article></main>
<script>
window.actions=0; window.ambiguousActions=0;
document.querySelectorAll('.duplicate').forEach(n=>n.onclick=()=>window.ambiguousActions++);
document.querySelector('#duplicate-record').onclick=()=>{
  const duplicate=document.querySelector('#clone');
  if(duplicate) duplicate.remove();
  else { const clone=document.querySelector('main article').cloneNode(true);
    clone.id='clone';document.querySelector('main').append(clone); }
};
document.querySelector('#advance').onclick=()=>{
  window.actions++;
  setTimeout(()=>{
    document.querySelector('main').innerHTML=[
      '<article data-record-id="new" data-ready="true">',
      '<aside data-record-id="quote"><span data-field="author">引用作者</span>',
      '<span data-field="time">錯誤時間</span><span data-field="body">引用正文</span></aside>',
      '<h1 data-field="author">主作者</h1><time data-field="time">2026-01-01T12:00:00Z</time>',
      '<p data-field="body">繁體中文主內容</p><img id="media" alt="尚在載入"></article>'
    ].join('');
    setTimeout(()=>{
      const image=document.querySelector('#media');
      image.src='/media.svg';image.alt='綠色 READY 媒體';
    },1500);
  },400);
};
</script>'''
SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="320" height="100">
<rect width="320" height="100" fill="green"/><text x="30" y="60" fill="white"
font-size="36">READY</text></svg>'''


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        body = SVG if self.path == '/media.svg' else HTML
        self.send_response(200)
        self.send_header('Content-Type', 'image/svg+xml' if self.path == '/media.svg' else 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))

    def log_message(self, *_):
        pass


async def acceptance(url):
    params = StdioServerParameters(command=sys.executable, args=[str(runtime.ROOT/'server.py')],
        cwd=runtime.ROOT, env={'PYTHONIOENCODING':'utf-8'})
    folder = runtime.ROOT/'tmp'
    folder.mkdir(exist_ok=True)
    guide = (runtime.ROOT/'skills/software-development/hermes-web-mcp-workflows/references/browser.md').read_text(encoding='utf-8')
    fence = chr(96)*3
    record_example, media_example = re.findall(fence+r'js\n(.*?)\n'+fence, guide, re.S)
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as client:
            init = await client.initialize()
            assert len((await client.list_tools()).tools) == 28
            bootstrap = await client.call_tool('hermes_context', {'task':'isolated browser acceptance'})
            bid = bootstrap.structured_content['bootstrap']['bootstrap_id']
            witnesses = []
            images = []
            safe_cleanup = False
            last_session = None
            space = None
            space_name = f'Hermes generic acceptance {time.time_ns()}'

            async def call(code):
                nonlocal safe_cleanup, last_session
                safe_cleanup, last_session = False, None
                # Each invocation is new code; timeouts only poll its original owned process.
                reply = await client.call_tool('browser_use', {'action':'run','code':code,'bootstrap_id':bid},
                    read_timeout_seconds=60)
                deadline = time.monotonic()+90
                while True:
                    assert not reply.is_error, str(reply.structured_content)
                    data = reply.structured_content
                    last_session = data.get('session_id')
                    output = data.get('output', '')
                    for line in output.splitlines():
                        if line.startswith('WITNESS '):
                            witnesses.append(json.loads(line[8:]))
                    images.extend(block for block in reply.content if block.type == 'image')
                    if data.get('status') in ('exited','completed','already_exited'):
                        safe_cleanup = data.get('completion_reason','exited') == 'exited'
                        assert data.get('exit_code') == 0, output
                        assert not data.get('partial_output'), 'fixture should produce bounded output'
                        return data
                    assert time.monotonic() < deadline, data
                    await asyncio.sleep(1)
                    reply = await client.call_tool('hermes_process_read', {'action':'poll',
                        'session_id':data['session_id'],'bootstrap_id':bid}, read_timeout_seconds=60)

            try:
                await call(f'const task=await taskSpace({json.dumps(space_name)}); '
                    'console.log("WITNESS "+JSON.stringify({spaceId:task.spaceId,phase:"created"}));')
                space = witnesses[-1]['spaceId']
                resume = f'const task=await taskSpace({space}); const page=task.page("p1");\n'
                await call(resume+f'await page.goto({json.dumps(url)}); '
                    'console.log(await page.snapshot()); '
                    'console.log("WITNESS "+JSON.stringify({phase:"initial",'
                    'old:await page.evaluate(()=>document.querySelector("article").dataset.recordId)}));')
                assert witnesses[-1]['old'] == 'old'
                await call(resume+'''
let ambiguity=false;
try { await page.click("loc=css:.duplicate"); }
catch(error) { ambiguity=error.name==="ElementResolutionError"; }
if(!ambiguity) throw new Error("SDK did not reject ambiguous selector");
await page.fill("loc=role:textbox[name='訊息']", "繁體中文 English");
await page.click("loc=role:button[name='載入新記錄']");
let wrongWait=false;
try { await page.waitForFunction(()=>false, undefined, {timeout:100}); }
catch(error) { wrongWait=/timeout|timed out/i.test(error.name+" "+error.message);
  if(!wrongWait) throw new Error("Unexpected wait error: "+error.name+" "+error.message); }
if(!wrongWait) throw new Error("bad wait condition did not time out");
const recordId="new", mainSelector="main";
'''+record_example+'''
const fields=await page.evaluate(inspectRecord,{id:recordId,scope:mainSelector,read:true});
const before=await page.evaluate(()=>({width:document.querySelector("#media").naturalWidth,
  actions:window.actions,ambiguousActions:window.ambiguousActions,
  input:document.querySelector("input").value}));
if(fields.fields.author.text!=="主作者"||fields.fields.body.text!=="繁體中文主內容"
   ||fields.fields.time.text!=="2026-01-01T12:00:00Z"||!fields.ready||before.width!==0)
  throw new Error("wrong entity fields or missing delayed-media witness");
console.log("WITNESS "+JSON.stringify({phase:"fields",fields,before}));
await page.click("loc=role:button[name='切換重複記錄']");
const duplicate=await page.evaluate(inspectRecord,{id:recordId,scope:mainSelector,read:true});
if(duplicate.ready||duplicate.matches!==2) throw new Error("duplicate record was not rejected");
await page.click("loc=role:button[name='切換重複記錄']");
const mediaSelector="#media";
'''+media_example+'''
console.log("WITNESS "+JSON.stringify({phase:"media",spaceId:task.spaceId,
  state:await page.evaluate(()=>({width:document.querySelector("#media").naturalWidth,
    actions:window.actions,ambiguousActions:window.ambiguousActions}))}));
''')
                fields, media = witnesses[-2:]
                assert fields['before'] == {'width':0,'actions':1,'ambiguousActions':0,'input':'繁體中文 English'}
                assert media['state'] == {'width':320,'actions':1,'ambiguousActions':0}
                assert media['spaceId'] == space
                assert len(images) == 1
                (folder/'browser-smoke.png').write_bytes(base64.b64decode(images[0].data))
                print(json.dumps({'version':init.server_info.version,'passed':[
                    'old-to-ready identity','quote exclusion','bounded field extraction','wrong wait recovery',
                    'ambiguous selector rejected','duplicate identity rejected','UTF-8 input','media 0-to-decoded',
                    'same TaskSpace across rounds','one action','MCP image block'],
                    'image':'tmp/browser-smoke.png','host_display':'unverified'},ensure_ascii=False))
            finally:
                # A lost CLI receipt cannot authorize racing finish against a live browser script.
                if safe_cleanup:
                    if space is not None:
                        cleanup = f'const task=await taskSpace({space}); await task.finish({{keep:[]}});'
                    else:
                        cleanup = ('const matches=(await listTaskSpaces()).filter(s=>s.name==='
                            +json.dumps(space_name)+' && s.ownership==="agent"); '
                            'if(matches.length===1) await (await taskSpace(matches[0].id)).finish({keep:[]});')
                    await call(cleanup+' console.log("fixture finished");')
                else:
                    print(json.dumps({'cleanup':'deferred','space_id':space,'space_name':space_name,
                        'session_id':last_session,'reason':'unconfirmed process outcome; inspect before cleanup'}))


def main():
    service = ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    thread = threading.Thread(target=service.serve_forever,daemon=True)
    thread.start()
    try:
        asyncio.run(acceptance(f'http://127.0.0.1:{service.server_port}/'))
    finally:
        service.shutdown()
        service.server_close()
        thread.join()


if __name__ == '__main__':
    main()
