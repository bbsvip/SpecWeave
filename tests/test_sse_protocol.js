const assert=require('node:assert/strict');
const {createParser}=require('../source/static/js/sse_protocol.js');
const items=[];
const parser=createParser(item=>items.push(item));
const utf8=new TextEncoder().encode('data: {"type":"delta","payload":{"text":"Tiếng Việt 🌿"}}\r\n\r\n');
const decoder=new TextDecoder();
for(let i=0;i<utf8.length;i+=2){
  parser.push(decoder.decode(utf8.slice(i,i+2),{stream:true}));
}
parser.push(decoder.decode());
parser.push('data: {"type":"usage","payload":{"input_tokens":42}}\n\n');
parser.push(': keep-alive\n\n');
parser.push('data: {"type":"done","payload":{}}');
parser.finish();
assert.equal(items.length,3);
assert.equal(items[0].payload.text,'Tiếng Việt 🌿');
assert.equal(items[1].payload.input_tokens,42);
assert.equal(items[2].type,'done');
console.log('SpecWeave SSE framing tests passed');

// A valid error frame must propagate a callback error rather than disappearing
// behind the invalid-JSON catch block.
{
  const reader = createParser((frame) => {if(frame.type === 'error') throw Error('model failed');});
  assert.throws(() => reader.push('data: {"type":"error"}\n\n'), /model failed/);
}
