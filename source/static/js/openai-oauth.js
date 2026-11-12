(() => {
  const root=document.getElementById('openaiOAuthApp');
  if(!root||root.dataset.enabled!=='true')return;
  const element=id=>document.getElementById(id);
  let loginId='',busy=false;
  async function showLogin(id){
    const state=await sw.api(`/api/openai/oauth/login/${encodeURIComponent(id)}`,{},'GET');
    const url=new URL(state.url);
    if(url.protocol!=='https:'||url.hostname!=='auth.openai.com')throw Error('Địa chỉ đăng nhập không hợp lệ');
    loginId=id;
    element('openaiOAuthLink').href=state.url;
    element('openaiOAuthAnswer').value='';
    element('openaiOAuthProgress').textContent='Mở trang ChatGPT, hoàn tất xác nhận rồi dán URL chuyển hướng.';
    element('openaiOAuthFlow').classList.remove('d-none');
  }
  async function loadStatus(){
    try{
      const result=await sw.api('/api/openai/oauth/status',{},'GET');
      element('openaiOAuthStatus').textContent=result.connected?`Đã kết nối${result.email?' · '+result.email:''}`:'Chưa kết nối';
      element('openaiOAuthDisconnect').disabled=!result.connected;
      if(result.login_id&&!loginId)await showLogin(result.login_id);
    }catch(error){element('openaiOAuthStatus').textContent=error.message;}
  }
  element('openaiOAuthConnect').addEventListener('click',async()=>{
    if(busy)return;
    busy=true;element('openaiOAuthConnect').disabled=true;
    try{
      const login=await sw.api('/api/openai/oauth/start',{});
      await showLogin(login.id);
    }catch(error){sw.toast(error.message,'error');}
    finally{busy=false;element('openaiOAuthConnect').disabled=false;}
  });
  element('openaiOAuthSend').addEventListener('click',async()=>{
    if(busy||!loginId)return;
    busy=true;element('openaiOAuthSend').disabled=true;
    const field=element('openaiOAuthAnswer');
    try{
      await sw.api(`/api/openai/oauth/login/${encodeURIComponent(loginId)}/answer`,{value:field.value.trim()});
      field.value='';loginId='';
      element('openaiOAuthFlow').classList.add('d-none');
      await loadStatus();sw.toast('Đã kết nối ChatGPT. Fetch hoặc thêm model trong Provider.','success');
      location.reload();
    }catch(error){sw.toast(error.message,'error');}
    finally{busy=false;element('openaiOAuthSend').disabled=false;}
  });
  element('openaiOAuthCancel').addEventListener('click',()=>sw.act(async()=>{
    await sw.api('/api/openai/oauth/cancel',{});loginId='';
    element('openaiOAuthAnswer').value='';element('openaiOAuthLink').removeAttribute('href');
    element('openaiOAuthFlow').classList.add('d-none');
  },''));
  element('openaiOAuthDisconnect').addEventListener('click',()=>sw.act(async()=>{
    await sw.api('/api/openai/oauth/disconnect',{});
    element('openaiOAuthAnswer').value='';element('openaiOAuthLink').removeAttribute('href');
    element('openaiOAuthFlow').classList.add('d-none');await loadStatus();
  },'Đã ngắt kết nối ChatGPT'));
  loadStatus();
})();
