/* SpecWeave shared browser behaviors. No framework build required. */
(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  window.sw = {
    csrf,
    async api(path, data = {}, method = 'POST') {
      const response = await fetch(path, {method, headers: {'Content-Type':'application/json','X-CSRF-Token':csrf}, credentials:'same-origin', body: method === 'GET' ? undefined : JSON.stringify(data)});
      let result; try { result = await response.json(); } catch { result = {}; }
      if (!response.ok) throw new Error(result.message || result.detail || `HTTP ${response.status}`);
      return result;
    },
    toast(message, type='info') {
      const root = document.getElementById('toasts'); if(!root) return;
      const el = document.createElement('div'); el.className = 'sw-toast ' + type; el.textContent=message;
      root.appendChild(el); setTimeout(() => el.remove(), 4700);
    },
    async act(fn, good='Đã lưu thành công') {
      try { await fn(); if (good) this.toast(good,'success'); }
      catch (e) { this.toast(e.message || String(e),'error'); }
    },
    confirm(message) { return window.confirm(message); },
    val(id) {return document.getElementById(id)?.value || '';},
  };
  const preference=localStorage.getItem('sw-theme-preference') || localStorage.getItem('sw-theme') || 'light';
  const darkQuery=window.matchMedia('(prefers-color-scheme: dark)');
  const activeTheme=()=>preference==='system'?(darkQuery.matches?'dark':'light'):preference;
  document.documentElement.dataset.bsTheme=activeTheme();
  document.documentElement.dataset.density=localStorage.getItem('sw-density')||'comfortable';
  if(preference==='system') darkQuery.addEventListener('change',()=>document.documentElement.dataset.bsTheme=darkQuery.matches?'dark':'light');
  document.getElementById('themeToggle')?.addEventListener('click',()=>{
    const next=document.documentElement.dataset.bsTheme==='dark'?'light':'dark';
    document.documentElement.dataset.bsTheme=next;localStorage.setItem('sw-theme-preference',next);
  });
  const menu=()=>document.getElementById('appShell')?.classList.toggle('menu-open');
  document.getElementById('mobileMenu')?.addEventListener('click',menu);
  document.getElementById('mobileOverlay')?.addEventListener('click',menu);
  const projectFilter=document.getElementById('projectFilter');
  projectFilter?.addEventListener('input',()=>{
    document.querySelectorAll('#projectGrid .project-tile').forEach(x=>x.hidden=!x.dataset.name.includes(projectFilter.value.toLowerCase()));
  });
  document.querySelectorAll('[data-show-form]').forEach(el=>el.addEventListener('click',()=>{
    document.getElementById(el.dataset.showForm)?.classList.toggle('d-none');
  }));
  const prSettings=document.getElementById('projectSettings');
  if (prSettings) {
    const id=prSettings.dataset.project, path=`/api/projects/${id}/settings`;
    document.getElementById('saveProjectGeneral')?.addEventListener('click',()=>sw.act(()=>sw.api(path,{name:sw.val('projectName'),description:sw.val('projectDescription'),stage:sw.val('projectStage'),ui_required:sw.val('projectUiRequired')==='auto'?null:sw.val('projectUiRequired')==='true',quality_review_required:sw.val('projectReviewRequired')==='true'})));
    document.getElementById('saveProjectAI')?.addEventListener('click',()=>sw.act(()=>sw.api(path,{active_model_id:sw.val('projectModel'),thinking_level:sw.val('projectThinking'),agent_backend:sw.val('projectBackend')})));
    document.getElementById('addMember')?.addEventListener('click',()=>sw.act(async()=>{await sw.api(`/api/projects/${id}/members`,{username:sw.val('memberUsername'),role:sw.val('memberRole')});location.reload();}));
    document.getElementById('archiveProject')?.addEventListener('click',()=>{
      if(sw.confirm('Đổi trạng thái archive cho dự án này?')) sw.act(async()=>{await sw.api(`/api/projects/${id}/archive`);location.reload();});
    });
    document.getElementById('deleteProject')?.addEventListener('click',()=>{
      if(sw.confirm('Xóa mềm dự án? Folder được giữ để operator có thể phục hồi.')) sw.act(async()=>{await sw.api(`/api/projects/${id}/delete`);location.href='/projects';});
    });
  }
  const decForm=document.getElementById('decisionForm');
  if(decForm){
    const id=decForm.dataset.project;
    document.getElementById('submitDecision')?.addEventListener('click',()=>sw.act(async()=>{
      await sw.api(`/api/projects/${id}/decisions`,{title:sw.val('decisionTitle'),description:sw.val('decisionDescription'),rationale:sw.val('decisionRationale')});location.reload();
    }));
  }
  document.querySelectorAll('.decision-status').forEach(el=>el.addEventListener('click',()=>sw.act(async()=>{
    const pid=location.pathname.split('/')[2];await sw.api(`/api/projects/${pid}/decisions/${el.dataset.id}/status`,{status:el.dataset.status});location.reload();
  })));
  const reqForm=document.getElementById('requirementForm');
  if(reqForm){
    const pid=reqForm.dataset.project;
    let editingId=null;
    document.getElementById('submitRequirement')?.addEventListener('click',()=>sw.act(async()=>{
      await sw.api(`/api/projects/${pid}/requirements${editingId?'/'+editingId:''}`,{
        title:sw.val('requirementTitle'),description:sw.val('requirementDescription'),
        criteria:sw.val('requirementCriteria'),priority:sw.val('requirementPriority')});location.reload();
    }));
    document.querySelectorAll('.req-edit').forEach(el=>el.addEventListener('click',()=>sw.act(async()=>{
      const response=await fetch(`/api/projects/${pid}/requirements/${el.dataset.id}`);
      if(!response.ok)throw Error('Không thể đọc requirement');
      const item=await response.json();editingId=item.id;
      for(const [field,value] of [['requirementTitle',item.title],['requirementDescription',item.description],
          ['requirementCriteria',item.criteria],['requirementPriority',item.priority]]){
        document.getElementById(field).value=value||'';
      }
      reqForm.classList.remove('d-none');document.getElementById('cancelRequirement').classList.remove('d-none');
      document.getElementById('submitRequirement').textContent='Lưu thay đổi (cần duyệt lại)';
      reqForm.scrollIntoView({behavior:'smooth',block:'start'});
    },'')));
    document.getElementById('cancelRequirement')?.addEventListener('click',()=>{
      editingId=null;reqForm.classList.add('d-none');
      document.getElementById('cancelRequirement').classList.add('d-none');
      document.getElementById('submitRequirement').textContent='Lưu requirement';
    });
  }
  document.querySelectorAll('.req-approve').forEach(el=>el.addEventListener('click',()=>sw.act(async()=>{
    const pid=location.pathname.split('/')[2];await sw.api(`/api/projects/${pid}/requirements/${el.dataset.id}/status`,{status:'approved'});location.reload();
  })));
  const searchSettings=document.getElementById('searchSettings');
  if(searchSettings)document.getElementById('saveSearch')?.addEventListener('click',()=>sw.act(()=>sw.api('/api/settings/search',{
    url:sw.val('searxngUrl'),language:sw.val('searxngLanguage'),safesearch:Number(sw.val('searxngSafe')),engines:sw.val('searxngEngines'),categories:sw.val('searxngCategories')
  })));
  document.getElementById('saveBrowser')?.addEventListener('click',()=>sw.act(async()=>{
    await sw.api('/api/settings/browser',{proxy:sw.val('browserProxy')});location.reload();
  }));
  document.getElementById('clearBrowserProxy')?.addEventListener('click',()=>{
    if(sw.confirm('Xóa cấu hình browser proxy?'))sw.act(async()=>{await sw.api('/api/settings/browser',{clear_proxy:true});location.reload();});
  });
  const research=document.getElementById('researchApp');
  if(research){
    const pid=research.dataset.project, result=document.getElementById('researchResults');
    const showResult=(list)=>{result.replaceChildren();for(const item of list){
      const card=document.createElement('div');card.className='research-result';
      const a=document.createElement('a');a.textContent=item.title||item.url;a.href=item.url;a.target='_blank';a.rel='noopener noreferrer';
      const url=document.createElement('small');url.textContent=item.url;
      const desc=document.createElement('p');desc.textContent=item.snippet||item.text||'';
      card.append(a,url,desc);result.appendChild(card);
    }};
    document.getElementById('researchSearch')?.addEventListener('click',()=>sw.act(async()=>{
      result.textContent='Đang tìm kiếm...';const data=await sw.api(`/api/projects/${pid}/research/search`,{query:sw.val('researchQuery')});showResult(data.results);
    },'Đã lưu các nguồn tìm kiếm'));
    document.getElementById('researchFetch')?.addEventListener('click',()=>sw.act(async()=>{
      result.textContent='Đang tải nội dung website...';const data=await sw.api(`/api/projects/${pid}/research/fetch`,{url:sw.val('fetchUrl'),browser:document.getElementById('useBrowser').checked});showResult([data]);
    },'Đã lưu nội dung website'));
  }
  const exportApp=document.getElementById('exportApp');
  if(exportApp){
    for(const buttonId of ['exportZip','exportReviewed']){
      document.getElementById(buttonId)?.addEventListener('click',()=>sw.act(async()=>{
        const mode=document.getElementById(buttonId).dataset.mode;
        const response=await fetch(`/api/projects/${exportApp.dataset.project}/export?mode=${mode}`,
            {method:'POST',headers:{'X-CSRF-Token':csrf},credentials:'same-origin'});
        if(!response.ok){
          const error=await response.json().catch(()=>({}));
          const detail=error.detail || {};
          throw new Error(typeof detail==='string'?detail:(detail.message||`Export lỗi HTTP ${response.status}`));
        }
        const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');
        a.href=url;a.download='specweave-'+exportApp.dataset.project.slice(0,8)+'-'+mode+'.zip';
        a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
      },'Đã tạo gói ZIP'));
    }
  }
  const userForm=document.getElementById('userForm');
  if(userForm)document.getElementById('addUser')?.addEventListener('click',()=>sw.act(async()=>{
    await sw.api('/api/admin/users',{username:sw.val('userUsername'),email:sw.val('userEmail'),password:sw.val('userPassword'),role:sw.val('userRole')});location.reload();
  }));
  document.querySelectorAll('.user-toggle').forEach(el=>el.addEventListener('click',()=>sw.act(async()=>{
    await sw.api(`/api/admin/users/${el.dataset.id}/update`,{enabled:el.dataset.enabled==='true'});location.reload();
  })));
  document.querySelectorAll('.user-reset').forEach(el=>el.addEventListener('click',()=>{
    const pw=prompt('Nhập mật khẩu tạm mới (ít nhất 12 ký tự):');
    if(pw)sw.act(()=>sw.api(`/api/admin/users/${el.dataset.id}/update`,{password:pw}));
  }));
})();
