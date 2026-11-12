(() => {
  const root=document.getElementById('docsApp');if(!root)return;
  const pid=root.dataset.project,doc=root.dataset.doc;let revision=Number(root.dataset.revision),mtime=Number(root.dataset.mtime);
  document.getElementById('newDoc')?.addEventListener('click',()=>{
    const name=prompt('Tên tài liệu mới:','Product Brief');if(!name)return;
    sw.act(async()=>{const data=await sw.api(`/api/projects/${pid}/documents`,{name});location.href=data.redirect},'');
  });
  const input=document.getElementById('docEditor');if(!input)return;
  let editor=null;
  if(window.CodeMirror){
    editor=CodeMirror.fromTextArea(input,{mode:'markdown',lineNumbers:true,lineWrapping:true,indentUnit:2,extraKeys:{'Ctrl-S':save,'Cmd-S':save}});
  }
  const getText=()=>editor?editor.getValue():input.value;
  const preview=document.getElementById('docPreview');
  function update(){
    if(window.marked&&window.DOMPurify){preview.innerHTML=DOMPurify.sanitize(marked.parse(getText()));
      preview.querySelectorAll('a').forEach(a=>{a.target='_blank';a.rel='noopener noreferrer'});
    }else preview.textContent=getText();
    document.getElementById('docSaveStatus').textContent='• Chưa lưu';
  }
  if(editor)editor.on('change',()=>update());else input.addEventListener('input',update);
  async function save(){
    try{
      const result=await sw.api(`/api/projects/${pid}/files/content`,{path:root.dataset.path,content:getText(),expected_mtime:mtime});
      mtime=result.mtime;revision+=1;document.getElementById('docSaveStatus').textContent='• Đã lưu';sw.toast('Đã lưu bản nháp','success');
    }catch(e){sw.toast(e.message,'error')}
  }
  document.getElementById('docSave')?.addEventListener('click',save);
  document.getElementById('docApprove')?.addEventListener('click',()=>{
    if(!sw.confirm('Phê duyệt revision hiện tại? Hãy chắc chắn đã lưu các sửa đổi.'))return;
    sw.act(async()=>{await sw.api(`/api/projects/${pid}/documents/${doc}/approve`,{revision});location.reload()},'');
  });
  document.querySelectorAll('.editor-tab').forEach(btn=>btn.addEventListener('click',()=>{
    document.querySelectorAll('.editor-tab').forEach(x=>x.classList.toggle('active',x===btn));
    document.getElementById('markdownWorkspace').className='markdown-workspace mode-'+btn.dataset.view;
    if(editor)setTimeout(()=>editor.refresh(),30);
  }));
  document.addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();save();}
  });
  update();document.getElementById('docSaveStatus').textContent='';
})();
