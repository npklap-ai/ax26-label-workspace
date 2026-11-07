const test = require('node:test');
const assert = require('node:assert/strict');
const {consume, validateBytes, validateTable, sourceUrl} = require('../stream.js');
const stream = parts => new ReadableStream({start(c){for(const p of parts)c.enqueue(p);c.close();}});
test('split UTF8 and newline records preserve Korean and evidence and require completion', async()=>{
 const bytes = new TextEncoder().encode('{"type":"delta","text":"한글"}\n{"type":"evidence","data":{"title":"근거"}}\n{"type":"completed"}\n');
 let state;
 state=await consume(stream([...bytes].map(x=>new Uint8Array([x]))));
 assert.equal(state.text,'한글');assert.equal(state.completed,true);assert.equal(state.evidence[0].title,'근거');
});
test('EOF or malformed record throws while retaining delivered partial text',async()=>{
 let partial='';
 await assert.rejects(consume(stream([new TextEncoder().encode('{"type":"delta","text":"일부"}\n')]),{onDelta:t=>partial+=t}),/완료/);
 assert.equal(partial,'일부');
 await assert.rejects(consume(stream([new TextEncoder().encode('broken\n')])),/JSON/);
});
test('failure before completion cannot become success and diagnostic detail is retained',async()=>{
 await assert.rejects(consume(stream([new TextEncoder().encode('{"type":"error","message":"검색 실패","code":"TOOL","stage":"search"}\n{"type":"completed"}\n')])),/검색 실패.*TOOL.*search/);
});
test('completion stops a transport that stays open',async()=>{
 let cancelled=false;
 const body=new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('{"type":"completed"}\n'));},cancel(){cancelled=true;}});
 const state=await consume(body);
 assert.equal(state.completed,true);assert.equal(cancelled,true);
});
test('aborted stream rejects and releases reader',async()=>{
 const controller=new AbortController();
 const body=new ReadableStream({start(){}});
 const pending=consume(body,{signal:controller.signal});
 controller.abort(new DOMException('중단','AbortError'));
 await assert.rejects(pending,/중단/);
 assert.equal(body.locked,false);
});
test('UTF8 exact boundary passes and oversized text is not truncated',()=>{
 const original='한'.repeat(349525)+' ';
 assert.equal(validateBytes(original),original);
 const oversize=original+'한';
 assert.throws(()=>validateBytes(oversize),/1 MiB/);
 assert.equal(oversize.length,original.length+1);
});
test('table rejects row column cell character and header limits',()=>{
 assert.throws(()=>validateTable(Array.from({length:2001},()=>['x'])),/2000/);
 assert.throws(()=>validateTable([Array(31).fill('x')]),/30/);
 assert.throws(()=>validateTable(Array.from({length:1001},()=>Array(20).fill('x'))),/20000/);
 assert.throws(()=>validateTable([['x'.repeat(1001)]]),/1000/);
 assert.throws(()=>validateTable([['x'.repeat(101)]],{header:true}),/100/);
 assert.doesNotThrow(()=>validateTable([['원료명','배합비'],['한글','50']],{header:true}));
});
test('evidence links accept only explicit http(s) URL fields',()=>{
 assert.equal(sourceUrl({url:'javascript:alert(1)'}),null);
 assert.equal(sourceUrl({text:'https://example.com'}),null);
 assert.equal(sourceUrl({source_url:'https://example.com/law'}),'https://example.com/law');
});
test('timeout stays distinct from user abort and duplicate event ids do not repeat text',async()=>{
 const body=new ReadableStream({start(){}});
 const controller=new AbortController();
 const pending=consume(body,{signal:controller.signal});
 controller.abort(new DOMException('대기 시간 초과','TimeoutError'));
 await assert.rejects(pending,error=>error.name==='TimeoutError');
 const encoded=new TextEncoder().encode('{"type":"delta","event_id":"a","text":"한"}\n{"type":"delta","event_id":"a","text":"한"}\n{"type":"completed"}\n');
 assert.equal((await consume(stream([encoded]))).text,'한');
});
test('invalid UTF8 and oversized NDJSON line fail without a success state',async()=>{
 await assert.rejects(consume(stream([new Uint8Array([0xff,0x0a])])));
 await assert.rejects(consume(stream([new TextEncoder().encode('x'.repeat(1048577))])),/제한/);
});
test('backend upstreamStatus and retryAfter diagnostics are displayed alongside standard aliases',async()=>{
 const backendEvent={type:'error',message:'요청 한도',code:'UPSTREAM_HTTP',stage:'messages',upstreamStatus:429,retryAfter:'45'};
 await assert.rejects(consume(stream([new TextEncoder().encode(JSON.stringify(backendEvent)+'\n')])),/요청 한도.*UPSTREAM_HTTP.*messages.*HTTP 429.*45/);
 const {errorMessage}=require('../stream.js');
 assert.match(errorMessage({message:'오류',status:503,retry_after:'60'}),/HTTP 503.*60/);
});


test('request id uses cryptographic fallback in HTTP browsers without randomUUID', () => {
  const fallback = {getRandomValues(bytes) { bytes.fill(0); return bytes; }};
  assert.equal(require('../stream.js').requestId(fallback), '00000000-0000-4000-8000-000000000000');
  assert.match(require('../stream.js').requestId(), /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  assert.throws(() => require('../stream.js').requestId({}), /안전한 요청 ID/);
});
