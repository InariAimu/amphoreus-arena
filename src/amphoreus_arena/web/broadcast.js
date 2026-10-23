(() => {
  const $ = id => document.getElementById(id), params=new URLSearchParams(location.search);
  let record=JSON.parse($('initial-data').textContent), index=0, live=!params.has('ply'), renderer, playerIds=[], lastBoardKey='';
  const controlled=params.has('embedded')||params.get('follow')==='replay';
  if(controlled)live=false;
  const channelName='amphoreus:'+location.pathname.replace(/[^/]*$/,'');
  const channel=typeof BroadcastChannel==='function'?new BroadcastChannel(channelName):null;
  const text=(node,value)=>{node.textContent=value;};
  const time=seconds=>{const value=Math.max(0,Math.floor(seconds||0));return `${String(Math.floor(value/60)).padStart(2,'0')}:${String(value%60).padStart(2,'0')}`;};
  const count=value=>Number.isFinite(value)?Math.round(value).toLocaleString('en-US'):'—';
  const sideName=id=>record.presentation?.players?.[id]?.side_name||renderer?.sideNames?.[id]||ArenaPresentation.sideNames[id]||id;
  function presentation(id) {
    const p=record.players[id];
    return {name:p.kind,avatar:'',accent:'#a1f1da',harness:p.kind.toUpperCase(),...(ArenaPresentation[p.kind]||{}),...record.presentation?.players?.[id]};
  }
  function configure() {
    renderer=ArenaRenderers[record.game_id]||{title:record.game_id,subtitle:'双人对局',note:'',formatAction:t=>JSON.stringify(t.action),render:(container,snapshot)=>{const img=document.createElement('img');img.className='fallback-frame';img.src=snapshot.frame;img.alt='游戏快照';container.replaceChildren(img);}};
    text($('game-title'),renderer.title);text($('match-title'),record.presentation?.title||renderer.subtitle);text($('game-note'),renderer.note);
    $('stage').dataset.game=record.game_id;
    playerIds=Object.keys(record.players).slice(0,2);$('players').replaceChildren();
    playerIds.forEach((id,i)=>{
      const p=presentation(id), card=document.createElement('article');card.className='player-card';card.id=`player-${i}`;card.dataset.seat=String(i+1).padStart(2,'0');card.style.setProperty('--accent',p.accent);
      // Fixed structure only; every model- or user-supplied string uses textContent.
      card.innerHTML='<div class="bubble empty"><div class="bubble-label"><span>思路摘要</span><span class="bubble-ply"></span></div><p></p></div><div class="avatar-wrap"></div><div class="player-info"><div class="identity"><span class="seat"></span><strong class="player-name"></strong><span class="player-state"></span></div><div class="harness"></div><div class="model-line"><span class="model"></span><span class="effort"></span></div><div class="metrics"><div><span class="metric-label">累计用时</span><strong class="metric-value clock">00:00</strong><span class="metric-sub turn-time">本手 —</span></div><div><span class="metric-label token-label">TOKENS</span><strong class="metric-value token-value">—</strong><span class="metric-sub token-detail">等待统计</span><div class="token-meter"><i></i></div></div></div></div>';
      text(card.querySelector('.seat'),sideName(id));card.querySelector('.seat').classList.toggle('second',i===1);text(card.querySelector('.player-name'),p.name);text(card.querySelector('.harness'),p.harness);
      const wrap=card.querySelector('.avatar-wrap');
      if(p.avatar){const img=document.createElement('img');img.className='avatar';img.src=p.avatar;img.alt=p.name+' 半身像';img.addEventListener('error',()=>{wrap.textContent=p.name.slice(0,1);wrap.classList.add('avatar-placeholder');});wrap.append(img);}
      else {wrap.textContent=p.name.slice(0,1);wrap.classList.add('avatar-placeholder');}
      $('players').append(card);
    });
    lastBoardKey='';
  }
  function frame() {return record.snapshots[index];}
  function visibleTurns(){return record.turns.filter(t=>t.ply<=(frame()?.ply??0));}
  function isTerminal(){return index===record.snapshots.length-1&&!['running','starting'].includes(record.status);}
  function activeTurn(){return live&&index===record.snapshots.length-1&&record.status==='running'?record.active_turn:null;}
  function activeSeconds(){const active=activeTurn();return active?Math.max(0,(Date.now()-Date.parse(active.started_at))/1000)||0:0;}
  function renderClocks(){
    const turns=visibleTurns(), active=activeTurn(), seconds=activeSeconds();
    playerIds.forEach((id,i)=>{
      const own=turns.filter(t=>t.player===id), last=own.at(-1), card=$(`player-${i}`), extra=active?.player===id?seconds:0;
      text(card.querySelector('.clock'),time(own.reduce((s,t)=>s+(t.elapsed_seconds||0),0)+extra));
      text(card.querySelector('.turn-time'),extra?`本手 ${extra.toFixed(1)}s · 思考中`:`本手 ${last?last.elapsed_seconds.toFixed(1)+'s':'—'}`);
    });
    text($('time-label'),time(turns.reduce((s,t)=>s+(t.elapsed_seconds||0),0)+seconds));
  }
  function draw(){
    const snapshot=frame();if(!snapshot){text($('turn-indicator'),'等待开局');return;}
    const turns=visibleTurns(), last=turns.at(-1), end=isTerminal(), active=activeTurn(), current=snapshot.state.current_player;
    if(renderer.describe)text($('game-note'),renderer.describe(snapshot));
    const key=record.game_id+JSON.stringify(snapshot);
    if(lastBoardKey!==key){renderer.render($('game-view'),snapshot);lastBoardKey=key;}
    text($('mode'),live?'LIVE':'REPLAY');$('mode').classList.toggle('live',live);
    text($('match-status'),live?(end?'对局已结束':'实时对局'):'赛事回放');
    const result=end?record.result:null;
    const finalLabel={finished:'对局结束',stopped:'对局暂停 · 达到步数上限',failed:'对局中断',cancelled:'对局已取消'}[record.status];
    text($('turn-indicator'),end?finalLabel:`${sideName(current)}行棋 · 第 ${Math.floor(snapshot.ply/2)+1} 回合`);
    playerIds.forEach((id,i)=>{
      const own=turns.filter(t=>t.player===id), latest=own.at(-1), p=record.players[id], card=$(`player-${i}`), bubble=card.querySelector('.bubble');
      card.classList.toggle('active',!end&&current===id);card.classList.toggle('winner',!!result&&result.winner===id);
      text(card.querySelector('.player-state'),result?(result.winner===id?'WINNER':result.winner?'FINISHED':'DRAW'):end?'FINISHED':active?.player===id?'THINKING':current===id?'TO MOVE':'WAITING');
      const model=latest?.model||p.model||p.requested_model||'未提供模型';text(card.querySelector('.model'),model);card.querySelector('.model').title=model;
      const effort=latest?.reasoning_effort||p.reasoning_effort;text(card.querySelector('.effort'),`推理 ${effort||'—'}`);
      const explanation=latest?.explanation||'';text(bubble.querySelector('p'),explanation||(active?.player===id?'正在思考，等待本手说明…':'等待落子说明…'));bubble.classList.toggle('empty',!explanation);bubble.classList.toggle('long',explanation.length>65);bubble.querySelector('p').title=explanation;
      text(bubble.querySelector('.bubble-ply'),latest?`第 ${latest.ply} 手 · ${renderer.formatAction(latest)}`:'OPENING');
      // Never use final player metadata for historical usage. Missing means unknown.
      const usage=latest?.usage;
      let label='TOKENS · 未提供',value=null,detail='此回合未记录统计',fill=0;
      if(usage?.kind==='context'){label='TOKENS · 上下文占用';value=usage.context_tokens;detail=usage.context_limit?`容量 ${count(usage.context_limit)}`:'上下文 token 数';fill=usage.context_limit?value/usage.context_limit:0;}
      else if(usage?.kind==='session'){label='TOKENS · 会话累计';value=(usage.input_tokens??0)+(usage.output_tokens??0);detail=`入 ${count(usage.input_tokens)} / 出 ${count(usage.output_tokens)}`;fill=value?1:0;}
      text(card.querySelector('.token-label'),label);text(card.querySelector('.token-value'),count(value));text(card.querySelector('.token-detail'),detail);card.querySelector('.token-meter i').style.width=`${fill*100}%`;
      card.querySelector('.token-meter').style.visibility=usage?.kind==='context'&&usage.context_limit?'visible':'hidden';
    });
    $('move-list').replaceChildren();
    const rows=[];for(let i=0;i<turns.length;i+=2)rows.push(turns.slice(i,i+2));
    rows.slice(-11).forEach(pair=>{const row=document.createElement('div');row.className='move-pair';const n=document.createElement('span');n.className='number';text(n,String(Math.floor((pair[0].ply+1)/2)).padStart(2,'0'));row.append(n);for(let i=0;i<2;i++){const item=document.createElement('span');if(i===0)item.classList.add('red-move');if(pair[i]===last)item.classList.add('latest');text(item,pair[i]?renderer.formatAction(pair[i]):'·');row.append(item);}$('move-list').append(row);});
    $('result').hidden=!result;
    if(result){text($('result-title'),result.winner?`${sideName(result.winner)}获胜`:'和棋');text($('result-detail'),renderer.formatResult?renderer.formatResult(result,snapshot):`${snapshot.ply} 手 · ${result.reason==='no_legal_moves'?'对方无合法着法':result.reason||'对局结束'}`);}
    text($('footer-status'),end?'MATCH COMPLETE':live?'LIVE MATCH':'MATCH REPLAY');text($('move-caption'),last?`${sideName(last.player)} ${renderer.formatAction(last)} · ${last.elapsed_seconds.toFixed(1)}s`:'初始局面 · 等待第一手');
    text($('ply-label'),`PLY ${String(snapshot.ply).padStart(3,'0')}`);$('progress').style.width=`${record.snapshots.length>1?index/(record.snapshots.length-1)*100:0}%`;renderClocks();
    window.dispatchEvent(new CustomEvent('arena:frame',{detail:{index,ply:snapshot.ply,live}}));
  }
  function setFrame(value,options={}){index=Math.max(0,Math.min(record.snapshots.length-1,Math.trunc(value)||0));live=!!options.live;draw();}
  function setRecord(value,options={}){const changed=value.game_id!==record.game_id||JSON.stringify(value.presentation)!==JSON.stringify(record.presentation)||JSON.stringify(Object.keys(value.players))!==JSON.stringify(Object.keys(record.players));record=value;if(changed)configure();if(options.live!==undefined)live=options.live;index=Math.max(0,Math.min(live?record.snapshots.length-1:index,record.snapshots.length-1));draw();}
  function command(data){if(data?.type!=='arena:state')return;if(data.record)setRecord(data.record,{live:data.live});setFrame(data.index,{live:data.live});}
  window.addEventListener('message',event=>{if(event.source===window.parent&&(event.origin===location.origin||location.protocol==='file:'))command(event.data);});
  if(channel)channel.onmessage=event=>{if(controlled)command(event.data);};
  async function poll(){if(!live||controlled||location.protocol==='file:')return;try{const response=await fetch('match.json',{cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);setRecord(await response.json(),{live:true});document.body.classList.remove('disconnected');}catch{document.body.classList.add('disconnected');text($('footer-status'),'连接中断 · 正在重试');}}
  function resize(){const scale=Math.min(innerWidth/1600,innerHeight/900);$('stage').style.transform=`translate(-50%, -50%) scale(${scale})`;}
  window.arena={setFrame,setRecord,getState:()=>({index,ply:frame()?.ply,live,status:record.status}),ready:false};
  configure();index=params.has('ply')?Math.max(0,Math.min(record.snapshots.length-1,Number(params.get('ply'))||0)):controlled?0:record.snapshots.length-1;draw();resize();window.addEventListener('resize',resize);
  setInterval(renderClocks,100);setInterval(poll,1000);
  window.arena.ready=true;if(window.parent!==window)window.parent.postMessage({type:'arena:ready'},location.protocol==='file:'?'*':location.origin);
  if(channel)channel.postMessage({type:'arena:ready'});
})();
