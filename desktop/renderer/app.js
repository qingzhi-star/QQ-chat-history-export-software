import { MessageList } from './virtual.js';
const $ = id => document.getElementById(id);
const api = window.qilu;
let state = null, current = null, selecting = false, allSelected = false;
let selected = new Set(), excluded = new Set(), dates = null, loading = false, refreshing = false;
let toastTimer, chatFrame;
const resourceCache = new Map(), resourceQueue = [];
let resourceActive = 0;
const formats = new Set(['html']);

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function toast(error) {
  $('toast').textContent = error.message || String(error);
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 6000);
}
async function run(action) { try { return await action(); } catch (error) { toast(error); } }
function busy(value, text = '') {
  loading = value;
  $('switch-account').disabled = value;
  $('reload').disabled = value;
  $('begin-select').disabled = value || !current;
  $('status').textContent = text;
}
function resource(token) {
  if (!token) return Promise.resolve(null);
  if (token.startsWith('data:image/')) return Promise.resolve(token);
  if (resourceCache.has(token)) {
    const value = resourceCache.get(token);
    resourceCache.delete(token); resourceCache.set(token, value);
    return value;
  }
  const promise = new Promise(resolve => { resourceQueue.push({ token, resolve }); pump(); });
  resourceCache.set(token, promise);
  while (resourceCache.size > 300) resourceCache.delete(resourceCache.keys().next().value);
  return promise;
}
function pump() {
  while (resourceActive < 6 && resourceQueue.length) {
    const { token, resolve } = resourceQueue.shift();
    resourceActive++;
    api.media({ token }).then(resolve, () => resolve(null)).finally(() => { resourceActive--; pump(); });
  }
}
function avatar(name, token, extra = '') {
  const container = node('div', `avatar ${extra}`, (name || '绮')[0]);
  resource(token).then(uri => {
    if (!uri) return;
    const image = node('img'); image.src = uri; image.alt = '';
    image.addEventListener('load', () => container.replaceChildren(image));
  });
  return container;
}
function shortTime(ts) {
  if (!ts) return '';
  const date = new Date(ts * 1000), today = new Date();
  return date.toDateString() === today.toDateString() ? date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : `${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}
function fullTime(ts) { return new Date(ts*1000).toLocaleString('zh-CN', { hour12: false }); }
function formatTime(ts) { return new Date(ts*1000).toLocaleTimeString('zh-CN', { hour12: false }); }
function selectionCount() { return allSelected ? Math.max(0,(current?.count || 0)-excluded.size) : selected.size; }
function isSelected(index) { return allSelected ? !excluded.has(index) : selected.has(index); }
function updateSelection() {
  $('selection-count').textContent = `已选择 ${selectionCount().toLocaleString()} / ${(current?.count || 0).toLocaleString()} 条`;
  $('select-all').textContent = allSelected && !excluded.size ? '全不选' : '全选';
  $('next').disabled = selectionCount() === 0;
  list.invalidate();
}
function toggleMessage(index) {
  const set = allSelected ? excluded : selected;
  set.has(index) ? set.delete(index) : set.add(index);
  updateSelection();
}

const list = new MessageList($('messages'), $('message-space'), (offset, size) => api.messages({ chat: current.id, offset, size }), renderMessage);
list.onError = toast;

function quoteSummary(message) {
  if (!message) return '引用正文未保存在本机';
  return message.segments.map(segment => segment.kind === 'image' ? '[图片]' : segment.kind === 'face' ? '[表情]' : segment.kind === 'file' ? `[文件]${segment.filename}` : segment.text || '').join('').trim() || '引用正文未保存在本机';
}
function appendSegments(container, segments, rowIndex) {
  for (const segment of segments) {
    if (segment.kind === 'reply') {
      const card = node('div', 'quote');
      const header = node('div', 'quote-header');
      header.append(node('span', '', segment.quoted?.name || '引用消息'), node('span', '', '↑'));
      card.append(header, node('div', 'quote-text', quoteSummary(segment.quoted)));
      container.append(card);
      if (segment.text) container.append(document.createTextNode(segment.text));
    } else if (segment.kind === 'face') {
      const placeholder = node('span', 'emoji-placeholder', '[表情]');
      container.append(placeholder);
      resource(segment.resource).then(uri => {
        if (!uri || !placeholder.isConnected) return;
        const image = node('img', 'emoji'); image.src = uri; image.alt = '[表情]';
        image.addEventListener('load', () => placeholder.replaceWith(image));
      });
    } else if (segment.kind === 'image') {
      if (segment.missing || !segment.resource) {
        container.append(node('span', 'missing-media', '[图片](本机未缓存)'));
        continue;
      }
      const placeholder = node('span', 'picture-loading', '图片加载中…');
      container.append(placeholder);
      resource(segment.resource).then(uri => {
        if (!placeholder.isConnected) return;
        if (!uri) { placeholder.className = 'missing-media'; placeholder.textContent = '[图片](本机未缓存)'; return; }
        const image = node('img', 'chat-picture'); image.alt = '';
        image.addEventListener('load', () => placeholder.replaceWith(image));
        image.addEventListener('error', () => { placeholder.className = 'missing-media'; placeholder.textContent = '[图片](本机未缓存)'; });
        image.addEventListener('click', event => {
          if (!selecting) { event.stopPropagation(); run(() => api.openMedia({ token: segment.resource })); }
        });
        image.src = uri;
      });
    } else if (segment.kind === 'file') {
      const file = node('span', 'file-link', `[文件]${segment.filename || segment.text || '附件'}${segment.missing ? '(本机未缓存)' : ''}`);
      file.addEventListener('click', event => {
        if (!selecting && segment.resource) { event.stopPropagation(); run(() => api.openMedia({ token: segment.resource })); }
      });
      container.append(file);
    } else if (segment.text) {
      container.append(document.createTextNode(segment.text));
    }
  }
}
function renderMessage(message, index) {
  const article = node('article', `message${selecting ? ' selecting' : ''}${isSelected(index) && selecting ? ' selected' : ''}${message?.self ? ' own' : ''}${message?.segments.some(s => s.kind === 'reply') ? ' has-quote' : ''}`);
  if (!message) { article.append(node('span', 'muted', '正在加载…')); return article; }
  if(message.day){article.classList.add("new-day");article.append(node("div","day-marker",message.day));}
  article.addEventListener('click', () => { if (selecting) toggleMessage(index); });
  article.append(node('span', 'row-check', isSelected(index) ? '✓' : ''));
  if (message.recall) { article.append(node('div', 'recall', quoteSummary(message))); return article; }
  article.append(avatar(message.name, message.avatar));
  const main = node('div', 'message-main');
  const header = node('div', 'message-heading', message.name);
  const time = node('time', '', formatTime(message.ts)); time.title = fullTime(message.ts);
  header.append(time);
  const body = node('div', 'message-body');
  appendSegments(body, message.segments, index);
  main.append(header, body); article.append(main);
  return article;
}

const chatSpace = node('div', 'chat-space'); $('chats').append(chatSpace);
const chatNodes = new Map();
function drawChats() {
  chatFrame = null;
  const chats = state?.chats || [], height = 94;
  chatSpace.style.height = `${chats.length*height}px`;
  const first = Math.max(0, Math.floor($('chats').scrollTop/height)-2);
  const end = Math.min(chats.length, first+Math.ceil($('chats').clientHeight/height)+5);
  const keep = new Set();
  for (let i=first;i<end;i++) {
    const chat = chats[i]; keep.add(chat.id);
    let item = chatNodes.get(chat.id);
    if (!item || item.dataset.revision !== `${chat.title}:${chat.preview}:${chat.ts}:${chat.avatar}`) {
      item?.remove();
      item = node('button','chat'); item.dataset.chat = chat.id;
      item.dataset.revision = `${chat.title}:${chat.preview}:${chat.ts}:${chat.avatar}`;
      item.append(avatar(chat.title,chat.avatar));
      const content = node('div','chat-content'), top = node('div','chat-top');
      const title = node('span','chat-title',chat.title); title.title = chat.title;
      top.append(title,node('time','',shortTime(chat.ts)));
      content.append(top,node('div','chat-preview',chat.preview || '（无消息）')); item.append(content);
      item.addEventListener('click',()=>run(()=>openChat(chat)));
      chatSpace.append(item); chatNodes.set(chat.id,item);
    }
    item.style.transform = `translateY(${i*height}px)`;
    item.classList.toggle('selected',chat.id===current?.id);
  }
  for (const [id,item] of chatNodes) if (!keep.has(id)) { item.remove(); chatNodes.delete(id); }
}
function scheduleChats() { if (!chatFrame) chatFrame=requestAnimationFrame(drawChats); }
$('chats').addEventListener('scroll',scheduleChats,{passive:true});
new ResizeObserver(scheduleChats).observe($('chats'));

function applyState(next, { preserve = false } = {}) {
  if(state && next.generation<state.generation)return;
  const previous = current, anchor = list.anchor(), follow = list.follow;
  state = { ...state, ...next };
  const account = state.account;
  $('account-name').textContent = account.name;
  $('account-uin').textContent = account.uin ? `QQ ${account.uin}` : '点击右侧切换账号';
  $('account-avatar').replaceWith(Object.assign(avatar(account.name,account.avatar,'account-avatar'),{id:'account-avatar'}));
  current = preserve ? state.chats.find(chat=>chat.id===previous?.id) || null : null;
  if (!current) { list.reset(0); $('welcome').hidden=false; $('chat-title').textContent='把对话，收进绮录'; }
  else { $('chat-title').textContent=current.title; list.reset(current.count,{bottom:follow,anchor,preserve:true}); }
  if (!preserve) $('chats').scrollTop=0;
  scheduleChats(); busy(false,'');
}
async function boot() {
  if (loading) return;
  busy(true,'正在识别本机账号…');
  try {
    const result = await api.boot(); exitSelection(); applyState(result);
    $('version').textContent = `绮录 ${result.version} · Electron`;
    formats.clear(); for (const format of result.formats) formats.add(format);
    $('output-folder').textContent = result.out;
    if (!state.account.uin) showAccounts();
  } catch (error) { busy(false,'未能打开本机资料，点击左侧 ↻ 重试'); throw error; }
}
function showAccounts() {
  const menu=$('accounts'); menu.replaceChildren();
  for (const identity of state?.accounts || []) {
    const item=node('button'); item.append(avatar(identity.name,identity.avatar));
    const text=node('span','identity'); text.append(node('strong','',identity.name),node('small','',`QQ ${identity.uin}`));
    item.append(text); if(identity.uin===state.account.uin)item.append(node('em','','当前'));
    item.addEventListener('click',()=>run(async()=>{
      if(loading)return;
      menu.hidden=true; busy(true,'正在自动准备账号…');
      try { const result=await api.switch({uin:identity.uin}); exitSelection(); applyState(result); }
      catch(error){busy(false,'切换失败，当前账号已保留');throw error;}
    }));menu.append(item);
  }
  if(!menu.childElementCount)menu.append(node('p','muted','未发现本机账号，请登录 QQ 后重新检测。'));
  menu.hidden=false;
}
async function openChat(chat) {
  if(loading || current?.id===chat.id)return;
  exitSelection(); current=chat;
  $('chat-title').textContent=chat.title; $('welcome').hidden=true;
  list.reset(chat.count); $('begin-select').disabled=false; scheduleChats();
}
function exitSelection() {
  selecting=false; selected.clear(); excluded.clear(); allSelected=false; dates=null;
  $('selection-actions').hidden=true; $('normal-actions').hidden=false; $('status').hidden=false;
  list.invalidate();
}
function startSelection() {
  if(!current)return;
  selecting=true; $('selection-actions').hidden=false; $('normal-actions').hidden=true; $('status').hidden=true;
  updateSelection();
}
$('switch-account').addEventListener('click',()=>{$('accounts').hidden ? showAccounts() : $('accounts').hidden=true;});
document.addEventListener('click',event=>{if(!event.target.closest('.account-menu,.account-card'))$('accounts').hidden=true;});
$('reload').addEventListener('click',()=>run(boot));
$('begin-select').addEventListener('click',startSelection);
$('cancel-select').addEventListener('click',exitSelection);
$('select-all').addEventListener('click',()=>{allSelected=!allSelected;selected.clear();excluded.clear();dates=null;$('dates').textContent='时间选择';updateSelection();});
$('dates').addEventListener('click',()=>$('date-dialog').showModal());
$('cancel-dates').addEventListener('click',()=>$('date-dialog').close());
$('apply-dates').addEventListener('click',()=>run(async()=>{
  const start=$('date-start').value, end=$('date-end').value;
  if(!start || !end || start>end)throw new Error('请选择有效的起止日期');
  const startTs=Math.floor(new Date(`${start}T00:00:00`).getTime()/1000), endTs=Math.floor(new Date(`${end}T23:59:59`).getTime()/1000);
  const indices=await api.range({chat:current.id,start:startTs,end:endTs});
  selected=new Set(indices);excluded.clear();allSelected=false;dates={start:startTs,end:endTs};
  $('dates').textContent=`${start.slice(5)} — ${end.slice(5)}`;$('date-dialog').close();updateSelection();
}));
$('next').addEventListener('click',()=>{
  $('export-summary').textContent=`${current.title} · 已选择 ${selectionCount().toLocaleString()} 条消息`;
  for(const checkbox of document.querySelectorAll('.format-card input'))checkbox.checked=formats.has(checkbox.value);
  $('export-progress').textContent='';$('do-export').disabled=false;$('export-dialog').showModal();
});
$('choose-folder').addEventListener('click',()=>run(async()=>{const path=await api.chooseFolder();if(path)$('output-folder').textContent=path;}));
$('cancel-export').addEventListener('click',()=>$('export-dialog').close());
$('do-export').addEventListener('click',()=>run(async()=>{
  const choices=[...document.querySelectorAll('.format-card input:checked')].map(input=>input.value);
  if(!choices.length)throw new Error('请至少选择一种导出格式');
  $('do-export').disabled=true; $('cancel-export').disabled=true;$('export-progress').textContent='正在整理与导出，请稍候…';
  try {
    const result=await api.export({chat:current.id,formats:choices,selected:[...selected],all_messages:allSelected,excluded:[...excluded],...(dates||{})});
    if(result.canceled){$('export-progress').textContent='已取消选择保存位置';return;}
    formats.clear();choices.forEach(format=>formats.add(format));
    $('export-progress').textContent=`已导出 ${result.messages} 条消息。${result.failed ? `${result.failed} 条失败。` : ''}${result.pdf_error || ''}`;
  } finally { $('do-export').disabled=false; $('cancel-export').disabled=false; }
}));
$('search').addEventListener('click',()=>{$('search-dialog').showModal();$('search-query').focus();});
$('close-search').addEventListener('click',()=>$('search-dialog').close());
async function search(){
  const query=$('search-query').value.trim();if(!query)return;
  $('search-results').replaceChildren(node('p','muted','正在搜索本机记录…'));
  const hits=await api.search({query,chat:current?.id || null});
  $('search-results').replaceChildren();
  for(const hit of hits){const item=node('button','search-hit',`${hit.name} · ${fullTime(hit.ts)}\n${hit.text}`);item.addEventListener('click',()=>run(async()=>{await openChat(state.chats.find(chat=>chat.id===hit.chat));list.see(hit.index);$('search-dialog').close();}));$('search-results').append(item);}
  if(!hits.length)$('search-results').append(node('p','muted','没有找到匹配的消息'));
}
$('run-search').addEventListener('click',()=>run(search));
$('search-query').addEventListener('keydown',event=>{if(event.key==='Enter')run(search);});
$('more').addEventListener('click',()=>$('more-dialog').showModal());
$('close-more').addEventListener('click',()=>$('more-dialog').close());
$('classic').addEventListener('click',()=>run(()=>api.classic()));
$('open-output').addEventListener('click',()=>run(()=>api.openOutput()));
for(const button of document.querySelectorAll('[data-window]'))button.addEventListener('click',()=>api.window(button.dataset.window));
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!document.querySelector('dialog[open]'))exitSelection();if(event.ctrlKey&&event.key==='f'){event.preventDefault();$('search').click();}});
setInterval(()=>run(async()=>{
  if(loading || refreshing || selecting || document.querySelector('dialog[open]'))return;
  refreshing=true;
  try {const result=await api.refresh();if(result)applyState(result,{preserve:true});}finally{refreshing=false;}
}),2000);
run(boot);
