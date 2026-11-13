/* SpecWeave Conversation Workspace — stream-first, project-scoped interactive planning. */
(() => {
  'use strict';
  const root=document.getElementById('discussionApp'); if(!root)return;
  const pid=root.dataset.project, did=root.dataset.discussion;
  const list=document.getElementById('messageList'),input=document.getElementById('chatInput');
  const send=document.getElementById('sendChat'),stop=document.getElementById('stopChat');
  const state=document.getElementById('agentState'),activity=document.getElementById('toolActivity');
  const suggestButton=document.getElementById('suggestPlanningQuestion');
  const questionSlot=document.getElementById('planningQuestion');
  const headerUsage=document.getElementById('sessionUsage');
  const progressElement=document.getElementById('discoveryProgress');
  let controller=null,runId=null,openQuestion=null,selectedIds=new Set();
  let discoveryDone=root.dataset.discoveryReady==='true';
  let streamingTimer=null,mermaidLibrary=null,mermaidCounter=0;
  let following=true,lastScrollTop=list.scrollTop;
  list.addEventListener('scroll',()=>{
    if(list.scrollTop<lastScrollTop-0.5)following=false;
    else if(list.scrollHeight-list.scrollTop-list.clientHeight<12)following=true;
    lastScrollTop=list.scrollTop;
  },{passive:true});
  list.addEventListener('wheel',event=>{if(event.deltaY<0)following=false;},{passive:true});
  const make=(tag,cls='',txt)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(txt!==undefined)node.textContent=txt;return node;};
  function followIfNearBottom(force=false){
    if(force)following=true;
    else if(list.scrollTop<lastScrollTop-0.5)following=false;
    if(following){list.scrollTop=list.scrollHeight;lastScrollTop=list.scrollTop;}
  }
  new ResizeObserver(()=>followIfNearBottom()).observe(list);
  new MutationObserver(()=>followIfNearBottom()).observe(list,{childList:true,subtree:true,characterData:true});
  function formatNumber(n){return typeof n==='number'?new Intl.NumberFormat('vi-VN').format(n):'—';}
  function hideUnfinishedDecisionJSON(raw,mode){
    // Do not show raw JSON choices in the chat while the model is writing them.
    // Final server message is parsed/normalized, including persisted session history.
    if(mode!=='discovery'&&mode!=='compare')return raw;
    const match=/(?:\[\s*)?\{\s*"(?:id|label|question|context|options|recommended_index|recommendation_reason)"\s*:/.exec(raw);
    if(match){
      const before=raw.slice(0,match.index).replace(/```(?:json)?\s*$/i,'').trimEnd();
      return before+'\n\n*Đang chuẩn hóa các lựa chọn…*';
    }
    return raw;
  }
  function decodeToolUnicode(text){
    return String(text).replace(/\\u([0-9a-fA-F]{4})/g,(_m,h)=>String.fromCharCode(parseInt(h,16)));
  }
  function decodeToolTree(value){
    if(typeof value==='string')return decodeToolUnicode(value);
    if(Array.isArray(value))return value.map(decodeToolTree);
    if(value&&typeof value==='object'){
      return Object.fromEntries(Object.entries(value).map(([key,val])=>[decodeToolUnicode(key),decodeToolTree(val)]));
    }
    return value;
  }
  function formatToolValue(value){
    // Display only. The provider receives the original arguments unchanged.
    if(value===null||value===undefined)return '';
    let parsed=value;
    if(typeof parsed==='string'){
      try{parsed=JSON.parse(parsed);}catch{}
      if(typeof parsed==='string'){
        try{parsed=JSON.parse(parsed);}catch{}
      }
    }
    if(typeof parsed==='object'&&parsed!==null){
      // JSON.stringify emits actual UTF-8 Vietnamese characters, not \u escapes.
      return JSON.stringify(decodeToolTree(parsed),null,2);
    }
    const text=String(parsed);
    return decodeToolUnicode(text);
  }
  function renderMarkdown(node,content){
    if(window.marked&&window.DOMPurify){
      node.innerHTML=DOMPurify.sanitize(marked.parse(content,{breaks:true}),{USE_PROFILES:{html:true}});
      node.querySelectorAll('a').forEach(a=>{a.target='_blank';a.rel='noopener noreferrer';});
      // ThinkRail-like inline visual artifacts: render Mermaid only when opened.
      // Source text remains visible without CDN access. Strict mode forbids diagram HTML.
      node.querySelectorAll('pre code.language-mermaid').forEach(code=>{
        const pre=code.closest('pre');if(!pre||pre.parentElement?.classList.contains('mermaid-preview'))return;
        const source=code.textContent||'';
        const frame=make('details','mermaid-preview');
        const summary=make('summary','','Sơ đồ · xem trực quan / nguồn Mermaid');
        const diagram=make('div','mermaid-render');
        pre.replaceWith(frame);frame.append(summary,diagram,pre);
        frame.addEventListener('toggle',async()=>{
          if(!frame.open||frame.dataset.rendered)return;
          frame.dataset.rendered='loading';diagram.textContent='Đang render Mermaid…';
          try{
            if(!mermaidLibrary){
              mermaidLibrary=import('https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs').then(m=>m.default);
            }
            const mermaid=await mermaidLibrary;
            mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'neutral'});
            const id='sw-mermaid-'+(++mermaidCounter);
            const {svg}=await mermaid.render(id,source);
            diagram.innerHTML=DOMPurify.sanitize(svg,{USE_PROFILES:{svg:true,svgFilters:true}});
            frame.dataset.rendered='done';
          }catch(e){diagram.textContent='Không render được sơ đồ; mã Mermaid vẫn có sẵn bên dưới.';frame.dataset.rendered='error';}
        });
      });
      node.querySelectorAll('pre').forEach(pre=>{
        const b=make('button','code-copy','Sao chép');b.type='button';
        b.addEventListener('click',()=>navigator.clipboard?.writeText(pre.querySelector('code')?.textContent||pre.textContent||'').then(()=>{b.textContent='Đã sao chép';}));
        pre.append(b);
      });
    }else node.textContent=content;
  }
  list.querySelectorAll('.markdown-content').forEach(node=>renderMarkdown(node,node.textContent));
  function addMessage(who,text=''){
    list.querySelector('.conversation-intro')?.remove();
    const article=make('article','message '+(who==='user'?'message-user':'message-agent'));
    article.dataset.createdAt=new Date().toISOString().replace('Z','');
    const avatar=make('div','message-avatar',who==='user'?'A':'✦');
    const body=make('div','message-body');
    const meta=make('div','message-meta',who==='user'?'Anh':'SpecWeave AI');
    const content=make('div','message-content'+(who==='assistant'?' markdown-content':''),text);
    body.append(meta,content);article.append(avatar,body);
    list.append(article);
    followIfNearBottom();
    return {article,body,content};
  }
  function buildTurnActivity(turn){
    const surface=make('section','turn-live');
    const live=make('div','turn-live-heading');
    const spinner=make('span','turn-loader');spinner.setAttribute('aria-hidden','true');
    const heading=make('span','turn-live-label','SpecWeave đang chuẩn bị phiên…');live.append(spinner,heading);
    const details=make('details','turn-activity');
    const summary=make('summary','','Hoạt động của agent · đang chạy');
    const inner=make('div','turn-activity-body');
    const timeline=make('div','turn-timeline');
    const thinking=make('details','turn-thinking');thinking.hidden=true;
    const thinkingTitle=make('summary','','Thinking · model đang phân tích');
    const thinkingText=make('pre','turn-thinking-text');thinking.append(thinkingTitle,thinkingText);
    const tools=make('div','turn-tool-list');
    inner.append(thinking,timeline,tools);details.append(summary,inner);
    const metrics=make('div','turn-metrics turn-metrics-visible','Đang đo thời gian phản hồi…');
    surface.append(live,details,metrics);turn.body.append(surface);
    return {surface,live,spinner,heading,details,summary,timeline,thinking,thinkingTitle,thinkingText,tools,metrics,toolRows:new Map()};
  }
  const actionButton=(label,cls,cb)=>{const b=make('button','btn btn-sm '+cls,label);b.type='button';b.addEventListener('click',cb);return b;};
  function setBusy(active,label){
    send.disabled=active;input.disabled=active;
    stop.classList.toggle('d-none',!runId||!active);
    state.textContent=label||(active?'Đang trả lời…':'Sẵn sàng');
    root.classList.toggle('chat-busy',active);
    suggestButton&&(suggestButton.disabled=active);
    root.querySelectorAll('[data-planning-control]').forEach(button=>{button.disabled=active;});
  }
  function describeUsage(usage,first,elapsed,outputChars=0,outputTps=null){
    const parts=[];
    if(typeof usage.input_tokens==='number')parts.push('Input '+formatNumber(usage.input_tokens));
    if(typeof usage.output_tokens==='number')parts.push('Output '+formatNumber(usage.output_tokens));
    if(typeof usage.cache_read_tokens==='number')parts.push('Cache read '+formatNumber(usage.cache_read_tokens));
    if(typeof usage.cache_write_tokens==='number')parts.push('Cache write '+formatNumber(usage.cache_write_tokens));
    if(typeof usage.cost_usd==='number')parts.push('$'+usage.cost_usd.toFixed(5));
    if(typeof first==='number')parts.push('TTFT '+(first/1000).toFixed(2)+'s');
    if(typeof elapsed==='number')parts.push('Thời gian '+(elapsed/1000).toFixed(1)+'s');
    if(typeof outputTps==='number'&&Number.isFinite(outputTps)){
      parts.push(outputTps.toFixed(1)+' tok/s (trung bình)');
    }else if(typeof usage.output_tokens==='number'&&typeof first==='number'&&elapsed>first){
      parts.push((usage.output_tokens/((elapsed-first)/1000)).toFixed(1)+' tok/s (trung bình)');
    }else if(outputChars>0&&typeof first==='number'&&elapsed>first+250){
      parts.push('~'+(outputChars/4/((elapsed-first)/1000)).toFixed(1)+' tok/s (ước tính ký tự)');
    }
    if(!('input_tokens' in usage)&&!('output_tokens' in usage))parts.unshift('Token thực: chưa có');
    return parts.length?parts.join(' · '):'Usage: provider chưa cung cấp';
  }
  function addTimeline(timeline,type,text){
    const row=make('div','activity-line');
    row.append(make('span','activity-dot '+type),make('span','',text));
    timeline.append(row);if(timeline.children.length>32)timeline.firstChild.remove();
  }
  async function talk({continueQuestionId=null,answerQuestion=null,advancePlanning=false,resumeRunId=null}={}){
    if(controller||!did)return false;
    // The worker owns continuation and can finish it after this tab closes.
    const message=(continueQuestionId||answerQuestion||advancePlanning||resumeRunId)?'':input.value.trim();
    if(!continueQuestionId&&!answerQuestion&&!advancePlanning&&!resumeRunId&&!message)return false;
    let optimistic=null;
    if(message){followIfNearBottom(true);input.value='';input.style.height='auto';optimistic=addMessage('user',message);}
    if(answerQuestion)followIfNearBottom(true);
    document.getElementById('piRunPlaceholder')?.remove();
    const turn=addMessage('assistant','');const panel=buildTurnActivity(turn);
    followIfNearBottom();
    const role=sw.val('agentRole'),mode=sw.val('agentMode');
    let full='',usage={},firstTokenMs=null,elapsedMs=0,completed=false,outputTps=null;
    let thinkingFull='',lastPhase='request',answerAccepted=false,nextQuestionMissing=false;
    let observing=Boolean(resumeRunId), terminalStatus=null;
    const started=performance.now();let renderScheduled=false,renderHandle=null;
    const scheduleRender=()=>{
      if(renderScheduled)return;renderScheduled=true;
      renderHandle=setTimeout(()=>{renderScheduled=false;renderHandle=null;renderMarkdown(turn.content,hideUnfinishedDecisionJSON(full,mode));followIfNearBottom();},95);
    };
    const flushRender=()=>{
      if(renderHandle!==null)clearTimeout(renderHandle);
      renderScheduled=false;renderHandle=null;
      turn.content.hidden=!full.trim();
      renderMarkdown(turn.content,full);
    };
    runId=resumeRunId;controller=new AbortController();setBusy(true,'Đang kết nối…');
    headerUsage&&(headerUsage.textContent='Đang kết nối…');
    addTimeline(panel.timeline,'status','Kết nối tới agent');
    streamingTimer=setInterval(()=>{
      if(terminalStatus)return;
      elapsedMs=performance.now()-started;
      panel.metrics.textContent=describeUsage(usage,firstTokenMs,elapsedMs,full.length,outputTps);
      if(headerUsage)headerUsage.textContent=panel.metrics.textContent;
    },350);
    try{
      // Start a durable job; the browser owns only its EventSource subscriber.
      // Closing/navigating the tab never aborts the lượt chạy.
      const target=answerQuestion?
        `/api/projects/${pid}/planning/questions/${encodeURIComponent(answerQuestion.question.id)}/answer-start`:
        `/api/projects/${pid}/chat/start`;
      const payload=answerQuestion?
        {...answerQuestion.data,role,mode:'discovery'}:
        {discussion_id:did,message,role,mode:continueQuestionId||advancePlanning?'discovery':mode,
         ...(continueQuestionId?{continue_question_id:continueQuestionId}:{}),
         ...(advancePlanning?{advance_planning:true}: {})};
      let queued;
      if(resumeRunId){
        queued={run_id:resumeRunId,events_url:`/api/projects/${pid}/runs/${encodeURIComponent(resumeRunId)}/events`};
        addTimeline(panel.timeline,'status','Đã nối lại lượt chạy trên server');
      }else{
        const body=JSON.stringify(payload);
        const keepalive=new TextEncoder().encode(body).length<60000;
        const response=await fetch(target,{
          method:'POST',credentials:'same-origin',keepalive,
          headers:{'Content-Type':'application/json','X-CSRF-Token':sw.csrf},
          body,signal:controller.signal,
        });
        const data=await response.json().catch(()=>({}));
        if(response.status===409){
          // F5/double-submit race: a durable lượt chạy may already have been
          // committed by the preceding tab. Attach it; do NOT send a second
          // prompt or create a second run on this discussion.
          const active=await fetch(`/api/projects/${pid}/discussions/${did}/active-run`,{
            credentials:'same-origin',cache:'no-store',signal:controller.signal
          });
          if(active.ok){
            const existing=await active.json();
            if(existing.run_id){
              queued={run_id:existing.run_id,
                events_url:`/api/projects/${pid}/runs/${encodeURIComponent(existing.run_id)}/events`};
              observing=true;
              // The new message was NOT accepted. Remove its optimistic bubble
              // and restore the input while observing the earlier lượt chạy.
              if(optimistic){optimistic.article.remove();optimistic=null;}
              if(message&&!input.value.trim())input.value=message;
              addTimeline(panel.timeline,'status','Đã nối lại lượt SpecWeave có sẵn; tin nhắn mới chưa được gửi');
            }
          }
        }
        if(!queued){
          if(!response.ok)throw new Error(data.detail||data.message||`HTTP ${response.status}`);
          queued=data;
        }
      }
      if(!queued.run_id)throw new Error('Server chưa trả về lượt chạy ID');
      runId=queued.run_id;
      setBusy(true,'Đang kết nối với SpecWeave…');
      answerAccepted=Boolean(answerQuestion&&!observing);
      if(answerAccepted){
        panel.heading.textContent='Đã nhận câu trả lời · đang xử lý…';
        setBusy(true,'Đang xử lý câu trả lời…');
        renderPlanningQuestion(null);refreshDiscoveryProgress();refreshPlanningQuestion();
      }
      const historyStatus=document.querySelector(`[data-run-status="${CSS.escape(runId)}"]`);
      const showStatus=(status,label)=>{
        terminalStatus=['completed','cancelled','error'].includes(status)?status:terminalStatus;
        if(historyStatus){
          historyStatus.textContent=label||status;
          historyStatus.classList.toggle('good',status==='completed'&&!label);
        }
      };
      const handleFrame=(ev)=>{
        const p=ev.payload||{};
        if(ev.type==='start'){
          showStatus('running');
          const message=`${p.model||'Agent'} · ${p.backend||'model'}`;
          panel.summary.textContent='Hoạt động · '+message;
          panel.heading.textContent='Đang phân tích với '+String(p.model||'model')+'…';
          addTimeline(panel.timeline,'status','Bắt đầu: '+message);
          setBusy(true,'Đang phân tích…');
        }else if(ev.type==='status'){
          if(['queued','waiting_lock'].includes(p.phase))showStatus('queued');
          const message=String(p.message||'Đang xử lý…');
          setBusy(true,message);panel.heading.textContent=message;
          if(p.phase!==lastPhase||panel.timeline.lastElementChild?.textContent!==message){
            addTimeline(panel.timeline,'status',message);
          }
          lastPhase=p.phase||'';
        }else if(ev.type==='thinking'){
          const piece=String(p.text||'');
          if(piece){
            thinkingFull+=piece;
            panel.thinking.hidden=false;
            panel.thinkingText.textContent=thinkingFull;
            panel.heading.textContent='Đang suy luận…';
            if(firstTokenMs===null)firstTokenMs=Math.round(performance.now()-started);
            if(panel.thinking.open)panel.thinkingText.scrollTop=panel.thinkingText.scrollHeight;
          }
        }else if(ev.type==='delta'){
          if(firstTokenMs===null)firstTokenMs=Math.round(performance.now()-started);
          full+=String(p.text||'');scheduleRender();
          setBusy(true,'Đang trả lời…');panel.heading.textContent='Đang viết câu trả lời…';
        }else if(ev.type==='timing'){
          if(typeof p.first_token_ms==='number')firstTokenMs=p.first_token_ms;
        }else if(ev.type==='tool'){
          const name=String(p.name||'tool').slice(0,90);
          const toolStatus=p.status==='complete'?'✓ Hoàn tất':
            p.status==='toolcall_end'?'Đã chuẩn bị tham số':
            ['update','toolcall_delta'].includes(p.status)?'Đang cập nhật tham số':
            ['preparing','toolcall_start'].includes(p.status)?'Đang chuẩn bị công cụ':
            p.status==='error'?'Thất bại':p.status==='queued'?'Sắp thực thi':'Đang chạy';
          panel.heading.textContent='Công cụ: '+name+' · '+toolStatus;
          const key=String(p.id||name);
          let item=panel.toolRows.get(key);
          if(!item){
            const details=make('details','turn-tool');
            const title=make('summary','','◌ '+name+' · Đang chạy');
            const text=make('pre','turn-tool-detail','');
            details.append(title,text);panel.tools.append(details);
            item={details,title,text,args:null,result:null,progress:null};panel.toolRows.set(key,item);
          }
          item.title.textContent=(p.status==='complete'?'✓ ':p.status==='running'?'◌ ':'↻ ')+name+' · '+toolStatus;
          if(p.status==='complete')item.details.classList.add('tool-complete');
          if(p.arguments!==undefined){
            const args=formatToolValue(p.arguments);
            item.args=p.status==='toolcall_delta'?(item.args||'')+args:args;
          }
          if(p.result!==undefined)item.result=formatToolValue(p.result);
          if(p.summary)item.progress=String(p.summary);
          item.text.textContent=[
            item.args!==null?'Input\n'+item.args:null,
            item.progress?'Tiến độ\n'+item.progress:null,
            item.result!==null?'Output\n'+item.result:null
          ].filter(Boolean).join('\n\n');
          if(!['update','toolcall_delta'].includes(p.status)){
            addTimeline(panel.timeline,'tool',name+' · '+toolStatus);
            if(activity.textContent==='Chưa có tool call')activity.replaceChildren();
            if(!item.activityRow){
              item.activityRow=make('div','tool-item');activity.prepend(item.activityRow);
              if(activity.children.length>15)activity.lastElementChild?.remove();
            }
            item.activityRow.textContent=name+' · '+toolStatus;
          }
        }else if(ev.type==='usage'){
          usage={...usage,...p};
          panel.metrics.textContent=describeUsage(usage,firstTokenMs,elapsedMs,full.length,outputTps);
          if(headerUsage)headerUsage.textContent=panel.metrics.textContent;
        }else if(ev.type==='final'&&typeof p.text==='string'){
          full=p.text;scheduleRender();
        }else if(ev.type==='planning_question'){
          // Tool-created questions are first-class chat events, not a hidden sidebar operation.
          renderPlanningQuestion(p);renderQuestionTimeline([{...p,run_id:runId}],turn);addTimeline(panel.timeline,'status','AI đã đưa ra câu hỏi để anh xác nhận');
        }else if(ev.type==='planning_needs_input'){
          nextQuestionMissing=true;
        }else if(ev.type==='planning_complete'){
          discoveryDone=true;
          renderPlanningQuestion(null);
          const notice=make('section','planning-handoff');
          notice.append(make('strong','','Đã có dữ liệu để soạn tài liệu dự án'));
          notice.append(make('p','',p.summary||'Các bản nháp cần được kiểm tra và phê duyệt.'));
          const a=make('a','btn btn-primary btn-sm','Xem bản nháp');
          a.href=p.url||`/projects/${pid}/artifacts`;notice.append(a);
          turn.body.append(notice);
          addTimeline(panel.timeline,'status','Discovery kết thúc; tài liệu draft sẵn sàng review');
        }else if(ev.type==='done'){
          const notice=p.notice;
          showStatus('completed',notice?.label);
          completed=true;elapsedMs=typeof p.duration_ms==='number'?p.duration_ms:performance.now()-started;
          if(typeof p.first_token_ms==='number')firstTokenMs=p.first_token_ms;
          usage={...usage,...(p.usage||{})};
          outputTps=typeof p.output_tps==='number'?p.output_tps:null;
          const label=notice?.label||'Hoàn thành';
          panel.summary.textContent='Hoạt động · '+(elapsedMs/1000).toFixed(1)+'s';
          panel.heading.textContent=label;panel.spinner.classList.add('completed');
          if(notice?.message&&!turn.article.dataset.questionId)panel.live.after(make('p','turn-live-helper',notice.message));
          panel.details.open=false;
          addTimeline(panel.timeline,'status',label);
        }else if(ev.type==='error'){
          showStatus(p.status==='cancelled'?'cancelled':'error');
        }
      };
      await new Promise((resolve,reject)=>{
        const url=queued.events_url||`/api/projects/${pid}/runs/${encodeURIComponent(runId)}/events`;
        const source=new EventSource(url,{withCredentials:true});
        let lastEventId=0;
        let settled=false;
        const finish=(error)=>{
          if(settled)return;
          settled=true;source.close();
          controller?.signal.removeEventListener('abort',onAbort);
          error?reject(error):resolve();
        };
        const onAbort=()=>finish(new DOMException('Đã ngắt theo dõi; SpecWeave chỉ hủy khi bấm Dừng','AbortError'));
        controller.signal.addEventListener('abort',onAbort,{once:true});
        source.onmessage=(raw)=>{
          if(settled)return;
          try{
            const frame=JSON.parse(raw.data);
            if(frame.run_id!==runId)return;
            const eventId=Number(raw.lastEventId);
            if(eventId&&eventId<=lastEventId&&frame.type!=='done'&&frame.type!=='error')return;
            lastEventId=Math.max(lastEventId,eventId||0);
            handleFrame(frame);
            if(frame.type==='done')finish();
            else if(frame.type==='error'){
              const error=new Error(frame.payload?.message||'lượt chạy failed');
              if(terminalStatus==='cancelled')error.name='AbortError';
              finish(error);
            }
          }catch(e){finish(e);}
        };
        source.onopen=()=>{
          if(!settled)state.textContent='Đang theo dõi SpecWeave…';
        };
        source.onerror=()=>{
          if(settled)return;
          if(source.readyState===EventSource.CLOSED){
            finish(new Error('Không thể mở luồng SpecWeave. Anh có thể thử kết nối lại.'));
            return;
          }
          // EventSource reconnects with Last-Event-ID, so no duplicate SpecWeave call.
          panel.heading.textContent='Mất kết nối, đang nối lại và tải các sự kiện còn thiếu…';
          state.textContent='Đang kết nối lại…';
        };
      });
      if(!completed)throw new Error('lượt chạy đã kết thúc nhưng thiếu sự kiện done');
      flushRender();
      panel.metrics.textContent=describeUsage(usage,firstTokenMs,elapsedMs,full.length,outputTps);
      if(headerUsage)headerUsage.textContent=panel.metrics.textContent;
      await refreshPlanningQuestion();
      await refreshDiscoveryProgress();
      followIfNearBottom();
      if((nextQuestionMissing||advancePlanning)&&!openQuestion&&!discoveryDone){
        if(panel.heading.textContent==='Hoàn thành')panel.heading.textContent='Discovery chưa hoàn tất';
        const note=make('p','turn-error',
          'Anh có thể bổ sung thông tin trong chat hoặc yêu cầu SpecWeave tiếp tục từ dữ liệu đã lưu.');
        const retry=actionButton('Tiếp tục với SpecWeave','btn-outline-primary',()=>{
          if(controller)return;
          talk({advancePlanning:true});
        });
        turn.body.append(note,retry);
      }
      return true;
    }catch(error){
      if(error.name==='AbortError'){
        if(full)flushRender();
        panel.summary.textContent='Đã dừng';panel.heading.textContent='Đã dừng';panel.spinner.classList.add('completed');
        addTimeline(panel.timeline,'status','Người dùng dừng phản hồi');
      }else{
        if(full)flushRender();
        if(optimistic&&!runId)optimistic.article.remove();
        if(message&&!runId&&!input.value.trim())input.value=message;
        panel.summary.textContent='Không hoàn thành';panel.heading.textContent='Không hoàn thành';panel.spinner.classList.add('completed');
        const msg=String(error.message||error).slice(0,400);
        addTimeline(panel.timeline,'error',msg);
        turn.content.append(make('p','turn-error','Lỗi: '+msg));
        sw.toast(msg,'error');
        if(runId&&!terminalStatus){
          const interruptedRun=runId;
          turn.body.append(actionButton('Kết nối lại với SpecWeave','btn-outline-primary',()=>{
            if(controller)return;
            turn.article.remove();
            talk({resumeRunId:interruptedRun});
          }));
        }else if(answerQuestion&&answerAccepted){
          const retry=actionButton('Thử tiếp tục trong cùng phiên SpecWeave','btn-outline-primary',()=>{
            retry.disabled=true;talk({continueQuestionId:answerQuestion.question.id});
          });turn.body.append(retry);
        }
      }
      return false;
    }finally{
      if(streamingTimer){clearInterval(streamingTimer);streamingTimer=null;}
      if(renderHandle!==null)clearTimeout(renderHandle);
      controller=null;runId=null;setBusy(false);
      if(!openQuestion)followIfNearBottom();
    }
  }
  function renderPlanningQuestion(q){
    if(q&&openQuestion?.id===q.id)return;
    questionSlot.replaceChildren();questionSlot.hidden=!q;openQuestion=q;selectedIds=new Set();
    if(!q)return;
    const panel=make('div','planning-brief');questionSlot.append(panel);
    panel.setAttribute('aria-label','Câu hỏi đang chờ xác nhận');
    panel.append(make('div','planning-dock-label','Câu hỏi đang chờ'));
    panel.append(make('h3','',q.question));
    const explanation=make('div','planning-explanation');
    panel.append(explanation);
    if(q.context)explanation.append(make('p','planning-description',q.context));
    const optHolder=make('div','planning-options');panel.append(optHolder);
    const optionNodes=[];
    for(const opt of q.options){
      const row=make('button','planning-option');row.type='button';row.setAttribute('aria-pressed','false');
      row.dataset.planningControl='true';row.disabled=Boolean(controller);
      row.append(make('span','planning-check',q.allow_multiple?'□':'○'));
      const body=make('div');const title=make('div','planning-option-title');
      title.append(make('span','',opt.label));
      if(opt.id===q.recommended_id)title.append(make('span','planning-recommended','AI đề xuất'));
      body.append(title);
      if(opt.description)body.append(make('div','planning-option-copy',opt.description));
      const trade=make('div','planning-tradeoffs');
      if(opt.pros)trade.append(make('div','up','Ưu điểm: '+opt.pros));
      if(opt.cons)trade.append(make('div','down','Hạn chế: '+opt.cons));
      if(trade.childNodes.length)body.append(trade);
      row.append(body);optHolder.append(row);
      row.addEventListener('click',()=>{
        if(controller)return;
        followIfNearBottom(true);
        if(!q.allow_multiple)selectedIds.clear();
        if(row.classList.contains('selected')&&q.allow_multiple)selectedIds.delete(opt.id);
        else selectedIds.add(opt.id);
        for(const item of optionNodes){
          const on=selectedIds.has(item.opt.id);item.node.classList.toggle('selected',on);
          item.node.setAttribute('aria-pressed',String(on));
          item.node.querySelector('.planning-check').textContent=on?'✓':q.allow_multiple?'□':'○';
        }
      });
      optionNodes.push({node:row,opt});
    }
    if(q.recommendation_reason){const why=make('div','planning-reason');why.append(make('strong','','Vì sao AI đề xuất: '),document.createTextNode(q.recommendation_reason));explanation.append(why);}
    if(q.impact)explanation.append(make('div','planning-extras','Tác động: '+q.impact));
    if(q.assumptions?.length)explanation.append(make('div','planning-extras','Giả định: '+q.assumptions.join('; ')));
    if(q.sources?.length){
      const sources=make('div','planning-extras');sources.append(make('strong','','Nguồn tham khảo: '));
      for(const src of q.sources){
        if(!/^https?:\/\//i.test(src.url))continue;
        const link=make('a','planning-source',src.title||src.url);link.href=src.url;link.target='_blank';link.rel='noopener noreferrer';sources.append(link);
      }
      explanation.append(sources);
    }
    const custom=make('textarea','planning-custom');custom.placeholder='Điều chỉnh hoặc nhập câu trả lời riêng…';panel.append(custom);
    const notes=make('input','planning-custom');notes.style.minHeight='38px';notes.placeholder='Ghi chú bổ sung (tùy chọn)';panel.append(notes);
    custom.setAttribute('aria-label',custom.placeholder);
    notes.setAttribute('aria-label',notes.placeholder);
    const actions=make('div','planning-actions');panel.append(actions);
    let submitting=false;
    async function submit(action){
      if(controller||submitting)return;
      if(action==='answer'&&!selectedIds.size&&!custom.value.trim()){
        sw.toast('Hãy chọn phương án hoặc nhập câu trả lời riêng.','error');
        custom.focus();
        return;
      }
      submitting=true;
      panel.classList.add('planning-loading');
      try{
        const data={action,selected_ids:[...selectedIds],custom_answer:custom.value,note:notes.value};
        if(action==='answer'){
          // The server saves the answer and enqueues SpecWeave in the same transaction.
          const ok=await talk({answerQuestion:{question:q,data}});
          if(!ok){
            panel.classList.remove('planning-loading');
            // If the request failed before it was accepted, the question is still pending.
            // Otherwise the chat turn exposes a safe retry continuation button.
            await refreshPlanningQuestion();
          }
        }else{
          await sw.api(`/api/projects/${pid}/planning/questions/${q.id}/answer`,data);
          renderPlanningQuestion(null);await refreshDiscoveryProgress();sw.toast('Đã hoãn câu hỏi này.','info');
        }
      }catch(e){sw.toast(e.message,'error');}
      finally{submitting=false;panel.classList.remove('planning-loading');}
    }
    actions.append(actionButton('Xác nhận & tiếp tục với AI','btn-primary',()=>submit('answer')));
    actions.append(actionButton('Thảo luận thêm','btn-outline-primary',()=>{
      input.value=`Hãy phân tích sâu câu hỏi: ${q.question}\nPhản biện phương án ${q.options.find(o=>o.id===q.recommended_id)?.label||'đề xuất'} và các rủi ro. Tôi chưa chốt phương án.`;
      input.focus();
    }));
    actions.append(actionButton('Tạm hoãn','btn-outline-secondary',()=>submit('defer')));
    followIfNearBottom();
  }
  function scrollToQuestion(){
    questionSlot.querySelector('button')?.focus({preventScroll:true});
  }
  function renderQuestionTimeline(questions,liveTurn=null){
    for(const q of questions){
      let article=list.querySelector(`[data-question-id="${CSS.escape(q.id)}"]`);
      const sources=(q.message_ids||[]).map(id=>list.querySelector(`[data-message-id="${CSS.escape(id)}"]`)).filter(Boolean);
      if(!article){
        article=sources.find(node=>node.classList.contains('message-agent'));
        if(!article&&liveTurn&&!liveTurn.article.dataset.questionId)article=liveTurn.article;
        if(!article){
          article=addMessage('assistant').article;
          article.dataset.createdAt=q.timeline_at||q.created_at;
          const next=[...list.querySelectorAll(':scope > article[data-created-at]')].find(node=>
            node!==article&&node.dataset.createdAt>article.dataset.createdAt);
          if(next)list.insertBefore(article,next);
        }
        article.dataset.questionId=q.id;
        article.dataset.questionRunId=q.run_id||'';
        article.classList.add('message-question');
      }
      const body=article.querySelector('.message-body');
      const meta=body.querySelector('.message-meta');
      if(q.created_at){
        const timestamp=meta.querySelector('span')||make('span');
        timestamp.textContent=new Date(q.created_at+'Z').toLocaleString('vi-VN');
        if(!timestamp.parentElement)meta.append(timestamp);
      }
      if(liveTurn&&liveTurn.article!==article&&article.dataset.questionRunId===q.run_id){
        body.append(liveTurn.content,...liveTurn.body.querySelectorAll('.turn-live'));
        liveTurn.article.remove();liveTurn.article=article;liveTurn.body=body;
      }
      for(const source of sources)if(source!==article)source.remove();
      // The brief and saved answer carry the visible content for this turn.
      for(const content of body.querySelectorAll(':scope > .message-content')){
        const generic=!content.textContent.trim()||/^(Chọn một phương án trong câu hỏi|Hãy xác nhận câu hỏi đang chờ)/.test(content.textContent.trim());
        if(generic)content.hidden=true;
      }
      body.querySelectorAll(':scope > p.small.text-secondary').forEach(node=>node.remove());
      if(q.status!=='pending'){
        const summary=body.querySelector(':scope > .turn-activity > summary');
        if(summary)summary.textContent=summary.textContent.replace(/^Chờ xác nhận/, 'Đã xử lý');
        const label=body.querySelector('.turn-live-label');
        if(label?.textContent==='Chờ xác nhận')label.textContent='Đã xử lý';
      }
      const previous=body.querySelector(':scope > .qa-card');
      const expanded=new Set([...previous?.querySelectorAll('details[open]')||[]].map(node=>node.dataset.section));
      const card=make('section','qa-card');card.setAttribute('aria-label','Câu hỏi và câu trả lời');
      const heading=make('div','qa-heading');
      heading.append(make('h3','',q.question));
      const statuses={answered:'Đã trả lời',deferred:'Tạm hoãn',superseded:'Nhánh trước',pending:'Chờ trả lời'};
      heading.append(make('span','qa-status',statuses[q.status]||q.status));card.append(heading);
      const versions=(q.answer_history||[]).filter(version=>['answered','deferred'].includes(version.status));
      const latest=versions[versions.length-1];
      const answer=['answered','deferred'].includes(q.status)?q.answer:(latest?.answer||{});
      const comparison=make('details','qa-options');comparison.dataset.section='options';comparison.open=expanded.has('options');
      comparison.append(make('summary','','Các phương án và đề xuất của AI'));
      if(q.context)comparison.append(make('p','qa-context',q.context));
      for(const option of q.options){
        const selected=(answer?.selected_ids||[]).includes(option.id);
        const row=make('div','qa-option'+(selected?' chosen':''));
        row.append(make('strong','',option.label+(selected?' · Đã chọn':'')));
        if(option.description)row.append(make('p','',option.description));
        row.append(make('p','',`Ưu điểm: ${option.pros}`),make('p','',`Hạn chế: ${option.cons}`));comparison.append(row);
      }
      if(q.recommendation_reason)comparison.append(make('p','',`AI đề xuất ${q.options.find(option=>option.id===q.recommended_id)?.label||''}. ${q.recommendation_reason}`));
      if(q.impact)comparison.append(make('p','',`Tác động: ${q.impact}`));
      if(q.assumptions?.length)comparison.append(make('p','',`Giả định: ${q.assumptions.join('; ')}`));
      for(const source of q.sources||[]){
        if(!/^https?:\/\//i.test(source.url))continue;
        const link=make('a','planning-source',source.title||source.url);
        link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';comparison.append(link);
      }
      card.append(comparison);
      function appendAnswer(target,value,label){
        const section=make('div','qa-answer');section.append(make('strong','qa-answer-label',label));
        if(value.selected_labels?.length)section.append(make('p','qa-answer-choice',value.selected_labels.join(', ')));
        if(value.custom_answer)section.append(make('p','',value.custom_answer));
        if(value.note)section.append(make('p','',value.note));
        target.append(section);
      }
      if(answer&&Object.keys(answer).length)appendAnswer(card,answer,q.status==='pending'?'Câu trả lời trước':'Câu trả lời');
      if(versions.length>1){
        const revisions=make('details','qa-revisions');revisions.dataset.section='revisions';revisions.open=expanded.has('revisions');
        revisions.append(make('summary','',`Các lần trả lời trước (${versions.length-1})`));
        for(const version of versions.slice(0,-1)){
          const date=version.recorded_at?new Date(version.recorded_at+'Z').toLocaleString('vi-VN'):'';
          appendAnswer(revisions,version.answer||{},date||'Câu trả lời trước');
        }
        card.append(revisions);
      }
      if(['answered','deferred'].includes(q.status)){
        const reopen=actionButton('Quay lại trả lời','btn-outline-primary',()=>{
          if(controller)return;
          const confirmation=make('div','planning-reopen-confirm');
          confirmation.append(make('p','','Các câu hỏi phía sau sẽ chuyển sang nhánh trước. Bản nháp sẽ cần cập nhật theo câu trả lời mới.'));
          const confirm=actionButton('Quay lại câu hỏi này','btn-primary',async()=>{
            confirm.disabled=true;
            try{
              const response=await sw.api(`/api/projects/${pid}/planning/questions/${q.id}/reopen`);
              discoveryDone=false;renderPlanningQuestion(response.question);
              await refreshPlanningQuestion();await refreshDiscoveryProgress();scrollToQuestion();
              sw.toast('Đã quay lại câu hỏi. Chọn câu trả lời mới để tiếp tục.','info');
            }catch(error){sw.toast(error.message,'error');confirm.disabled=false;}
          });
          confirmation.append(confirm,actionButton('Giữ câu trả lời hiện tại','btn-outline-secondary',()=>{confirmation.remove();reopen.hidden=false;}));
          reopen.hidden=true;card.append(confirmation);
        });
        reopen.dataset.planningControl='true';reopen.disabled=Boolean(controller);card.append(reopen);
      }
      if(previous)previous.replaceWith(card);
      else body.insertBefore(card,body.querySelector('.turn-live,.turn-history-metrics,.turn-activity'));
    }
    followIfNearBottom();
  }
  async function refreshDiscoveryProgress(){
    if(!progressElement||!did)return;
    try{
      const progress=await sw.api(`/api/projects/${pid}/planning/discovery-progress?discussion_id=${encodeURIComponent(did)}`,{},'GET');
      const finished=progress.status==='draft_ready';
      discoveryDone=finished;
      progressElement.replaceChildren();
      progressElement.append(make('strong','',finished?'Đủ dữ liệu · đã tạo draft':'Đang làm rõ với SpecWeave'));
      progressElement.append(make('span','',`${progress.answered} đã xác nhận · ${progress.pending} chờ trả lời · ${progress.deferred} hoãn`));
      progressElement.append(make('span','',`${progress.drafts}/${progress.total_artifacts} bản nháp hiện có`));
      if(progress.answers_revised)progressElement.append(make('span','','Đã đổi nhánh trả lời · bản nháp cần cập nhật và rà soát lại.'));
      if(finished){
        const a=make('a','', 'Xem tài liệu để review');a.href=`/projects/${pid}/artifacts`;progressElement.append(a);
        if(suggestButton){suggestButton.textContent='Xem bản nháp';suggestButton.title='Mở danh sách bản nháp';}
      }else if(suggestButton){
        suggestButton.textContent='AI đề xuất';suggestButton.title='AI đề xuất phương án để chọn';
      }
    }catch(e){progressElement.textContent='Chưa tải được tiến độ: '+e.message;}
  }
  async function refreshPlanningQuestion(){
    if(!did)return;
    try{
      const data=await sw.api(`/api/projects/${pid}/planning/questions?discussion_id=${encodeURIComponent(did)}`,{},'GET');
      renderPlanningQuestion(data.pending);
      const threads=[...(data.history||[])];
      if(data.pending&&!threads.some(q=>q.id===data.pending.id))threads.push(data.pending);
      renderQuestionTimeline(threads);
      const recent=data.history?.[0];
      if(!data.pending&&!discoveryDone&&recent?.status==='answered'&&!recent.answer?.continuation_run_id){
        questionSlot.hidden=false;
        questionSlot.append(actionButton('Tiếp tục từ lựa chọn đã lưu','btn-outline-primary',()=>{
          talk({continueQuestionId:recent.id});
        }));
      }
    }catch(e){console.warn('Planning question unavailable:',e.message);}
  }
  // 'AI đề xuất' now uses the SAME SpecWeaveSession and event stream as chat,
  // never a stateless SpecWeave completion hidden behind a plain JSON API.
  suggestButton?.addEventListener('click',()=>{
    if(!did||controller)return;
    if(openQuestion){scrollToQuestion();return;}
    if(discoveryDone){location.href=`/projects/${pid}/artifacts`;return;}
    talk({advancePlanning:true});
  });
  document.querySelector('[data-plan-suggest]')?.addEventListener('click',()=>suggestButton?.click());
  send?.addEventListener('click',()=>talk());
  input?.addEventListener('keydown',e=>{
    if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();talk();}
  });
  input?.addEventListener('input',()=>{input.style.height='auto';input.style.height=Math.min(input.scrollHeight,210)+'px';});
  document.querySelectorAll('[data-suggest]').forEach(b=>b.addEventListener('click',()=>{input.value=b.dataset.suggest;input.focus();}));
  stop?.addEventListener('click',async()=>{
    // Server-side cancellation survives browser disconnects.
    if(!runId)return;
    stop.disabled=true;
    try{await sw.api(`/api/projects/${pid}/runs/${runId}/cancel`);}
    catch(e){sw.toast(e.message,'error');}
    finally{stop.disabled=false;}
  });
  document.getElementById('clearContextView')?.addEventListener('click',()=>followIfNearBottom(true));
  document.getElementById('newDiscussion')?.addEventListener('click',async()=>{
    const name=prompt('Tên phiên thảo luận:','Phiên thảo luận mới');if(!name)return;
    await sw.act(async()=>{const data=await sw.api(`/api/projects/${pid}/discussions`,{title:name,mode:'discovery'});location.href=data.redirect;},'');
  });
  async function reconnectActiveRun(){
    if(!did||controller)return;
    // Use the ID captured by the HTML even if it finished before JS loaded.
    if(root.dataset.activeRun){
      await talk({resumeRunId:root.dataset.activeRun});
      return;
    }
    setBusy(true,'Đang kiểm tra lượt chạy trên server…');
    try{
      const current=await sw.api(`/api/projects/${pid}/discussions/${did}/active-run`,{},'GET');
      if(current.run_id){
        await talk({resumeRunId:current.run_id});
        return;
      }
      setBusy(false);
      await refreshPlanningQuestion();
    }catch(e){
      console.warn('lượt chạy reconnect:',e.message);
      setBusy(false,'Chưa kiểm tra được trạng thái SpecWeave');
      sw.toast(e.message,'error');
    }
  }
  const savedThreads=document.getElementById('planningThreads');
  if(savedThreads)renderQuestionTimeline(JSON.parse(savedThreads.textContent));
  list.scrollTop=list.scrollHeight;
  reconnectActiveRun();refreshDiscoveryProgress();
})();
