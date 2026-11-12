/* Provider deletion: preview model/project/review impact before typed confirmation. */
(() => {
  async function remove(id) {
    try {
      const preview=await sw.api(`/api/providers/${encodeURIComponent(id)}/delete-preview`,{},'GET');
      const details=[`Xóa provider: ${preview.name}`,
        `• ${preview.models} model sẽ bị xóa vĩnh viễn`,
        `• ${preview.projects} dự án sẽ bỏ model đang chọn`,
        `• ${preview.review_jobs} review giữ lịch sử`,
        `• ${preview.active_reviews} review đang chạy/chờ`,
        `• ${preview.active_chats} chat đang chạy`];
      if(preview.project_names.length)details.push('Dự án ảnh hưởng: '+preview.project_names.join(', '));
      if(preview.kind==='openai-oauth')details.push('Cần Disconnect OpenAI OAuth trước khi xóa.');
      const choice=window.prompt(details.join('\n')+'\n\nNhập CHÍNH XÁC tên provider để xác nhận xóa:');
      if(choice===null)return;
      if(choice!==preview.name)throw Error('Tên xác nhận không trùng với provider.');
      const result=await sw.api(`/api/providers/${encodeURIComponent(id)}`,{confirm_name:choice},'DELETE');
      sw.toast(`Đã xóa ${result.models_deleted} models; ${result.projects_reset} dự án đã bỏ model.`, 'success');
      window.location.assign('/settings/providers');
    } catch(e) {sw.toast(e.message||String(e),'error');}
  }
  document.querySelectorAll('.provider-delete').forEach(btn=>btn.addEventListener('click',()=>remove(btn.dataset.id)));
  const detail=document.getElementById('providerDetailApp');
  document.getElementById('providerDelete')?.addEventListener('click',()=>remove(detail.dataset.provider));
  document.querySelectorAll('.model-delete').forEach(btn=>btn.addEventListener('click',async()=>{
    const id=detail?.dataset.provider,mid=btn.dataset.id;
    try {
      const impact=await sw.api(`/api/providers/${encodeURIComponent(id)}/models/${encodeURIComponent(mid)}/delete-preview`,{},'GET');
      const reply=prompt(`Xóa model ${impact.model_name}?\n${impact.projects} dự án sẽ bỏ model; ${impact.review_jobs} review giữ lịch sử.\n${impact.active_reviews} review và ${impact.active_chats} chat đang hoạt động.\n\nNhập chính xác Model ID để xác nhận:`);
      if(reply===null)return;
      if(reply!==impact.model_name)throw Error('Model ID xác nhận không chính xác');
      await sw.api(`/api/providers/${encodeURIComponent(id)}/models/${encodeURIComponent(mid)}`,{confirm_model:reply},'DELETE');
      location.reload();
    }catch(e){sw.toast(e.message||String(e),'error');}
  }));
})();
