/* OpenAI OAuth owner-controlled local runtime. Never persist or log any token/callback. */
(() => {
  const root=document.getElementById('openaiOAuthApp');
  if(!root || root.dataset.enabled!=='true')return;
  const $=id=>document.getElementById(id);
  const auth=path=>sw.api('/api/openai/oauth/'+path,{},'GET');
  let loginId='',offset=0,busy=false,pollHandle=null,finished=false;
  function status(text,kind=''){const el=$('openaiOAuthStatus');el.textContent=text;el.className='status-chip '+kind;}
  function progress(text){$('openaiOAuthProgress').textContent=text;}
  function stopPolling(){if(pollHandle){clearTimeout(pollHandle);pollHandle=null;}}
  function setFlowInput(enabled){
    $('openaiOAuthSend').disabled=!enabled;
    $('openaiOAuthAnswer').disabled=!enabled;
  }
  function failLogin(message){
    finished=true;stopPolling();setFlowInput(false);
    progress(message+' — Bấm Connect / Reconnect để thử phiên mới.');
  }
  async function loadStatus(){
    try{const data=await auth('status');
      status(data.connected?(data.provider_enabled?'Đã kết nối':'Đã xác thực · cần kích hoạt'):'Chưa kết nối',data.connected?'good':'');
      $('openaiOAuthDisconnect').disabled=!data.connected;
      return data;
    }catch(e){status('Không truy cập được Pi runner','rejected');sw.toast(e.message,'error');return null;}
  }
  async function poll(){
    if(!loginId || finished)return;
    try{
      const result=await auth(`login/${encodeURIComponent(loginId)}?offset=${offset}`);
      offset=result.next_offset;
      for(const event of result.events||[]){
        if(event.type==='auth_url'){
          const a=$('openaiOAuthLink');a.href=event.url;a.classList.remove('d-none');progress('Mở liên kết xác thực trong trình duyệt');
        }else if(event.type==='device_code'){
          $('openaiOAuthCodeInfo').textContent=`Truy cập ${event.url}, nhập mã ${event.user_code}`;
        }else if(event.type==='prompt')progress(event.message||'Dán callback URL / authorization code');
        else if(event.type==='progress')progress(event.message||'Đang chờ đăng nhập');
        else if(event.type==='error'){failLogin(`${event.message} (${event.code||'OAUTH_RUNTIME_FAILURE'}; ${event.phase||'unknown'} / ${event.hint||'UNCLASSIFIED'})`);sw.toast(event.message,'error');}
        else if(event.type==='done'){
          finished=true;progress('Đã xác thực OpenAI thành công.');
          await sw.api('/api/openai/oauth/activate',{});
          await loadStatus();sw.toast('Đã kết nối tài khoản OpenAI','success');
          $('openaiOAuthFlow').classList.add('d-none');
        }
      }
      if(result.status==='error' && !finished)failLogin('Phiên đăng nhập đã thất bại');
    }catch(e){failLogin(e.message);sw.toast(e.message,'error');}
    if(!finished)pollHandle=setTimeout(poll,1200);
  }
  $('openaiOAuthDiagnose')?.addEventListener('click',()=>sw.act(async()=>{
    const data=await auth('diagnostics');
    const out=$('openaiOAuthDiagnostics');
    out.classList.remove('d-none');
    out.textContent=['Node imports: '+(data.node_import?'OK':'FAILED'),
      'Pi models.login: '+(data.model_login?'OK':'FAILED'),
      'OpenAI OAuth adapter: '+(data.provider_oauth?'OK':'FAILED'),
      'Credential volume writable: '+(data.vault_writable?'OK':'FAILED')].join('\n');
  },''));
  $('openaiOAuthConnect')?.addEventListener('click',async()=>{
    if(busy)return;
    if(!confirm('Chỉ kết nối tài khoản ChatGPT của chính anh trên runtime cục bộ do anh kiểm soát. Không chia sẻ cho người khác. Tiếp tục?'))return;
    busy=true;
    try{
      stopPolling();finished=false;offset=0;setFlowInput(true);
      const result=await sw.api('/api/openai/oauth/start',{});
      loginId=result.id;
      $('openaiOAuthFlow').classList.remove('d-none');
      $('openaiOAuthLink').classList.add('d-none');
      $('openaiOAuthCodeInfo').textContent='';
      $('openaiOAuthAnswer').value='';progress('Đang khởi tạo đăng nhập...');
      await poll();
    }catch(e){sw.toast(e.message,'error');}
    finally{busy=false;}
  });
  $('openaiOAuthSend')?.addEventListener('click',()=>sw.act(async()=>{
    if(finished)throw Error('Phiên OAuth đã kết thúc. Hãy bấm Connect / Reconnect để tạo phiên mới.');
    if(!loginId)throw Error('Chưa có phiên đăng nhập');
    const field=$('openaiOAuthAnswer'),value=field.value.trim();
    if(!value)throw Error('Cần dán redirect URL hoặc authorization code');
    await sw.api(`/api/openai/oauth/login/${encodeURIComponent(loginId)}/answer`,{value});
    field.value='';progress('Đang xác nhận với OpenAI...');
  },''));
  $('openaiOAuthCancel')?.addEventListener('click',()=>sw.act(async()=>{
    stopPolling();finished=true;await sw.api('/api/openai/oauth/cancel',{});
    $('openaiOAuthFlow').classList.add('d-none');
  },''));
  $('openaiOAuthDisconnect')?.addEventListener('click',()=>{
    if(!confirm('Ngắt kết nối OpenAI? Credential cục bộ sẽ bị xóa. Việc ngắt kết nối không tự thu hồi ủy quyền ở phía OpenAI.'))return;
    sw.act(async()=>{
      stopPolling();finished=true;
      await sw.api('/api/openai/oauth/disconnect',{});
      await loadStatus();
    });
  });
  loadStatus();
})();
