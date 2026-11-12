(() => {
  const root=document.getElementById('providersApp');if(!root)return;
  const form=document.getElementById('providerForm'),modal=document.getElementById('modelModal');let selectedProvider='';
  document.getElementById('showProviderForm').addEventListener('click',()=>form.classList.toggle('d-none'));
  document.getElementById('addProvider').addEventListener('click',()=>sw.act(async()=>{
    await sw.api('/api/providers',{name:sw.val('providerName'),base_url:sw.val('providerBase'),kind:sw.val('providerKind'),api_key:sw.val('providerKey')});location.reload();
  },''));
  const close=()=>modal.classList.add('d-none');modal.querySelector('[data-close-modal]').addEventListener('click',close);
  modal.addEventListener('click',e=>{if(e.target===modal)close()});
  function populate(items){
    const list=document.getElementById('fetchedModels');list.replaceChildren();
    for(const item of items){const label=document.createElement('label'),check=document.createElement('input');
      const id=typeof item==='string'?item:item.id;
      const name=typeof item==='string'?item:item.name||id;
      check.type='checkbox';check.value=id;check.className='form-check-input me-2';
      label.append(check,document.createTextNode(name===id?name:`${name} · ${id}`));list.appendChild(label);
    }
  }
  async function fetchModels(providerId){
    selectedProvider=providerId;modal.classList.remove('d-none');populate([]);
    document.getElementById('fetchedModels').textContent='Đang tải danh sách...';
    try{const data=await fetch(`/api/providers/${providerId}/discover`).then(async r=>{const d=await r.json();if(!r.ok)throw new Error(d.message||`HTTP ${r.status}`);return d});
      populate(data.model_details||data.models);if(!data.models.length)sw.toast('Endpoint không có model nào, có thể thêm manual.');
    }catch(e){populate([]);sw.toast(e.message,'error')}
  }
  root.querySelectorAll('.provider-fetch').forEach(btn=>btn.addEventListener('click',()=>fetchModels(btn.dataset.id)));
  root.querySelectorAll('.provider-add-model').forEach(btn=>btn.addEventListener('click',()=>{
    selectedProvider=btn.dataset.id;populate([]);modal.classList.remove('d-none');
  }));
  document.getElementById('saveModels').addEventListener('click',()=>sw.act(async()=>{
    const chosen=[...modal.querySelectorAll('.fetched-model-list input:checked')].map(x=>x.value);
    chosen.push(...sw.val('manualModels').split(/[\n,]/).map(x=>x.trim()).filter(Boolean));
    if(!chosen.length)throw new Error('Chọn hoặc nhập model ID');
    await sw.api(`/api/providers/${selectedProvider}/models`,{model_ids:[...new Set(chosen)],reasoning:document.getElementById('modelReasoning').checked});location.reload();
  },''));
})();
