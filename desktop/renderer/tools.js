export function initTools(hooks) {
  const {api,getState,getCurrent,applyState,onError,setBusy,clearResources,refreshMessages}=hooks;
  const $=id=>document.getElementById(id);
  const element=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
  const attempt=async fn=>{try{return await fn();}catch(error){onError(error);}};
  let polling=false,activeJob=null,lastApplied=null;
  function dialog(title){
    $('tool-title').textContent=title;$('tool-body').replaceChildren();$('tool-actions').replaceChildren();$('tool-message').textContent='';
    if(!$('tool-dialog').open)$('tool-dialog').showModal();
    return $('tool-body');
  }
  function button(text,action,primary=false,parent=$('tool-actions')){
    const control=element('button',primary?'primary':'',text);control.addEventListener('click',()=>attempt(action));parent.append(control);return control;
  }
  function field(parent,label,{type='text',value='',choices=null}={}){
    const holder=element('label','',label),input=element(choices?'select':'input');
    if(choices){for(const [key,text] of choices){const option=element('option','',text);option.value=key;input.append(option);}}
    else input.type=type;
    input.value=value;holder.append(input);parent.append(holder);return input;
  }
  function option(parent,label,checked=false){
    const holder=element('label','option'),input=element('input');input.type='checkbox';input.checked=checked;holder.append(input,document.createTextNode(label));parent.append(holder);return input;
  }
  function form(parent){const body=element('div','tool-form');parent.append(body);return body;}
  function formatChoices(parent,defaults=getState()?.formats||['html']){
    const holder=element('div','tool-checks');parent.append(holder);
    const fields=new Map(['txt','html','pdf'].map(format=>[format,option(holder,format.toUpperCase(),defaults.includes(format))]));
    return ()=>[...fields].filter(([,input])=>input.checked).map(([key])=>key);
  }
  function chats(parent){
    const list=element('div','tool-chat-list');parent.append(list);const fields=new Map();
    for(const chat of getState()?.chats||[]){
      const label=element('label'),input=element('input');input.type='checkbox';input.checked=chat.id===getCurrent()?.id;
      label.append(input,element('span','',chat.title),element('small','',`${chat.count.toLocaleString()} 条`));list.append(label);fields.set(chat.id,input);
    }
    const controls=element('div');parent.append(controls);
    button('全选 / 全不选',()=>{const all=[...fields.values()].every(input=>input.checked);for(const input of fields.values())input.checked=!all;},false,controls);
    return ()=>[...fields].filter(([,input])=>input.checked).map(([key])=>key);
  }
  function cancel(){button('取消',()=>$('tool-dialog').close());}
  async function showResult(result){
    if(result?.canceled)return;
    if(result?.id){$('tool-dialog').close();$('job-dialog').showModal();activeJob=result.id;await poll();return;}
    if(result?.state)applyState(result.state);
    if(result?.message)$('tool-message').textContent=result.message;
  }
  async function request(params){
    const controls=[...$('tool-actions').querySelectorAll('button')];controls.forEach(control=>control.disabled=true);
    $('tool-message').textContent='正在处理…';
    try{const result=await api.tool(params);await showResult(result);if(result?.canceled)$('tool-message').textContent='已取消';return result;}
    finally{controls.forEach(control=>control.disabled=false);}
  }
  function batch(incremental=false){
    const body=form(dialog(incremental?'增量备份':'批量导出'));
    const getChats=chats(body),getFormats=formatChoices(body);
    const remember=option(body,'保存队列，便于重启后继续',true),manifest=option(body,'生成导出清单和文件校验值',true);
    const inc=option(body,'增量备份：保存快照，后续追加新消息',incremental),rebuild=option(body,'全量重建已有增量备份（覆盖原导出）');
    rebuild.disabled=!inc.checked;inc.addEventListener('change',()=>{rebuild.disabled=!inc.checked;if(!inc.checked)rebuild.checked=false;});
    cancel();button('选择目录',async()=>{const path=await api.chooseFolder();if(path)$('tool-message').textContent=path;});
    button('开始',async()=>{
      if(!getChats().length || !getFormats().length)throw new Error('请选择会话和导出格式');
      await request({action:'batch',chats:getChats(),formats:getFormats(),incremental:inc.checked,rebuild:rebuild.checked,remember:remember.checked,manifest:manifest.checked});
    },true);
  }
  function archive(action){
    const titles={archiveCreate:'导出聊天归档',archiveOpen:'打开离线归档',archiveVerify:'校验聊天归档',libraryImport:'归档导入资料库'};
    const body=form(dialog(titles[action]));
    const getChats=action==='archiveCreate'?chats(body):null;
    body.append(element('p','muted',action==='archiveCreate'?'密码留空则不加密；设置密码后至少 8 个字符。':'密码仅用于打开加密归档，不会保存。未加密归档留空即可。'));
    const password=field(body,'归档密码',{type:'password'});
    const confirmation=action==='archiveCreate'?field(body,'再次输入密码',{type:'password'}):null;
    cancel();button(action==='archiveCreate'?'保存归档':'选择归档',async()=>{
      if(getChats && !getChats().length)throw new Error('请选择至少一个会话');
      if(confirmation && password.value!==confirmation.value)throw new Error('两次密码不一致');
      if(confirmation && password.value && password.value.length<8)throw new Error('密码至少 8 个字符');
      const value=password.value;password.value='';if(confirmation)confirmation.value='';
      await request({action,password:value,...(getChats?{chats:getChats()}:{})});
    },true);
  }
  async function library(){
    const body=form(dialog('打开本机资料库'));
    const rows=await api.tool({action:'libraryAccounts'});
    if(!rows.length)body.append(element('p','muted','资料库为空，请先导入聊天归档。'));
    for(const account of rows)button(`${account.label} · ${account.messages.toLocaleString()} 条消息`,async()=>{
      const result=await request({action:'libraryOpen',owner:account.owner});if(result?.state)$('tool-dialog').close();
    },false,body);
    button('关闭',()=>$('tool-dialog').close());
  }
  async function settings(){
    const preferences=await api.tool({action:'settingsGet'});
    const body=form(dialog('软件设置'));
    const getFormats=formatChoices(body,preferences.formats);
    const output=element('p','muted',preferences.last_out||getState()?.out||'未选择默认目录');body.append(output);
    button('选择默认保存目录',async()=>{const path=await api.chooseFolder();if(path)output.textContent=path;},false,body);
    const density=field(body,'消息密度',{value:preferences.density,choices:[['normal','标准'],['compact','紧凑']]}),limit=field(body,'缩略图磁盘上限（MiB，0 表示不保存）',{type:'number',value:preferences.thumbnail_mb});
    limit.min='0';limit.max=String(16*1024);
    cancel();button('保存设置',async()=>{
      const mb=Number(limit.value);if(!Number.isInteger(mb)||mb<0||mb>16384||!getFormats().length)throw new Error('请选择导出格式，缓存上限为 0–16384 的整数');
      const result=await request({action:'settingsSave',formats:getFormats(),density:density.value,thumbnail_mb:mb});
      if(result && !result.canceled){document.body.classList.toggle('compact',result.density==='compact');if(getState())getState().formats=result.formats;refreshMessages(result);$('tool-dialog').close();}
    },true);
  }
  async function missing(offset=0){
    const chat=getCurrent();if(!chat)throw new Error('请先选择一个会话');
    const body=form(dialog('本机缺失媒体'));
    body.append(element('p','muted','先在 QQ 打开对应聊天并下载附件，再重新检测。点击条目跳转到消息。'));
    const result=await api.tool({action:'missing',chat:chat.id,offset});
    for(const item of result.rows)button(`${new Date(item.ts*1000).toLocaleString('zh-CN')} · ${item.kind} · ${item.name||item.id}`,()=>{hooks.see(item.index);$('tool-dialog').close();},false,body);
    if(!result.rows.length)body.append(element('p','muted','本页没有缺失媒体'));
    if(offset)button('上一页',()=>missing(Math.max(0,offset-200)));
    if(result.more)button('下一页',()=>missing(offset+200));
    button('重新检测',async()=>{const result=await api.refresh();if(result)applyState(result,{preserve:true});await missing(0);});
    button('关闭',()=>$('tool-dialog').close());
  }
  function schedule(){
    const body=form(dialog('生成每日导出计划')),getChats=chats(body),getFormats=formatChoices(body);
    const clock=field(body,'每日运行时间',{type:'time',value:'20:00'});
    body.append(element('p','muted','生成默认禁用的 XML 和计划 JSON。请在 Windows 任务计划程序中核对后导入启用。移动软件后需重新生成。'));
    cancel();button('生成计划文件',()=>{if(!getChats().length||!getFormats().length)throw new Error('请选择会话和导出格式');return request({action:'schedule',chats:getChats(),formats:getFormats(),clock:clock.value});},true);
  }
  async function poll(){
    if(polling)return;polling=true;
    try{
      const job=await api.tool({action:'queueState',...(activeJob?{job:activeJob}:{})});
      if(!job){$('job-message').textContent='当前没有任务';$('job-cancel').disabled=true;$('job-retry').disabled=true;return;}
      activeJob=job.id;$('job-title').textContent=job.title;
      setBusy(job.running,job.running?`${job.title}…`:'');
      $('job-cancel').disabled=!job.running;$('job-retry').disabled=job.running||!job.progress?.tasks;
      $('job-message').textContent=job.error||job.result?.message||(job.running?'正在处理，可以取消任务。':'任务已完成');
      const tasks=job.progress?.tasks||job.result?.tasks||[];
      $('job-progress').replaceChildren();
      if(tasks.length){const table=element('table','progress-table');const head=element('tr');for(const title of ['会话','状态','消息数','原因'])head.append(element('th','',title));table.append(head);
        const names=new Map((getState()?.chats||[]).map(chat=>[chat.id,chat.title]));
        for(const task of tasks){const row=element('tr');for(const value of [names.get(task.chat_id)||task.chat_id,({pending:'等待',running:'导出中',done:'成功',failed:'失败',cancelled:'已取消',skipped:'已跳过'})[task.status]||task.status,String(task.messages)+(task.messages_added==null?'':` (+${task.messages_added})`),task.error||''])row.append(element('td','',value));table.append(row);}$('job-progress').append(table);}
      if(!job.running && job.result?.state && lastApplied!==job.id){lastApplied=job.id;applyState(job.result.state);}
      if(job.running)setTimeout(()=>attempt(poll),350);
    }finally{polling=false;}
  }
  $('job-cancel').addEventListener('click',()=>attempt(async()=>{await api.tool({action:'queueCancel'});await poll();}));
  $('job-retry').addEventListener('click',()=>attempt(async()=>{const job=await api.tool({action:'queueRetry',job:activeJob});activeJob=job.id;await poll();}));
  $('job-close').addEventListener('click',()=>$('job-dialog').close());
  async function open(action){
    $('more-dialog').close();
    if(action==='search'){$('search').click();return;}
    if(action==='batch'||action==='incremental'){batch(action==='incremental');return;}
    if(['archiveCreate','archiveOpen','archiveVerify','libraryImport'].includes(action)){archive(action);return;}
    if(action==='libraryOpen'){await library();return;}
    if(action==='settings'){await settings();return;}
    if(action==='missing'){await missing();return;}
    if(action==='schedule'){schedule();return;}
    if(action==='queueState'){if(!$('job-dialog').open)$('job-dialog').showModal();await poll();return;}
    dialog(action==='cacheClear'?'清理图片缓存':action==='libraryClear'?'清空本机资料库':'恢复导出队列');
    cancel();button('继续',async()=>{const result=await request({action});if(action==='cacheClear'&&result&&!result.canceled)clearResources();},true);
  }
  for(const control of document.querySelectorAll('[data-tool]'))control.addEventListener('click',()=>attempt(()=>open(control.dataset.tool)));
  return { showJob:async job=>{activeJob=job.id;$('job-dialog').showModal();await poll();},
    exportSearch:query=>{const body=form(dialog('导出全部匹配结果')),getFormats=formatChoices(body);cancel();button('开始导出',()=>request({action:'searchExport',query,formats:getFormats()}),true);} };
}
