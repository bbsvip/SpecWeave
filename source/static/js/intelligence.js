/* Planning Intelligence: project-local links and owner-controlled review. */
(() => {
  const root=document.getElementById('intelligenceApp');
  if(!root) return;
  const pid=root.dataset.project, owner=root.dataset.owner==='true', editable=root.dataset.editable==='true';
  const base=`/api/projects/${pid}/intelligence`;
  const api=(url,data={},method='POST')=>sw.api(url,data,method);
  const action=(fn,good='Đã lưu')=>sw.act(fn,good);
  const val=id=>document.getElementById(id)?.value||'';
  const allowed=new Set([
    'goal:requirement','goal:phase','requirement:decision','requirement:document',
    'requirement:phase','decision:document','decision:phase','phase:document',
    'research:requirement','research:decision'
  ]);
  let nodes=[],links=[],activePhase=null;
  const safe=(node,text)=>{node.textContent=text;return node;};
  const select=(id)=>document.getElementById(id);

  document.getElementById('piRefresh')?.addEventListener('click',()=>location.reload());
  let activeGoal=null;
  const resetGoal=()=>{
    activeGoal=null;document.getElementById('piGoalForm')?.reset();
    if(select('piGoalSubmit'))select('piGoalSubmit').textContent='Thêm mục tiêu';
    select('piGoalCancel')?.classList.add('d-none');
  };
  select('piGoalCancel')?.addEventListener('click',resetGoal);
  document.getElementById('piGoalForm')?.addEventListener('submit',e=>{
    e.preventDefault();action(async()=>{
      const payload={title:val('piGoalTitle'),outcome:val('piGoalOutcome'),metric:val('piGoalMetric')};
      await api(activeGoal?`${base}/goals/${activeGoal}`:`${base}/goals`,payload);
      location.reload();
    });
  });
  document.querySelectorAll('.pi-goal-edit').forEach(btn=>btn.addEventListener('click',()=>action(async()=>{
    const r=await fetch(`${base}/goals/${btn.dataset.id}`);
    if(!r.ok)throw Error('Không đọc được mục tiêu');
    const g=await r.json();activeGoal=g.id;
    select('piGoalTitle').value=g.title;select('piGoalOutcome').value=g.outcome;
    select('piGoalMetric').value=g.metric;select('piGoalSubmit').textContent='Lưu mục tiêu · cần duyệt lại';
    select('piGoalCancel').classList.remove('d-none');
    select('piGoalTitle').scrollIntoView({behavior:'smooth',block:'start'});
  },'')));
  document.querySelectorAll('.pi-goal-review').forEach(btn=>btn.addEventListener('click',()=>{
    if(!owner || !sw.confirm('Xác nhận duyệt mục tiêu này?'))return;
    action(async()=>{await api(`${base}/goals/${btn.dataset.id}/status`,{status:'approved'});location.reload();});
  }));

  const phaseForm=document.getElementById('piPhaseForm');
  const resetPhase=()=>{
    activePhase=null;phaseForm.reset();safe(select('piPhaseSubmit'), 'Tạo phase');
    select('piPhaseCancel').classList.add('d-none');
  };
  select('piPhaseCancel')?.addEventListener('click',resetPhase);
  phaseForm?.addEventListener('submit',e=>{
    e.preventDefault(); action(async()=>{
      const data={title:val('piPhaseTitle'),summary:val('piPhaseSummary'),
                  deliverables:val('piPhaseDeliverables'),acceptance:val('piPhaseAcceptance')};
      if(activePhase){
        data.revision=activePhase.revision;
        await api(`${base}/phases/${activePhase.id}`,data);
      }else await api(`${base}/phases`,data);
      location.reload();
    });
  });
  document.querySelectorAll('.pi-phase-edit').forEach(btn=>btn.addEventListener('click',()=>action(async()=>{
    const response=await fetch(`${base}/phases/${encodeURIComponent(btn.dataset.id)}`,{credentials:'same-origin'});
    if(!response.ok) throw Error('Không đọc được phase');
    activePhase=await response.json();
    for(const [key,element] of [['title','piPhaseTitle'],['summary','piPhaseSummary'],
      ['deliverables','piPhaseDeliverables'],['acceptance','piPhaseAcceptance']]){
      select(element).value=activePhase[key]||'';
    }
    safe(select('piPhaseSubmit'),'Lưu thay đổi · rev '+activePhase.revision);
    select('piPhaseCancel').classList.remove('d-none');
    phaseForm.scrollIntoView({behavior:'smooth',block:'start'});
  },'')));
  document.querySelectorAll('.pi-phase-review').forEach(btn=>btn.addEventListener('click',()=>{
    const reviewAction=btn.dataset.action;
    const comment=reviewAction==='request_changes'?prompt('Nêu lý do cần chỉnh sửa:'):(reviewAction==='approve'?prompt('Ghi chú phê duyệt (không bắt buộc):',''): '');
    if(comment===null || (reviewAction==='request_changes'&&!comment.trim()))return;
    if(reviewAction==='approve'&&!sw.confirm('Xác nhận phê duyệt phase ở revision hiện tại?'))return;
    action(async()=>{
      await api(`${base}/phases/${btn.dataset.id}/review`,{action:reviewAction,revision:Number(btn.dataset.revision),comment});
      location.reload();
    },'Đã cập nhật review');
  }));

  document.getElementById('piDependencyForm')?.addEventListener('submit',e=>{
    e.preventDefault();action(async()=>{
      await api(`${base}/phases/${val('piDepPhase')}/dependencies`,{prerequisite_id:val('piDepPrerequisite')});
      location.reload();
    });
  });
  document.querySelectorAll('.pi-dep-remove').forEach(btn=>btn.addEventListener('click',()=>{
    if(!sw.confirm('Xóa quan hệ phụ thuộc này? Phase liên quan sẽ phải được duyệt lại.'))return;
    action(async()=>{await api(`${base}/phases/${btn.dataset.phase}/dependencies/${btn.dataset.id}`,{},'DELETE');location.reload();});
  }));

  const src=select('piTraceSource'),dst=select('piTraceTarget'),traceList=select('piTraceList');
  const nodeText=n=>`[${n.type}] ${n.label}`;
  const fill=(element,entries)=>{
    if(!element)return;
    const previous=element.value;
    element.replaceChildren();
    for(const n of entries){
      const o=document.createElement('option');o.value=n.id;o.textContent=nodeText(n);element.appendChild(o);
    }
    if(entries.some(n=>n.id===previous))element.value=previous;
  };
  const selectedNode=(element)=>element?nodes.find(n=>n.id===element.value):null;
  const updateTargets=()=>{
    const from=selectedNode(src);
    fill(dst,from?nodes.filter(n=>allowed.has(`${from.type}:${n.type}`)):[]);
  };
  src?.addEventListener('change',updateTargets);
  const svgNS='http://www.w3.org/2000/svg';
  const makeSvg=(tag,props={})=>{
    const el=document.createElementNS(svgNS,tag);
    for(const [k,v] of Object.entries(props))el.setAttribute(k,String(v));
    return el;
  };
  const renderGraph=()=>{
    const wrap=select('piGraphWrap');wrap.replaceChildren();
    const types=['research','goal','requirement','decision','phase','document'];
    const columns=types.map(type=>({type,items:nodes.filter(n=>n.type===type).slice(0,14)}));
    const shown=columns.flatMap(c=>c.items);
    if(!shown.length){const p=document.createElement('p');p.className='pi-empty';p.textContent='Chưa có đối tượng để hiển thị sơ đồ.';wrap.appendChild(p);return;}
    const omitted=nodes.length-shown.length;
    if(omitted>0){const msg=document.createElement('p');msg.className='pi-graph-overflow';msg.textContent=`Sơ đồ đang hiển thị ${shown.length}/${nodes.length} đối tượng để giữ khả năng đọc; danh sách liên kết bên dưới vẫn đầy đủ.`;wrap.appendChild(msg);}
    const width=1360,height=Math.max(270,75+Math.max(...columns.map(c=>c.items.length))*75);
    const svg=makeSvg('svg',{viewBox:`0 0 ${width} ${height}`,width,height,role:'img',
      'aria-label':'Sơ đồ truy vết dự án: research, goals, requirements, decisions, phases, documents'});
    const defs=makeSvg('defs'),marker=makeSvg('marker',{id:'piGraphArrow',markerWidth:8,markerHeight:7,
      refX:7,refY:3.5,orient:'auto'});
    const markPath=makeSvg('path',{d:'M0,0 L7,3.5 L0,7 Z',fill:'#a2a9c1'});
    marker.appendChild(markPath);defs.appendChild(marker);svg.appendChild(defs);
    const positions=new Map();
    columns.forEach((column,columnIndex)=>{
      const x=22+columnIndex*225;
      const heading=makeSvg('text',{x:x+5,y:26,class:'pi-svg-heading'});
      heading.textContent=column.type.toUpperCase()+' ('+nodes.filter(n=>n.type===column.type).length+')';
      svg.appendChild(heading);
      column.items.forEach((n,index)=>{
        const y=43+index*75;positions.set(n.id,{x,y});
      });
    });
    for(const link of links){
      const a=positions.get(link.source),b=positions.get(link.target);if(!a||!b)continue;
      const sx=a.x+196,sy=a.y+24,tx=b.x,ty=b.y+24;
      const sameOrBack=tx<=sx;
      const d=sameOrBack?`M${sx},${sy} C${sx+35},${sy-40} ${tx-35},${ty-40} ${tx},${ty}`:
        `M${sx},${sy} C${sx+Math.max(20,(tx-sx)/2)},${sy} ${tx-Math.max(20,(tx-sx)/2)},${ty} ${tx},${ty}`;
      const path=makeSvg('path',{d,class:link.relation==='precedes'?'pi-svg-link prerequisite':'pi-svg-link',
        'marker-end':'url(#piGraphArrow)'});
      const tooltip=makeSvg('title');tooltip.textContent=link.relation+(link.note?': '+link.note:'');
      path.appendChild(tooltip);svg.appendChild(path);
    }
    for(const node of shown){
      const {x,y}=positions.get(node.id);
      const rect=makeSvg('rect',{x,y,width:196,height:49,rx:9,class:'pi-svg-node'});
      svg.appendChild(rect);
      const label=makeSvg('text',{x:x+10,y:y+20,class:'pi-svg-label'});
      label.textContent=node.label.length>27?node.label.slice(0,26)+'…':node.label;
      svg.appendChild(label);
      const status=makeSvg('text',{x:x+10,y:y+38,class:'pi-svg-sub'});
      status.textContent=node.status||node.type;
      svg.appendChild(status);
      const title=makeSvg('title');title.textContent=nodeText(node)+' · '+node.status;
      rect.appendChild(title);
    }
    wrap.appendChild(svg);
  };
  const renderLinks=()=>{
    traceList.replaceChildren();
    const byId=new Map(nodes.map(n=>[n.id,n]));
    if(!links.length){const x=document.createElement('p');x.className='pi-empty';x.textContent='Chưa có trace link nào. Bắt đầu nối mục tiêu → requirement → phase và tài liệu.';traceList.appendChild(x);}
    for(const link of links){
      if(link.relation==='precedes')continue;
      const item=document.createElement('div');item.className='pi-trace-row';
      const label=document.createElement('div');
      const a=byId.get(link.source),b=byId.get(link.target);
      label.textContent=`${a?nodeText(a):link.source}  →  ${b?nodeText(b):link.target} · ${link.relation}`;
      item.appendChild(label);
      if(editable){
        const button=document.createElement('button');button.type='button';button.className='btn btn-sm btn-outline-danger';button.title='Xóa link';
        button.textContent='Xóa';button.addEventListener('click',()=>{
          if(!sw.confirm('Xóa trace link?'))return;
          action(async()=>{await api(`${base}/links/${link.id}`,{},'DELETE');await loadGraph();},'Đã xóa');
        });item.appendChild(button);
      }
      traceList.appendChild(item);
    }
  };
  const loadGraph=async()=>{
    const result=await fetch(`${base}/graph`,{credentials:'same-origin'});
    if(!result.ok)throw Error('Không đọc được spec graph');
    const data=await result.json();nodes=data.nodes;links=data.edges;
    fill(src,nodes.filter(n=>[...allowed].some(pair=>pair.startsWith(n.type+':'))));
    updateTargets();renderGraph();renderLinks();
  };
  document.getElementById('piTraceForm')?.addEventListener('submit',e=>{
    e.preventDefault();action(async()=>{
      const a=selectedNode(src),b=selectedNode(dst);
      if(!a||!b)throw Error('Chọn hai đối tượng cần liên kết');
      await api(`${base}/links`,{source_type:a.type,source_id:a.id,target_type:b.type,target_id:b.id,
        relation:val('piTraceRelation'),note:val('piTraceNote')});
      await loadGraph();
      sw.toast('Đã tạo link. Nếu phase đang approved, trạng thái sẽ chuyển về draft.','success');
    },'');
  });
  loadGraph().catch(e=>sw.toast(e.message,'error'));
})();
