/* Quality & BMAD Review: all labels rendered as text, never as untrusted HTML. */
(() => {
  'use strict';
  const root=document.getElementById('qualityApp');if(!root)return;
  const pid=root.dataset.project,editable=root.dataset.editable==='true',owner=root.dataset.owner==='true';
  const base=`/api/projects/${pid}/quality`;
  const el=id=>document.getElementById(id);
  const text=(tag,content,klass)=>{const x=document.createElement(tag);x.textContent=String(content??'');if(klass)x.className=klass;return x;};
  const action=async(fn)=>{try{const result=await fn();el('qualityError').classList.add('d-none');await refresh();return result;}
    catch(e){el('qualityError').textContent=e.message||String(e);el('qualityError').classList.remove('d-none');}};
  const request=(url,data={},method='POST')=>sw.api(url,data,method);
  const button=(label,handler,klass='btn btn-sm btn-outline-primary')=>{
    const b=text('button',label,klass);b.type='button';b.addEventListener('click',()=>action(handler));return b;
  };
  const empty=(parent,msg)=>parent.appendChild(text('p',msg,'text-secondary small my-2'));
  const item=(parent,title,details)=>{const box=document.createElement('article');box.className='q-item';box.appendChild(text('strong',title));
    if(details)box.appendChild(text('p',details,'text-secondary mb-2'));parent.appendChild(box);return box;};
  const actions=box=>{const a=document.createElement('div');a.className='d-flex gap-2 flex-wrap mt-2';box.appendChild(a);return a;};
  let activeReviewId=null;
  let activeJobId=null, jobsTimer=null;
  const jobLabels={queued:'Chờ worker',running:'Đang phân tích',succeeded:'Hoàn thành',failed:'Lỗi',cancelled:'Đã hủy',stale:'Spec đã thay đổi'};
  const showJobs=async()=>{
    const resp=await fetch(`${base}/jobs`,{credentials:'same-origin'});
    if(!resp.ok)throw new Error('Không tải được hàng đợi review');
    const data=await resp.json(),out=el('qJobs');out.replaceChildren();
    if(!data.jobs.length){empty(out,'Chưa có review chạy nền.');return;}
    if(!data.jobs.some(j=>j.id===activeJobId))activeJobId=data.jobs[0].id;
    let hasActive=false;
    data.jobs.slice(0,8).forEach(j=>{
      if(j.status==='queued'||j.status==='running')hasActive=true;
      const title=`${jobLabels[j.status]||j.status} · ${j.progress}/${j.total} vai trò`;
      const details=`${j.output_used}/${j.output_budget} output tokens${j.usage_estimated?' (ước tính)':''} · ${j.created_at||''}`;
      const box=item(out,title,details);
      const bar=document.createElement('progress');bar.className='q-job-progress';
      bar.max=j.total;bar.value=j.progress;bar.setAttribute('aria-label',`Tiến độ ${j.progress}/${j.total}`);box.appendChild(bar);
      const detailsList=document.createElement('div');detailsList.className='small text-secondary mt-1';
      detailsList.textContent=j.steps.map(r=>`${r.role}: ${r.status==='completed'?'✓':'○'} · model ${r.model_id?.slice(0,8)||'—'}`).join('  ·  ');box.appendChild(detailsList);
      if(j.error)box.appendChild(text('small',j.error,'text-danger d-block'));
      if(j.summary)box.appendChild(text('p',j.summary,'small mt-2'));
      const tools=actions(box);
      if(editable&&(j.status==='running'||j.status==='queued'))
        tools.appendChild(button(j.cancel_requested?'Đã yêu cầu hủy':'Hủy job',
          ()=>request(`${base}/jobs/${j.id}/cancel`), 'btn btn-sm btn-outline-danger'));
      if(editable&&j.status==='failed')
        tools.appendChild(button('Thử lại từ bước lỗi',()=>request(`${base}/jobs/${j.id}/retry`)));
      if(j.review_id)tools.appendChild(button('Xem findings',async()=>{
        activeReviewId=j.review_id;await refresh();window.scrollTo({top:el('qReviewHistory').getBoundingClientRect().top+window.scrollY-90,behavior:'smooth'});
      }));
    });
    if(!hasActive && jobsTimer){clearInterval(jobsTimer);jobsTimer=null;}
    if(hasActive && !jobsTimer)jobsTimer=setInterval(()=>{
      showJobs().then(()=>refresh()).catch(e=>{console.warn('Review polling unavailable:',e.message);});
    },3500);
  };

  const showReviews=data=>{
    const out=el('qReviewHistory');out.replaceChildren();
    if(!data.reviews.length)empty(out,'Chưa có phiên kiểm tra. Hãy chạy Rules Review hoặc AI Review.');
    if(!data.reviews.some(r=>r.id===activeReviewId))activeReviewId=data.reviews[0]?.id||null;
    data.reviews.slice(0,8).forEach(r=>{
      const box=item(out,(r.engine==='adversarial'?'Adversarial 3-role Review':r.engine==='model'?'AI Semantic Review':'Rules Review')+(r.stale?' · Cũ':''),r.summary||'Không có nhận xét tổng quát');
      box.appendChild(text('small',r.created_at,'text-secondary d-block'));
      if(activeReviewId===r.id)box.classList.add('q-selected');
      const a=actions(box);a.appendChild(button('Xem findings',async()=>{activeReviewId=r.id;showReviews(data);},'btn btn-sm btn-outline-secondary'));
    });
    const recent=data.reviews.find(r=>r.id===activeReviewId), findings=el('qFindings');findings.replaceChildren();
    if(!recent){empty(findings,'Chưa có finding.');return;}
    const grouped=data.findings.filter(f=>f.review_id===recent.id);
    const blockers=data.findings.filter(f=>f.status==='confirmed'&&f.severity==='high');
    const shown=[...blockers,...grouped.filter(f=>!blockers.some(b=>b.id===f.id))];
    if(blockers.length)findings.appendChild(text('p','Các vấn đề HIGH đã xác nhận vẫn chặn handoff kể cả sau khi spec đổi:','text-danger fw-semibold'));
    if(!shown.length)empty(findings,'Không phát hiện vấn đề trong lần review đang chọn.');
    const reviewMap=new Map(data.reviews.map(r=>[r.id,r]));
    shown.forEach(f=>{
      const isStale=reviewMap.has(f.review_id)?reviewMap.get(f.review_id).stale:true;
      const box=item(findings,`${f.severity.toUpperCase()} · ${f.category} · ${f.status}`,f.description);
      if(f.evidence)box.appendChild(text('small','Evidence: '+f.evidence,'text-secondary d-block'));
      if(f.recommendation)box.appendChild(text('small','Đề xuất: '+f.recommendation,'d-block mt-1'));
      if(owner&&((!isStale&&f.status==='proposed') || f.status==='confirmed')){
        const a=actions(box);
        (f.status==='confirmed'?['resolved','dismissed']:['confirmed','resolved','dismissed']).forEach(status=>a.appendChild(button(
          ({confirmed:'Xác nhận',resolved:'Đã khắc phục',dismissed:'Bỏ qua'})[status],
          ()=>request(`${base}/findings/${f.id}/status`,{status}))));
      }
      if(isStale)box.appendChild(text('small',f.status==='confirmed'?'Finding đã xác nhận vẫn chặn handoff cho tới khi được xử lý.':'Review cũ; cần chạy lại trước khi xác nhận.','text-warning'));
    });
  };
  const showEvidence=data=>{
    const out=el('qEvidence');out.replaceChildren();
    if(!data.evidence.length)empty(out,'Chưa có nguồn. Tìm kiếm và lưu tại Research Explorer.');
    data.evidence.forEach(e=>{
      const box=item(out,e.title||e.url,`${e.score.score}/100 · ${e.score.verification} · ${e.score.source_kind}`);
      const a=document.createElement('a');a.href=e.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent='Mở nguồn';box.appendChild(a);
      box.appendChild(text('small','Tín hiệu: '+e.score.signals.join(' · '),'text-secondary d-block'));
      if(editable){const form=document.createElement('form');form.className='q-evidence-form mt-2';
        const kind=document.createElement('select');kind.className='form-select form-select-sm';
        ['unknown','official_docs','primary','research','community','internal'].forEach(k=>{
          const op=text('option',k);op.value=k;kind.appendChild(op);});kind.value=e.score.source_kind;
        const verification=document.createElement('select');verification.className='form-select form-select-sm';
        ['unverified','checked','disputed'].forEach(v=>{const op=text('option',v);op.value=v;verification.appendChild(op);});
        verification.value=e.score.verification;verification.disabled=!owner;
        const published=document.createElement('input');published.type='date';published.className='form-control form-control-sm';
        published.value=e.score.published_on||'';
        const note=document.createElement('input');note.className='form-control form-control-sm';note.placeholder='Nguồn gốc / ghi chú kiểm chứng';note.value=e.note||'';
        const save=text('button','Lưu đánh giá','btn btn-sm btn-outline-primary');save.type='submit';
        form.append(kind,verification,published,note,save);
        form.addEventListener('submit',ev=>{ev.preventDefault();action(()=>request(`${base}/evidence/${e.id}`,
          {source_kind:kind.value,...(owner?{verification:verification.value}:{}),published_on:published.value||null,note:note.value}));});
        box.appendChild(form);
      }
    });
  };
  const showTrace=data=>{
    const out=el('qTrace');out.replaceChildren();const entries=data.suggestions.filter(x=>x.status==='pending');
    if(!entries.length)empty(out,'Không có ứng viên chưa xử lý.');
    entries.slice(0,60).forEach(s=>{
      const box=item(out,`${s.score}% · ${s.from_label} → ${s.to_label}`,s.reason);
      box.appendChild(text('small',`${s.source_type} → ${s.target_type}`,'text-secondary'));
      if(editable){const a=actions(box);
        a.appendChild(button('Chấp nhận',()=>request(`${base}/trace/${s.id}/decision`,{action:'accept'}),'btn btn-sm btn-primary'));
        a.appendChild(button('Từ chối',()=>request(`${base}/trace/${s.id}/decision`,{action:'reject'}),'btn btn-sm btn-outline-secondary'));
      }
    });
  };
  const showWorkflow=data=>{
    const out=el('qWorkflow');out.replaceChildren();
    data.workflow.forEach((s,i)=>{
      const box=item(out,`${i+1}. ${s.label} · ${s.role.toUpperCase()}`,`Trạng thái: ${s.status}${s.stale?' · CẦN REVIEW LẠI':''}`);
      const ul=document.createElement('ul');ul.className='q-checklist';
      s.checklist.forEach(c=>{const li=text('li',(c.passed?'✓ ':'○ ')+c.label,c.passed?'text-success':'text-secondary');ul.appendChild(li);});
      box.appendChild(ul);
      if(editable){const a=actions(box);
        if(s.status==='not_started'||s.status==='draft'||s.status==='needs_changes'||s.stale){
          a.appendChild(button('Gửi review',()=>request(`${base}/workflow/${s.stage}`,{action:'submit'})));
        }
        if(owner&&s.status==='in_review'&&!s.stale){
          a.appendChild(button('Duyệt',()=>request(`${base}/workflow/${s.stage}`,{action:'approve'}),'btn btn-sm btn-primary'));
          a.appendChild(button('Yêu cầu sửa',async()=>{const notes=prompt('Lý do yêu cầu sửa?');if(!notes)return;
            return request(`${base}/workflow/${s.stage}`,{action:'request_changes',notes});}));
        }
      }
    });
  };
  const refresh=async()=>{
    const r=await fetch(`${base}/overview`,{credentials:'same-origin'});if(!r.ok)throw Error('Không tải được Quality Review');
    const data=await r.json();
    el('qCountReviews').textContent=data.reviews.length;
    el('qCountFindings').textContent=data.findings.filter(f=>f.status==='proposed'||f.status==='confirmed').length;
    el('qCountTrace').textContent=data.suggestions.filter(s=>s.status==='pending').length;
    el('qCountBlockers').textContent=data.quality_gate;
    showReviews(data);showEvidence(data);showTrace(data);showWorkflow(data);
  };
  el('qEnqueueReview')?.addEventListener('click',()=>{
    if(!confirm('Review chạy nền gọi model 4 lần và có phát sinh token. Tiếp tục?'))return;
    action(async()=>{
      const budget=Number(el('qOutputBudget')?.value||12000);
      const role_models={};
      document.querySelectorAll('.q-role-model').forEach(select=>{if(select.value)role_models[select.dataset.role]=select.value;});
      const job=await request(`${base}/jobs`,{engine:'adversarial',output_budget:budget,role_models});
      activeJobId=job.id;await showJobs();
    });
  });
  el('qRefreshJobs')?.addEventListener('click',()=>action(showJobs));
  el('qRunRules')?.addEventListener('click',()=>action(async()=>{const x=await request(`${base}/reviews/run`,{engine:'rules'});activeReviewId=x.id;}));
  el('qRunAI')?.addEventListener('click',()=>{
    if(!confirm('AI sẽ nhận thông tin spec trong project và gọi model đang chọn. Tiếp tục?'))return;
    action(async()=>{const x=await request(`${base}/reviews/run`,{engine:'model'});activeReviewId=x.id;});
  });
  el('qRunAdversarial')?.addEventListener('click',()=>{
    if(!confirm('Chạy 3 lượt AI review (Analyst, Architect, QA), có phát sinh token. Tiếp tục?'))return;
    action(async()=>{const x=await request(`${base}/reviews/run`,{engine:'adversarial'});activeReviewId=x.id;});
  });
  el('qGenerateTrace')?.addEventListener('click',()=>action(()=>request(`${base}/trace/suggest`,{limit:35})));
  showJobs().catch(e=>{console.warn('Cannot load review jobs:',e.message);});
  refresh().catch(e=>{el('qualityError').textContent=e.message;el('qualityError').classList.remove('d-none');});
})();
