/* SpecWeave product completion pages; self-contained, no Node.js/browser build. */
(() => {
  const $ = id => document.getElementById(id);
  const api = (...args) => sw.api(...args);
  const act = (fn,good='Đã cập nhật') => sw.act(fn,good);
  const area=$('artifactsApp');
  if(area){
    const pid=area.dataset.project;
    $('artifactSelectAll')?.addEventListener('click',()=>document.querySelectorAll('.artifact-select:not(:disabled)').forEach(x=>x.checked=true));
    document.querySelectorAll('button.artifact-preview').forEach(button=>button.addEventListener('click',()=>act(async()=>{
      const key=button.dataset.key;
      const response=await fetch(`/api/projects/${pid}/artifacts?key=${encodeURIComponent(key)}`);
      const payload=await response.json();if(!response.ok)throw Error(payload.message||'Cannot load draft');
      $('artifactPreviewTitle').textContent=payload.title;
      $('artifactPreviewContent').textContent=payload.content;
      $('artifactPreviewContent').focus();
    },'')));
    $('artifactGenerate')?.addEventListener('click',()=>act(async()=>{
      const keys=[...document.querySelectorAll('.artifact-select:checked')].map(x=>x.value);
      if(!keys.length)throw Error('Hãy chọn ít nhất một tài liệu');
      const overwrite=$('artifactOverwrite').checked;
      if(!confirm(`Tạo ${keys.length} draft? ${overwrite?'Các bản nháp trùng đường dẫn sẽ được thay thế, có snapshot.':''}`))return;
      const result=await api(`/api/projects/${pid}/artifacts/generate`,{keys,overwrite});
      sw.toast(`Đã tạo ${result.count} bản nháp`,'success');location.reload();
    },''));
  }
  if($('presetCreate')){
    const name=$('presetName'), data=$('presetProfile');
    let selected=null;
    $('presetCreate').addEventListener('click',()=>act(async()=>{
      let profile;try{profile=JSON.parse(data.value)}catch{throw Error('JSON không hợp lệ')}
      if(!profile || Array.isArray(profile) || typeof profile!=='object')throw Error('Profile phải là JSON object');
      if(!name.value.trim())throw Error('Cần nhập tên preset');
      if(selected)await api(`/api/presets/${selected}`,{name:name.value,profile});
      else await api('/api/presets',{name:name.value,profile});
      location.reload();
    },''));
    document.querySelectorAll('.preset-load').forEach(button=>button.addEventListener('click',()=>act(async()=>{
      const list=await fetch('/api/presets').then(r=>r.json());
      const preset=list.find(x=>x.id===button.dataset.id);if(!preset)throw Error('Preset không tồn tại');
      selected=preset.id;name.value=preset.name;data.value=JSON.stringify(preset.profile,null,2);
      $('presetCreate').textContent='Lưu thay đổi';name.scrollIntoView({behavior:'smooth'});
    },'')));
    document.querySelectorAll('.preset-delete').forEach(button=>button.addEventListener('click',()=>{
      if(!confirm('Xóa preset này?'))return;
      act(async()=>{await api(`/api/presets/${button.dataset.id}`,{},'DELETE');location.reload();},'');
    }));
  }
  document.querySelectorAll('.project-restore').forEach(b=>b.addEventListener('click',()=>{
    if(!confirm('Khôi phục dự án và quyền thành viên?'))return;
    act(async()=>{const r=await api(`/api/projects/${b.dataset.project}/restore`);location.href=r.redirect;},'');
  }));
  if($('appearanceTheme')){
    $('appearanceTheme').value=localStorage.getItem('sw-theme-preference')||'light';
    $('appearanceDensity').value=localStorage.getItem('sw-density')||'comfortable';
    $('saveAppearance').addEventListener('click',()=>{
      localStorage.setItem('sw-theme-preference',$('appearanceTheme').value);
      localStorage.setItem('sw-density',$('appearanceDensity').value);
      location.reload();
    });
  }
  if($('saveProfile'))$('saveProfile').addEventListener('click',()=>act(async()=>{
    await api('/api/profile',{display_name:$('profileDisplayName').value,email:$('profileEmail').value});location.reload();
  },''));
  document.querySelectorAll('.session-revoke').forEach(button=>button.addEventListener('click',()=>{
    if(!confirm('Thu hồi phiên đăng nhập này?'))return;
    act(async()=>{const r=await api(`/api/profile/sessions/${button.dataset.id}`,{},'DELETE');location.href=r.current_session?'/login':'/profile';},'');
  }));
  document.querySelectorAll('.member-role').forEach(select=>select.addEventListener('change',()=>act(async()=>{
    const pid=select.dataset.project;
    await api(`/api/projects/${pid}/members/${select.dataset.user}`,{role:select.value});
  })));
  document.querySelectorAll('.member-remove').forEach(button=>button.addEventListener('click',()=>{
    if(!confirm('Thu hồi quyền truy cập của thành viên này?'))return;
    act(async()=>{await api(`/api/projects/${button.dataset.project}/members/${button.dataset.user}`,{},'DELETE');location.reload();},'');
  }));
  if($('transferOwner'))$('transferOwner').addEventListener('click',()=>{
    const pid=$('projectSettings').dataset.project,userId=$('ownershipTarget').value;
    if(!userId) return sw.toast('Chọn thành viên đang có trong dự án','error');
    const name=prompt('Nhập chính xác tên dự án để chuyển ownership:');
    if(!name)return;
    act(async()=>{await api(`/api/projects/${pid}/transfer-ownership`,{user_id:userId,confirm_name:name});location.href=`/projects/${pid}`;},'');
  });
  const providerApp=$('providerDetailApp');
  if(providerApp){
    const pid=providerApp.dataset.provider;
    $('providerEditSave')?.addEventListener('click',()=>act(async()=>{
      const payload={name:$('providerEditName').value,enabled:$('providerEditEnabled').checked};
      if($('providerEditKind'))Object.assign(payload,{kind:$('providerEditKind').value,
        base_url:$('providerEditBase').value,api_key:$('providerEditKey').value});
      await api(`/api/providers/${pid}`,payload);
      location.reload();
    },''));
    $('providerClearKey')?.addEventListener('click',()=>{
      if(!confirm('Xóa API key của provider này? Việc gọi model có thể ngừng hoạt động.'))return;
      act(async()=>{await api(`/api/providers/${pid}`,{clear_api_key:true});location.reload();},'');
    });
    document.querySelectorAll('.model-save').forEach(button=>button.addEventListener('click',()=>act(async()=>{
      const id=button.dataset.id;
      const size=document.querySelector(`.model-context[data-id="${id}"]`).value;
      const reasoning=document.querySelector(`.model-reasoning[data-id="${id}"]`).value==='true';
      await api(`/api/providers/${pid}/models/${id}`,{context_window:Number(size),reasoning});location.reload();
    },'')));
  }
  document.querySelectorAll('.provider-toggle').forEach(button=>button.addEventListener('click',()=>act(async()=>{
    await api(`/api/providers/${button.dataset.id}`,{enabled:button.dataset.enabled==='true'});location.reload();
  },'')));
  document.querySelectorAll('.model-toggle').forEach(button=>button.addEventListener('click',()=>act(async()=>{
    await api(`/api/providers/${button.dataset.provider}/models/${button.dataset.id}`,{enabled:button.dataset.enabled==='true'});location.reload();
  },'')));
})();
