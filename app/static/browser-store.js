'use strict';
// Durable data stay in this browser. The VPS has only an expiring RAM copy.
window.KadrStorage = (() => {
  let db=null, enabled=false, token=null, etag=null;
  const TOKEN_KEY='kadr.browser-session.v1';
  function localError(){return Error('Не удалось сохранить кабинет в браузере. Разрешите хранение данных сайта и не закрывайте вкладку до повторной попытки.');}
  function read(key){return new Promise((resolve,reject)=>{const tx=db.transaction('workspace','readonly');const request=tx.objectStore('workspace').get(key);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(localError());});}
  function write(key,value){return new Promise((resolve,reject)=>{const tx=db.transaction('workspace','readwrite');tx.objectStore('workspace').put(value,key);tx.oncomplete=resolve;tx.onerror=tx.onabort=()=>reject(localError());});}
  function open(){return new Promise((resolve,reject)=>{const request=indexedDB.open('kadr.workspace.v1',1);request.onupgradeneeded=()=>request.result.createObjectStore('workspace');request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(localError());request.onblocked=()=>reject(Error('Закройте другие старые вкладки Кадра и повторите вход.'));});}
  function headers(extra={}){const h=new Headers(extra);if(token)h.set('X-Workspace-Session',token);return h;}
  async function lock(action){return navigator.locks.request('kadr.workspace.v1',action);}
  async function connect(){
    const saved=await read('snapshot');
    const r=await fetch('/api/portal/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:localStorage.getItem(TOKEN_KEY),snapshot:saved||null})});
    if(!r.ok){const error=await r.json();throw Error(error.detail||'Не удалось открыть кабинет.');}
    token=(await r.json()).token;localStorage.setItem(TOKEN_KEY,token);etag=null;
  }
  async function checkpoint(){
    const h=headers();if(etag)h.set('If-None-Match',etag);
    const r=await fetch('/api/portal/session/snapshot',{headers:h});
    if(r.status===304)return;
    if(!r.ok)throw Error('Изменение ещё не сохранено в браузере. Не закрывайте вкладку и повторите подключение.');
    const value=await r.json();await write('snapshot',value);etag=r.headers.get('ETag');
  }
  // Draft transactions start immediately on input; closing a tab does not
  // depend on a final network request completing.
  function stageDraft(id,data){
    if(!enabled||!db||!id)return;
    const tx=db.transaction('workspace','readwrite');tx.objectStore('workspace').put(data,'draft:'+id);
    tx.onerror=tx.onabort=()=>window.dispatchEvent(new CustomEvent('kadr-storage-error',{detail:localError().message}));
  }
  async function pendingDrafts(){return new Promise((resolve,reject)=>{const result=[];const tx=db.transaction('workspace','readonly');const cursor=tx.objectStore('workspace').openCursor();cursor.onsuccess=()=>{const c=cursor.result;if(!c){resolve(result);return;}if(String(c.key).startsWith('draft:'))result.push([c.key,c.value]);c.continue();};cursor.onerror=()=>reject(localError());});}
  async function flushDrafts(){
    const entries=await pendingDrafts();if(!entries.length)return;
    for(const [key,value] of entries){const r=await fetch('/api/portal/chats/'+String(key).slice(6)+'/draft',{method:'PUT',headers:headers({'Content-Type':'application/json'}),body:JSON.stringify(value)});if(!r.ok&&r.status!==404)throw Error('Не удалось восстановить черновик. Повторите подключение.');}
    await checkpoint();
    // Compare values inside one transaction: never erase a newer keystroke.
    await new Promise((resolve,reject)=>{const tx=db.transaction('workspace','readwrite');const store=tx.objectStore('workspace');for(const [key,value] of entries){const r=store.get(key);r.onsuccess=()=>{if(JSON.stringify(r.result)===JSON.stringify(value))store.delete(key);};}tx.oncomplete=resolve;tx.onerror=tx.onabort=()=>reject(localError());});
  }
  async function request(url,options={}){
    if(!enabled)return fetch(url,options);
    return lock(async()=>{
      const stored=localStorage.getItem(TOKEN_KEY);if(stored!==token){token=stored;etag=null;}
      if(!token)await connect();
      // Detect an expired server copy before replaying any staged input.
      let probe=await fetch('/api/portal/session/snapshot',{headers:headers(etag?{'If-None-Match':etag}:{})});
      if(probe.status===401)await connect();
      else if(!probe.ok&&probe.status!==304)throw Error('Не удалось восстановить кабинет. Повторите подключение.');
      else if(probe.ok){await write('snapshot',await probe.json());etag=probe.headers.get('ETag');}
      // Flush the latest browser draft before an action can replace it.
      await flushDrafts();
      let r=await fetch(url,{...options,headers:headers(options.headers)});
      if(r.status===401){await connect();await flushDrafts();r=await fetch(url,{...options,headers:headers(options.headers)});}
      if(r.ok)await checkpoint();
      return r;
    });
  }
  async function init(){
    const r=await fetch('/api/portal/session/config');if(!r.ok)throw Error('Не удалось проверить хранение кабинета.');
    enabled=(await r.json()).browser_storage;if(!enabled)return;
    if(!window.indexedDB||!navigator.locks)throw Error('Откройте Кадр в современном браузере по HTTPS, чтобы сохранять свой кабинет.');
    db=await open();
    await lock(async()=>{await connect();await flushDrafts();await checkpoint();});
    navigator.storage?.persist?.().catch(()=>{});
  }
  async function download(path){
    const r=await request(path);if(!r.ok){let message='Не удалось скачать документ.';try{message=(await r.json()).detail||message;}catch{}throw Error(message);}
    const blob=await r.blob();const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;
    const name=r.headers.get('Content-Disposition')||'';let filename=name.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    if(filename){try{filename=decodeURIComponent(filename);}catch{filename=null;}}
    link.download=filename||name.match(/filename="([^"]+)"/)?.[1]||'Кадр';link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }
  async function messageDrafts(){return enabled?(await read('message-drafts'))||{}:{};}
  function saveMessageDraft(key,value){
    if(!enabled||!db)return;
    const tx=db.transaction('workspace','readwrite');const store=tx.objectStore('workspace');const r=store.get('message-drafts');r.onsuccess=()=>store.put({...r.result,[key]:value},'message-drafts');
    tx.onerror=tx.onabort=()=>window.dispatchEvent(new CustomEvent('kadr-storage-error',{detail:localError().message}));
  }
  return {init,request,stageDraft,download,messageDrafts,saveMessageDraft,active:()=>enabled};
})();
