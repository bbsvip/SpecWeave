(() => {
  const root=document.getElementById('discussionApp');if(!root)return;
  const pid=root.dataset.project,did=root.dataset.discussion,list=document.getElementById('messageList');
  const input=document.getElementById('chatInput'),send=document.getElementById('sendChat'),stop=document.getElementById('stopChat');
  const state=document.getElementById('agentState'),activity=document.getElementById('toolActivity');
  let controller=null,runId=null;
  function renderMarkdown(node,content){
    if(window.marked && window.DOMPurify){node.innerHTML=DOMPurify.sanitize(marked.parse(content,{breaks:true}));
      node.querySelectorAll('a').forEach(a=>{a.target='_blank';a.rel='noopener noreferrer'});
    }else node.textContent=content;
  }
  list.querySelectorAll('.markdown-content').forEach(node=>renderMarkdown(node,node.textContent));
  function addMessage(who,text=''){
    list.querySelector('.conversation-intro')?.remove();
    const article=document.createElement('article');article.className='message '+(who==='user'?'message-user':'message-agent');
    const avatar=document.createElement('div');avatar.className='message-avatar';
    avatar.textContent=who==='user'?'U':'✦';
    const body=document.createElement('div');body.className='message-body';
    const meta=document.createElement('div');meta.className='message-meta';meta.textContent=who==='user'?'Anh':'SpecWeave AI';
    const content=document.createElement('div');content.className='message-content'+(who==='assistant'?' markdown-content':'');content.textContent=text;
    body.append(meta,content);article.append(avatar,body);list.appendChild(article);list.scrollTop=list.scrollHeight;
    return content;
  }
  const questionSlot=document.getElementById('planningQuestion');
  const suggestButton=document.getElementById('suggestPlanningQuestion');
  let openQuestion=null, selectedIds=new Set();
  const make=(tag, cls, txt)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(txt!==undefined)e.textContent=txt;return e;};
  const actionButton=(caption, style, callback)=>{
    const b=make('button','btn btn-sm '+style,caption);b.type='button';b.addEventListener('click',callback);return b;
  };
  function renderPlanningQuestion(q){
    questionSlot.replaceChildren();questionSlot.hidden=!q;openQuestion=q;selectedIds=new Set();
    if(!q)return;
    const panel=make('div','planning-brief');questionSlot.append(panel);
    panel.append(make('div','planning-header','✦  GỢI Ý QUYẾT ĐỊNH · CHƯA PHÊ DUYỆT'));
    panel.append(make('h3','',q.question));
    if(q.context)panel.append(make('p','planning-description',q.context));
    const optHolder=make('div','planning-options');panel.append(optHolder);
    const optionNodes=[];
    for(const opt of q.options){
      const row=make('button','planning-option');row.type='button';row.setAttribute('aria-pressed','false');
      row.append(make('span','planning-check',q.allow_multiple?'□':'○'));
      const body=make('div');
      const title=make('div','planning-option-title');title.append(make('span','',opt.label));
      if(opt.id===q.recommended_id)title.append(make('span','planning-recommended','Đề xuất'));
      body.append(title);
      if(opt.description)body.append(make('div','planning-option-copy',opt.description));
      const trade=make('div','planning-tradeoffs');
      if(opt.pros)trade.append(make('div','up','+ '+opt.pros));
      if(opt.cons)trade.append(make('div','down','− '+opt.cons));
      body.append(trade);row.append(body);optHolder.append(row);
      row.addEventListener('click',()=>{
        if(!q.allow_multiple)selectedIds.clear();
        if(row.classList.contains('selected')&&q.allow_multiple)selectedIds.delete(opt.id);
        else selectedIds.add(opt.id);
        for(const v of optionNodes){const on=selectedIds.has(v.opt.id);v.node.classList.toggle('selected',on);v.node.setAttribute('aria-pressed',String(on));v.node.querySelector('.planning-check').textContent=on?'✓':q.allow_multiple?'□':'○'}
      });
      optionNodes.push({node:row,opt});
    }
    if(q.recommendation_reason){
      const why=make('div','planning-reason');why.append(make('strong','','Vì sao AI đề xuất: '),document.createTextNode(q.recommendation_reason));panel.append(why);
    }
    if(q.impact)panel.append(make('div','planning-extras','Tác động cần cân nhắc: '+q.impact));
    if(q.assumptions?.length)panel.append(make('div','planning-extras','Giả định chưa xác minh: '+q.assumptions.join('; ')));
    if(q.sources?.length){
      const sources=make('div','planning-extras');sources.append(make('strong','','Nguồn nghiên cứu của dự án: '));
      for(const src of q.sources){
        if(!/^https?:\/\//i.test(src.url))continue;
        const a=make('a','planning-source',src.title||src.url);a.href=src.url;a.target='_blank';a.rel='noopener noreferrer';
        sources.append(a);
      }
      panel.append(sources);
    }
    const custom=make('textarea','planning-custom');custom.placeholder='Câu trả lời riêng, hoặc điều chỉnh phương án đã chọn (không bắt buộc)...';custom.setAttribute('aria-label','Điều chỉnh câu trả lời');panel.append(custom);
    const notes=make('input','planning-custom');notes.style.minHeight='38px';notes.placeholder='Ghi chú bổ sung về quyết định (tùy chọn)';notes.setAttribute('aria-label','Ghi chú');panel.append(notes);
    const actions=make('div','planning-actions');panel.append(actions);
    async function submit(action){
      panel.classList.add('planning-loading');
      try{
        const res=await sw.api(`/api/projects/${pid}/planning/questions/${q.id}/answer`,{
          action,selected_ids:[...selectedIds],custom_answer:custom.value,note:notes.value
        });
        if(action==='answer'){
          const labels=q.options.filter(o=>selectedIds.has(o.id)).map(o=>o.label);
          addMessage('user','Tôi chọn: '+(labels.join(', ')||custom.value)+(labels.length&&custom.value?'\nĐiều chỉnh: '+custom.value:'')+(notes.value?'\nGhi chú: '+notes.value:''));
          sw.toast('Đã lưu câu trả lời. Có thể yêu cầu AI tiếp tục phân tích.','success');
        }else sw.toast('Đã hoãn câu hỏi này.','info');
        renderPlanningQuestion(null);
      }catch(e){sw.toast(e.message,'error');panel.classList.remove('planning-loading');}
    }
    actions.append(actionButton('Xác nhận câu trả lời','btn-primary',()=>submit('answer')));
    actions.append(actionButton('Thảo luận thêm','btn-outline-primary',()=>{
      input.value=`Hãy phân tích sâu quyết định: ${q.question}\nLý do và rủi ro của phương án ${q.options.find(o=>o.id===q.recommended_id)?.label||'đề xuất'} là gì? Tôi chưa chốt phương án.`;
      input.focus();
    }));
    actions.append(actionButton('Tạm hoãn','btn-outline-secondary',()=>submit('defer')));
  }
  async function refreshPlanningQuestion(){
    if(!did)return;
    try{
      const data=await sw.api(`/api/projects/${pid}/planning/questions?discussion_id=${encodeURIComponent(did)}`,{},'GET');
      renderPlanningQuestion(data.pending);
    }catch(e){console.warn('Planning question unavailable:',e.message)}
  }
  suggestButton?.addEventListener('click',async()=>{
    if(!did||controller)return;
    suggestButton.disabled=true;suggestButton.textContent='AI đang đề xuất...';
    try{
      const data=await sw.api(`/api/projects/${pid}/planning/questions/suggest`,{discussion_id:did});
      renderPlanningQuestion(data.question);
      if(data.created)sw.toast('Đã tạo câu hỏi với các phương án đề xuất.','success');
    }catch(e){sw.toast(e.message,'error')}
    finally{suggestButton.disabled=false;suggestButton.textContent='AI đề xuất'}
  });
  const busy=(isBusy)=>{send.disabled=isBusy;input.disabled=isBusy;stop.classList.toggle('d-none',!isBusy);state.textContent=isBusy?'Thinking':'Ready'};
  async function talk(){
    const message=input.value.trim();if(!message||controller||!did)return;
    input.value='';input.style.height='auto';addMessage('user',message);const responseNode=addMessage('assistant','');
    const role=sw.val('agentRole'),mode=sw.val('agentMode');let full='';runId=null;
    controller=new AbortController();busy(true);
    try{
      const response=await fetch(`/api/projects/${pid}/chat`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':sw.csrf},
        body:JSON.stringify({discussion_id:did,message,role,mode}),signal:controller.signal});
      if(!response.ok){const payload=await response.json().catch(()=>({}));throw new Error(payload.message||`HTTP ${response.status}`)}
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',finished=false;
      while(!finished){
        const {done,value}=await reader.read();if(done)break;
        buffer+=decoder.decode(value,{stream:true}).replaceAll('\r\n','\n');
        let boundary;while((boundary=buffer.indexOf('\n\n'))>=0){
          const packet=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
          for(const line of packet.split('\n'))if(line.startsWith('data: ')){
            let ev;try{ev=JSON.parse(line.slice(6))}catch{continue}
            if(ev.run_id)runId=ev.run_id;
            if(ev.type==='delta'){full+=ev.payload?.text||'';responseNode.textContent=full;list.scrollTop=list.scrollHeight}
            if(ev.type==='status')state.textContent=ev.payload?.message||'Running';
            if(ev.type==='tool'){
              if(activity.textContent==='Chưa có tool call')activity.replaceChildren();
              const line=document.createElement('div');line.className='tool-item';line.textContent=`${ev.payload?.name||'tool'}: ${ev.payload?.status||'running'}`;
              activity.prepend(line);
            }
            if(ev.type==='final' && typeof ev.payload?.text==='string'){full=ev.payload.text;responseNode.textContent=full;}
            if(ev.type==='error')throw new Error(ev.message||'Agent error');
            if(ev.type==='done'){finished=true;break}
          }
          if(finished)break;
        }
      }
      renderMarkdown(responseNode,full||'(Không có nội dung)');
      await refreshPlanningQuestion();
    }catch(e){
      if(e.name==='AbortError'){responseNode.textContent=full+'\n\n[Đã dừng]' }
      else{responseNode.textContent=full+'\n\n[Lỗi: '+e.message+']';sw.toast(e.message,'error')}
    }finally{controller=null;busy(false);list.scrollTop=list.scrollHeight;input.focus();}
  }
  document.querySelector('[data-plan-suggest]')?.addEventListener('click',()=>suggestButton?.click());
  send.addEventListener('click',talk);
  input.addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();talk()}});
  document.querySelectorAll('[data-suggest]').forEach(button=>button.addEventListener('click',()=>{input.value=button.dataset.suggest;input.focus()}));
  stop.addEventListener('click',async()=>{
    if(runId)try{await sw.api(`/api/projects/${pid}/runs/${runId}/cancel`)}catch{}
    controller?.abort();
  });
  document.getElementById('clearContextView').addEventListener('click',()=>list.scrollTop=list.scrollHeight);
  document.getElementById('newDiscussion')?.addEventListener('click',async()=>{
    const name=prompt('Tên phiên thảo luận:','Phiên thảo luận mới');if(!name)return;
    await sw.act(async()=>{const data=await sw.api(`/api/projects/${pid}/discussions`,{title:name,mode:'discovery'});location.href=data.redirect},'');
  });
  list.scrollTop=list.scrollHeight;
  refreshPlanningQuestion();
})();
