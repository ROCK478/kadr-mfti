'use strict';
// No browser speech recognition/synthesis: only the same local application receives audio/text.
window.TaktSpeech = (() => {
  const micIcon = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></svg>';
  let options, available = {stt:false,tts:false}, checked = false;
  let capture = null, phase = 'idle', note = '', failed = false;
  let playback = null, playIndex = null, playPhase = 'idle', audioUrl = null, playRequest = null;
  const $ = id => document.getElementById(id);
  const active = () => phase !== 'idle';
  function setNote(text, error=false) { note=text;failed=error;refresh(); }
  async function errorText(response) {
    try { const data=await response.json();return typeof data.detail==='string'?data.detail:'Не удалось обработать речь. Попробуйте ещё раз.'; }
    catch { return 'Не удалось обработать речь. Попробуйте ещё раз.'; }
  }
  function refresh() {
    const button=$('chat-mic');
    if (button) {
      button.disabled=options.busy() || phase==='recognizing' || (phase==='idle' && (!available.stt || !navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode));
      const recording=phase==='recording', pending=phase==='permission';
      button.setAttribute('aria-label',recording?'Завершить запись':pending?'Отменить включение микрофона':'Продиктовать вопрос');
      button.title=button.getAttribute('aria-label');button.setAttribute('aria-pressed',String(recording));
      button.classList.toggle('recording',recording);
      button.innerHTML=recording?'<span class="record-stop"></span>':micIcon;
    }
    const cancel=$('chat-record-cancel');if(cancel)cancel.hidden=!active();
    const input=$('chat-input');if(input)input.readOnly=active();
    const send=$('chat-send');if(send)send.disabled=options.busy()||active();
    const status=$('speech-status');
    if(status){status.textContent=note;status.classList.toggle('speech-error',failed);status.closest('.speech-controls').hidden=!note&&!active();}
    document.querySelectorAll('[data-speak-answer]').forEach(button=>{
      const current=Number(button.dataset.speakAnswer)===playIndex;
      button.disabled=!available.tts;
      button.textContent=current?(playPhase==='loading'?'Отменить озвучивание…':playPhase==='paused'?'Продолжить':'Пауза'):'Прослушать';
      button.setAttribute('aria-label',current?button.textContent:'Прослушать ответ');
      button.setAttribute('aria-pressed',String(current));
    });
    document.querySelectorAll('[data-stop-speaking]').forEach(button=>{button.hidden=Number(button.dataset.stopSpeaking)!==playIndex;});
  }
  async function init(config) {
    options=config;
    try {const response=await fetch('/api/portal/speech');if(response.ok)available=await response.json();}
    catch { /* Text chat remains available when speech setup cannot be checked. */ }
    checked=true;refresh();
  }
  function releaseCapture(item) {
    clearTimeout(item.timer);item.stream?.getTracks().forEach(track=>track.stop());
    item.source?.disconnect();item.node?.disconnect();
    if(item.context?.state!=='closed')item.context?.close().catch(()=>{});
  }
  function cancelCapture(clearNote=true) {
    const item=capture;capture=null;phase='idle';
    if(item){item.cancelled=true;item.request?.abort();releaseCapture(item);}
    if(clearNote){note='Запись отменена. Текст вопроса сохранён.';failed=false;}
    refresh();
  }
  async function startCapture() {
    if(active()||options.busy()||!available.stt)return;
    stopPlayback();
    const item={chunks:[],cancelled:false,original:options.draft()};capture=item;
    phase='permission';setNote('Разрешите микрофон в браузере. Запись начнётся после разрешения.');
    try {
      item.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
      if(item.cancelled){releaseCapture(item);return;}
      item.context=new AudioContext({sampleRate:16000});
      await item.context.audioWorklet.addModule('/static/recorder-worklet.js');
      await item.context.resume();
      if(item.cancelled){releaseCapture(item);return;}
      item.node=new AudioWorkletNode(item.context,'takt-recorder');
      item.node.port.onmessage=event=>{
        if(event.data==='flushed'){item.flushed?.();return;}
        if(!item.cancelled)item.chunks.push(event.data);
      };
      item.source=item.context.createMediaStreamSource(item.stream);
      item.source.connect(item.node);item.node.connect(item.context.destination);
      item.stream.getAudioTracks().forEach(track=>{track.onended=()=>{if(capture===item&&phase==='recording')finishCapture();};});
      phase='recording';setNote('Слушаю… Нажмите квадрат, когда закончите. До 60 секунд.');
      item.timer=setTimeout(finishCapture,60000);
    } catch(error) {
      if(item.cancelled)return;
      cancelCapture(false);
      setNote(error.name==='NotAllowedError'?'Нет доступа к микрофону. Разрешите его для этого сайта или введите вопрос текстом.':error.name==='NotFoundError'?'Микрофон не найден. Подключите его или введите вопрос текстом.':'Не удалось включить микрофон. Проверьте его и попробуйте ещё раз.',true);
    }
  }
  function wav(chunks, rate) {
    const length=chunks.reduce((sum,c)=>sum+c.length,0), samples=new Float32Array(length);
    let offset=0;for(const chunk of chunks){samples.set(chunk,offset);offset+=chunk.length;}
    const count=Math.min(60*16000,Math.floor(length*16000/rate)), buffer=new ArrayBuffer(44+count*2), view=new DataView(buffer);
    const string=(at,text)=>{for(let i=0;i<text.length;i++)view.setUint8(at+i,text.charCodeAt(i));};
    string(0,'RIFF');view.setUint32(4,36+count*2,true);string(8,'WAVE');string(12,'fmt ');view.setUint32(16,16,true);
    view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);string(36,'data');view.setUint32(40,count*2,true);
    for(let i=0;i<count;i++){
      const from=Math.floor(i*rate/16000),to=Math.max(from+1,Math.floor((i+1)*rate/16000));let sum=0;
      for(let j=from;j<Math.min(to,length);j++)sum+=samples[j];
      const value=Math.max(-1,Math.min(1,sum/(to-from)));view.setInt16(44+i*2,value<0?value*32768:value*32767,true);
    }
    return new Blob([buffer],{type:'audio/wav'});
  }
  async function finishCapture() {
    const item=capture;if(!item||phase!=='recording')return;
    phase='recognizing';clearTimeout(item.timer);setNote('Распознаю речь на этом компьютере…');
    item.stream.getTracks().forEach(track=>track.stop());
    await new Promise(resolve=>{const timeout=setTimeout(resolve,250);item.flushed=()=>{clearTimeout(timeout);resolve();};item.node.port.postMessage('flush');});
    const audio=wav(item.chunks,item.context.sampleRate);releaseCapture(item);item.chunks=[];
    if(item.cancelled)return;
    try {
      item.request=new AbortController();const form=new FormData();form.append('file',audio,'question.wav');
      const response=await fetch('/api/portal/speech/transcribe',{method:'POST',body:form,signal:item.request.signal});
      if(!response.ok)throw new Error(await errorText(response));
      const result=await response.json();if(item.cancelled)return;
      const combined=(item.original.trim()?item.original.trim()+' ':'')+result.text;
      if(combined.length>2000)throw new Error('Вопрос получился длиннее 2000 символов. Сократите текст или продиктуйте более короткий запрос.');
      options.setDraft(combined);const input=$('chat-input');if(input){input.value=combined;input.focus();}
      capture=null;phase='idle';setNote('Готово. Проверьте текст и нажмите «Отправить».');
    }catch(error){if(item.cancelled)return;capture=null;phase='idle';setNote(error.message==='Failed to fetch'?'Связь с приложением прервалась. Попробуйте записать вопрос ещё раз.':error.message,true);}
  }
  function stopPlayback() {
    playRequest?.abort();playRequest=null;playback?.pause();playback=null;
    if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=null;playIndex=null;playPhase='idle';refresh();
  }
  async function speak(index) {
    if(!available.tts)return;
    if(index===playIndex){
      if(playPhase==='loading'){stopPlayback();return;}
      if(playPhase==='paused'){try{await playback.play();playPhase='playing';}catch{setNote('Нажмите «Прослушать», чтобы включить звук.',true);stopPlayback();}}
      else{playback.pause();playPhase='paused';}refresh();return;
    }
    if(active())cancelCapture();stopPlayback();
    const text=options.answerText(index);if(!text)return;
    const controller=new AbortController();playRequest=controller;playIndex=index;playPhase='loading';refresh();
    try{
      const response=await fetch('/api/portal/speech/synthesize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text}),signal:controller.signal});
      if(!response.ok)throw new Error(await errorText(response));
      const blob=await response.blob();if(controller.signal.aborted)return;
      audioUrl=URL.createObjectURL(blob);playback=new Audio(audioUrl);
      playback.onended=()=>stopPlayback();playback.onerror=()=>{setNote('Не удалось воспроизвести звук. Попробуйте ещё раз.',true);stopPlayback();};
      await playback.play();if(controller.signal.aborted)return;playPhase='playing';refresh();
    }catch(error){if(controller.signal.aborted)return;stopPlayback();setNote(error.message==='Failed to fetch'?'Связь с приложением прервалась. Повторите озвучивание.':error.message,true);}
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.id==='chat-mic'){if(phase==='recording')finishCapture();else if(phase==='permission')cancelCapture();else startCapture();}
    if(button.id==='chat-record-cancel')cancelCapture();
    if(button.hasAttribute('data-speak-answer'))speak(Number(button.dataset.speakAnswer));
    if(button.hasAttribute('data-stop-speaking'))stopPlayback();
  });
  function leave(){if(active())cancelCapture();stopPlayback();}
  window.addEventListener('pagehide',leave);
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&active())cancelCapture();});
  return {init,refresh,active,leave};
})();
