(() => {
  const $=id=>document.getElementById(id), params=new URLSearchParams(location.search);
  let record=JSON.parse($('initial-data').textContent), index=Math.max(0,Math.min(record.snapshots.length-1,Number(params.get('ply'))||0)), playing=false, live=false,timer=null;
  const channel=typeof BroadcastChannel==='function'?new BroadcastChannel('amphoreus:'+location.pathname.replace(/[^/]*$/,'')):null;
  function state(){return {type:'arena:state',record,index,live};}
  function send(){const data=state();$('feed').contentWindow?.postMessage(data,location.protocol==='file:'?'*':location.origin);channel?.postMessage(data);}
  function draw(){
    const snapshot=record.snapshots[index],last=record.turns.find(t=>t.ply===snapshot?.ply);
    $('seek').max=Math.max(0,record.snapshots.length-1);$('seek').value=index;$('position').textContent=`${snapshot?.ply||0} / ${record.snapshots.at(-1)?.ply||0}`;
    $('play').textContent=playing?'暂停':'播放';$('live').classList.toggle('active',live);$('prev').disabled=index===0;$('next').disabled=index===record.snapshots.length-1;
    $('status').textContent=live?'跟随最新局面':playing?'回放播放中':'回放已暂停';$('fen').textContent=snapshot?.state.fen||JSON.stringify(snapshot?.state,null,2)||'';$('explanation').textContent=last?.explanation||'本手暂无落子说明';send();
  }
  function stop(){clearTimeout(timer);playing=false;}
  function schedule(){clearTimeout(timer);if(!playing)return;timer=setTimeout(()=>{if(index<record.snapshots.length-1){index++;draw();schedule();}else{stop();draw();}},Number($('speed').value));}
  function seek(value){stop();live=false;index=Math.max(0,Math.min(record.snapshots.length-1,value));draw();}
  $('start').onclick=()=>seek(0);$('prev').onclick=()=>seek(index-1);$('next').onclick=()=>seek(index+1);$('seek').oninput=event=>seek(Number(event.target.value));
  $('play').onclick=()=>{if(playing)stop();else{live=false;if(index===record.snapshots.length-1)index=0;playing=true;schedule();}draw();};
  $('speed').onchange=schedule;$('live').onclick=()=>{stop();live=true;index=record.snapshots.length-1;draw();poll();};
  $('feed').onload=send;window.addEventListener('message',e=>{if(e.source===$('feed').contentWindow&&e.data?.type==='arena:ready')send();});
  if(channel)channel.onmessage=e=>{if(e.data?.type==='arena:ready')send();};
  window.addEventListener('keydown',e=>{if(['INPUT','SELECT','BUTTON','TEXTAREA'].includes(e.target.tagName))return;if(e.code==='Space'){e.preventDefault();$('play').click();}else if(e.code==='ArrowRight')seek(index+1);else if(e.code==='ArrowLeft')seek(index-1);});
  async function poll(){if(location.protocol==='file:')return;try{const response=await fetch('match.json',{cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);const value=await response.json();if(value.updated_at!==record.updated_at||value.turns.length!==record.turns.length||value.status!==record.status){record=value;if(live)index=record.snapshots.length-1;draw();}$('status').classList.remove('error');}catch{$('status').textContent='数据连接中断，保留当前画面并重试';$('status').classList.add('error');}}
  window.replay={seek,getState:()=>({index,live,playing}),setRecord:value=>{record=value;index=Math.min(index,record.snapshots.length-1);draw();}};
  draw();setInterval(poll,1000);
})();
