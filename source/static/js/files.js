(() => {
  const root=document.getElementById('fileManager');if(!root)return;
  const pid=root.dataset.project,folder=root.dataset.folder,modal=document.getElementById('fileEditorModal');
  const editable=root.dataset.editable==='true', owner=root.dataset.owner==='true';
  const editor=document.getElementById('fileEditor');let currentPath='',currentMtime=null;
  const close=()=>modal.classList.add('d-none');
  document.querySelectorAll('[data-close-modal]').forEach(x=>x.addEventListener('click',close));
  modal.addEventListener('click',e=>{if(e.target===modal)close()});
  document.getElementById('newFolder').addEventListener('click',()=>{
    if(!folder){sw.toast('Chọn thư mục inputs/docs/notes/research trước khi thêm folder','error');return}
    const name=prompt('Tên thư mục mới:');if(!name)return;
    sw.act(async()=>{await sw.api(`/api/projects/${pid}/folders`,{path:folder+'/'+name});location.reload()},'');
  });
  async function upload(files){
    if(!files.length)return;
    if(!folder){sw.toast('Hãy mở một thư mục trước khi upload','error');return}
    const fd=new FormData();fd.append('folder',folder);fd.append('csrf',sw.csrf);
    for(const file of files)fd.append('files',file,file.name);
    const label=document.getElementById('uploadStatus');label.textContent='Đang upload...';
    try{
      const r=await fetch(`/api/projects/${pid}/files/upload`,{method:'POST',body:fd});const data=await r.json();
      if(!r.ok)throw new Error(data.message||data.detail||`HTTP ${r.status}`);
      sw.toast(`Đã upload ${data.files.length} file`,'success');location.reload();
    }catch(e){label.textContent='Upload lỗi';sw.toast(e.message,'error')}
  }
  document.getElementById('uploadInput').addEventListener('change',e=>upload([...e.target.files]));
  root.addEventListener('dragover',e=>{e.preventDefault();root.classList.add('drag-over')});
  root.addEventListener('dragleave',()=>root.classList.remove('drag-over'));
  root.addEventListener('drop',e=>{e.preventDefault();root.classList.remove('drag-over');upload([...e.dataTransfer.files])});
  document.getElementById('refreshFiles').addEventListener('click',()=>location.reload());
  const trashPanel=document.getElementById('trashPanel');
  document.getElementById('openTrash')?.addEventListener('click',async()=>{
    trashPanel.classList.remove('d-none'); await refreshTrash();
  });
  document.getElementById('closeTrash')?.addEventListener('click',()=>trashPanel.classList.add('d-none'));
  async function refreshTrash(){
    const root=document.getElementById('trashEntries');root.replaceChildren();
    try{
      const r=await fetch(`/api/projects/${pid}/files/trash`,{credentials:'same-origin'});
      const data=await r.json();if(!r.ok)throw Error(data.message||`HTTP ${r.status}`);
      if(!data.entries.length){const p=document.createElement('p');p.textContent='Thùng rác trống.';root.appendChild(p);return;}
      for(const entry of data.entries){
        const line=document.createElement('div');line.className='d-flex flex-wrap align-items-center gap-2 py-2 border-bottom';
        const title=document.createElement('span');title.className='flex-grow-1';
        title.textContent=`${entry.type==='folder'?'[Thư mục] ':''}${entry.path} · ${entry.deleted_at||''}`;
        line.appendChild(title);
        const addButton=(label,handler,cls)=>{
          const b=document.createElement('button');b.type='button';b.textContent=label;
          b.className=cls||'btn btn-sm btn-outline-primary';b.addEventListener('click',handler);line.appendChild(b);
        };
        if(editable)addButton('Phục hồi',()=>sw.act(async()=>{
          const dest=prompt('Đường dẫn phục hồi:',entry.path);
          if(dest===null)return;
          await sw.api(`/api/projects/${pid}/files/trash/${entry.id}/restore`,{destination:dest.trim()||entry.path});
          await refreshTrash();sw.toast('Đã phục hồi','success');
        },''));
        if(owner)addButton('Xóa vĩnh viễn',()=>{
          if(!confirm('Xóa vĩnh viễn '+entry.path+'? Không thể hoàn tác!'))return;
          sw.act(async()=>{
            await sw.api(`/api/projects/${pid}/files/trash/${entry.id}`,{},'DELETE');
            await refreshTrash();sw.toast('Đã xóa vĩnh viễn','success');
          },'');
        },'btn btn-sm btn-outline-danger');
        root.appendChild(line);
      }
    }catch(e){const p=document.createElement('p');p.className='text-danger';p.textContent=e.message;root.appendChild(p);}
  }

  root.querySelectorAll('.file-open').forEach(button=>button.addEventListener('click',async()=>{
    currentPath=button.closest('tr').dataset.path;
    try{
      const r=await fetch(`/api/projects/${pid}/files/content?path=${encodeURIComponent(currentPath)}`);
      const data=await r.json();if(!r.ok)throw new Error(data.message||'Không thể mở văn bản');
      const isJson=/\.json$/i.test(currentPath);
      const formatBtn=document.getElementById('formatFileJson');
      formatBtn.classList.toggle('d-none',!isJson);
      if(isJson){try{editor.value=JSON.stringify(JSON.parse(data.content),null,2)}catch{editor.value=data.content}}
      else editor.value=data.content;
      document.getElementById('fileEditorTitle').textContent=currentPath;
      document.getElementById('fileVersionInfo').textContent=`${data.content.length} ký tự`;
      currentMtime=data.mtime;modal.classList.remove('d-none');
    }catch(e){sw.toast(e.message,'error')}
  }));
  document.getElementById('formatFileJson').addEventListener('click',()=>{
    try{editor.value=JSON.stringify(JSON.parse(editor.value),null,2);sw.toast('Đã định dạng JSON','success')}
    catch(e){sw.toast('JSON không hợp lệ: '+e.message,'error')}
  });
  document.getElementById('saveFile').addEventListener('click',()=>sw.act(async()=>{
    await sw.api(`/api/projects/${pid}/files/content`,{path:currentPath,content:editor.value,expected_mtime:currentMtime});close();
  }));
  document.getElementById('fileVersions').addEventListener('click',()=>sw.act(async()=>{
    const data=await fetch(`/api/projects/${pid}/files/versions?path=${encodeURIComponent(currentPath)}`).then(x=>x.json());
    const list=data.versions||[];if(!list.length){sw.toast('Chưa có bản cũ');return;}
    const chosen=prompt('Chọn bản để phục hồi:\n'+list.join('\n'),list[0]);
    if(chosen && list.includes(chosen) && sw.confirm('Phục hồi sẽ thay thế nội dung hiện tại?')){
      await sw.api(`/api/projects/${pid}/files/restore`,{path:currentPath,version:chosen});location.reload();
    }
  },''));
  root.querySelectorAll('.file-rename').forEach(b=>b.addEventListener('click',()=>{
    const old=b.closest('tr').dataset.path;const chosen=prompt('Đường dẫn mới (tương đối root dự án):',old);if(!chosen||old===chosen)return;
    sw.act(async()=>{await sw.api(`/api/projects/${pid}/files/move`,{source:old,destination:chosen});location.reload()},'');
  }));
  root.querySelectorAll('.file-delete').forEach(b=>b.addEventListener('click',()=>{
    const path=b.closest('tr').dataset.path;
    if(sw.confirm('Chuyển '+path+' vào thùng rác nội bộ?'))sw.act(async()=>{await sw.api(`/api/projects/${pid}/files/delete`,{path});location.reload()},'');
  }));
})();
