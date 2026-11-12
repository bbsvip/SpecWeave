/* Explicit SSE decoder: handles UTF-8 chunks, LF/CRLF delimiters and split frames. */
(function(root){
  'use strict';
  function createParser(onFrame){
    let buffer='';
    function parse(raw){
      const data=[];
      for(const line of raw.split(/\r?\n/)){
        if(line.startsWith('data:'))data.push(line.slice(5).replace(/^ /,''));
      }
      if(!data.length)return;
      try{onFrame(JSON.parse(data.join('\n')));}catch(e){
        // Invalid SSE JSON is discarded; keep reading the connection.
      }
    }
    function drain(){
      let match;
      while((match=/\r?\n\r?\n/.exec(buffer))){
        parse(buffer.slice(0,match.index));
        buffer=buffer.slice(match.index+match[0].length);
      }
    }
    return {
      push(chunk){buffer+=String(chunk);drain();},
      finish(){if(buffer.trim())parse(buffer);buffer='';},
    };
  }
  root.SpecWeaveSSE={createParser};
  if(typeof module!=='undefined'&&module.exports)module.exports={createParser};
})(typeof window!=='undefined'?window:globalThis);
