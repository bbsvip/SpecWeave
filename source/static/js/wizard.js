(() => {
  const root=document.querySelector('[data-wizard]');if(!root)return;
  const researchPolicy=document.getElementById('researchPolicy');
  let step=1;
  const selections={};
  for(const el of document.querySelectorAll('.chip-picks')){
    const key=el.dataset.field,items=el.dataset.options.split('|'),defaults=(el.dataset.default||'').split('|');
    selections[key]=new Set(defaults.filter(Boolean));
    function makeChip(text){
      if([...el.querySelectorAll('.choice-chip')].some(x=>x.textContent.replace(/^✓/,'')===text))return;
      const b=document.createElement('button');b.type='button';b.className='choice-chip';b.textContent=text;
      b.classList.toggle('selected',selections[key].has(text));b.setAttribute('aria-pressed',String(selections[key].has(text)));
      b.addEventListener('click',()=>{selections[key].has(text)?selections[key].delete(text):selections[key].add(text);b.classList.toggle('selected',selections[key].has(text));b.setAttribute('aria-pressed',String(selections[key].has(text)));updateReview();});
      el.insertBefore(b,el.querySelector('.choice-add'));
    }
    const plus=document.createElement('button');plus.className='choice-add';plus.type='button';plus.textContent='+ Tùy chọn khác';
    plus.addEventListener('click',()=>{
      if(el.querySelector('.custom-choice-input'))return;
      const input=document.createElement('input');input.className='custom-choice-input';input.placeholder='Nhập lựa chọn...';
      const finish=()=>{const value=input.value.trim().slice(0,100);input.remove();if(value){selections[key].add(value);makeChip(value);updateReview();}};
      input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();finish()}if(e.key==='Escape')input.remove()});
      input.addEventListener('blur',finish);el.insertBefore(input,plus);input.focus();
    });
    el.appendChild(plus);
    items.forEach(makeChip);
    el.__makeChip=makeChip;
  }
  function collect(){
    const profile={};for(const el of document.querySelectorAll('[data-profile]'))profile[el.dataset.profile]=el.value;
    for(const [k,values] of Object.entries(selections))profile[k]=[...values];
    profile.research_policies=[researchPolicy.value];
    if(profile.quality_review_required==='true')profile.quality_review_required=true;
    else if(profile.quality_review_required==='false')profile.quality_review_required=false;
    if(profile.ui_required==='false')profile.ui_required=false;
    else if(profile.ui_required==='true')profile.ui_required=true;
    else delete profile.ui_required;
    return profile;
  }
  function apply(profile){
    const policies=profile.research_policies;
    if(Array.isArray(policies)){
      const valid=[...researchPolicy.options].map(option=>option.value);
      researchPolicy.value=policies.includes('No external research')?'No external research':
        policies.find(policy=>valid.includes(policy))||valid[0];
    }
    for(const el of document.querySelectorAll('[data-profile]'))if(Object.hasOwn(profile,el.dataset.profile))el.value=profile[el.dataset.profile];
    for(const [key,value] of Object.entries(profile))if(selections[key] && Array.isArray(value)){
      selections[key]=new Set(value);const wrap=document.querySelector(`.chip-picks[data-field="${key}"]`);
      value.forEach(x=>wrap.__makeChip(x));wrap.querySelectorAll('.choice-chip').forEach(chip=>{
        const selected=selections[key].has(chip.textContent.replace(/^✓/,''));chip.classList.toggle('selected',selected);chip.setAttribute('aria-pressed',String(selected));
      });
    }
    updateReview();
  }
  function updateReview(){
    if(step!==5)return;
    document.getElementById('wizardReview').textContent='Thông tin đã chọn: '+Object.entries(collect()).filter(([,v])=>Array.isArray(v)?v.length:v!==''&&v!=null).map(([k,v])=>k.replaceAll('_',' ')+': '+(Array.isArray(v)?v.join(', '):String(v).slice(0,70))).join(' · ');
  }
  root.addEventListener('input',updateReview);
  root.addEventListener('change',updateReview);
  function go(n){step=Math.max(1,Math.min(5,n));
    document.querySelectorAll('[data-panel]').forEach(el=>el.classList.toggle('d-none',Number(el.dataset.panel)!==step));
    document.querySelectorAll('.wizard-step').forEach(el=>{const x=Number(el.dataset.step);el.classList.toggle('active',x===step);el.classList.toggle('done',x<step);});
    document.getElementById('wizardBack').disabled=step===1;document.getElementById('wizardStatus').textContent=`Bước ${step} / 5`;
    document.getElementById('wizardNext').innerHTML=step===5?'Tạo dự án <i class="ti ti-check"></i>':'Tiếp tục <i class="ti ti-arrow-right"></i>';
    document.getElementById('wizardProgress').style.width=(step*20)+'%';
    updateReview();
    window.scrollTo({top:0,behavior:'smooth'});
  }
  document.getElementById('wizardBack').addEventListener('click',()=>go(step-1));
  document.querySelectorAll('.wizard-step').forEach(el=>el.addEventListener('click',()=>go(Number(el.dataset.step))));
  document.getElementById('wizardNext').addEventListener('click',async()=>{
    if(step<5){if(step===1 && document.getElementById('newName').value.trim().length<2){sw.toast('Nhập tên dự án trước khi tiếp tục','error');return}go(step+1);return;}
    const button=document.getElementById('wizardNext');button.disabled=true;
    try{
      const payload={name:sw.val('newName'),description:sw.val('newDescription'),profile:collect(),model_id:sw.val('newModel'),thinking:sw.val('newThinking')};
      const result=await sw.api('/api/projects',payload);location.href=result.redirect;
    }catch(e){sw.toast(e.message,'error');button.disabled=false;}
  });
  document.getElementById('savePreset').addEventListener('click',async()=>{
    const name=prompt('Tên preset để lưu nhanh:');if(!name)return;
    await sw.act(async()=>{const res=await sw.api('/api/presets',{name,profile:collect()});const opt=document.createElement('option');opt.value=res.id;opt.textContent=name;document.getElementById('presetPicker').appendChild(opt);},'Đã lưu preset');
  });
  document.getElementById('presetPicker').addEventListener('change',async e=>{
    if(!e.target.value)return;
    try{const presets=await fetch('/api/presets').then(x=>x.json());const found=presets.find(p=>p.id===e.target.value);if(found){apply(found.profile);sw.toast('Đã áp dụng preset','success')}}catch(err){sw.toast(err.message,'error')}
  });
})();
