/* Go owns coordinates, stones, scoring labels and board-specific decoration. */
(() => {
  const columns='ABCDEFGHJKLMNOPQRST', ns='http://www.w3.org/2000/svg';
  const el=(tag,attrs={},text)=>{const node=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,v);if(text!==undefined)node.textContent=text;return node;};
  function render(container,snapshot){
    const state=snapshot.state,n=state.board_size,gap=540/(n-1),start=52;
    const svg=el('svg',{viewBox:'0 0 644 644',role:'img','aria-label':`${n} 路围棋 · 第 ${snapshot.ply} 手`});
    svg.append(el('rect',{x:15,y:15,width:614,height:614,rx:14,fill:'#e6c58f',stroke:'#b49361'}));
    const grid=el('g',{stroke:'#806842','stroke-width':1.25});
    for(let i=0;i<n;i++){
      const p=start+i*gap;
      grid.append(el('path',{d:`M${start} ${p} H${start+540} M${p} ${start} V${start+540}`}));
      svg.append(el('text',{x:31,y:p+4,'text-anchor':'middle',fill:'#665132','font-size':12,'font-family':'Consolas'},n-i));
      svg.append(el('text',{x:p,y:616,'text-anchor':'middle',fill:'#665132','font-size':12,'font-family':'Consolas'},columns[i]));
    }
    svg.append(grid);
    const stars=n===9?[2,4,6]:[3,(n-1)/2,n-4];
    for(const r of stars)for(const c of stars){
      if(n===9&&((r===4)!==(c===4)))continue;
      svg.append(el('circle',{cx:start+c*gap,cy:start+r*gap,r:3,fill:'#806842'}));
    }
    const last=state.moves.at(-1);
    const dead=new Set(state.adjudicated_dead?.length?state.adjudicated_dead:state.negotiation?.proposal?.dead||[]);
    state.board.forEach((row,r)=>[...row].forEach((stone,c)=>{
      if(stone==='.')return;
      const x=start+c*gap,y=start+r*gap,radius=gap*.43,square=columns[c]+(n-r);
      const group=el('g',{'data-stone':stone,'data-square':square,'data-last':String(square===last)});
      group.append(el('circle',{cx:x+1.6,cy:y+2.8,r:radius,fill:'#9a7e51',opacity:.75}));
      group.append(el('circle',{cx:x,cy:y,r:radius,fill:stone==='B'?'#222834':'#faf9f2',stroke:stone==='B'?'#10151e':'#a7a49b','stroke-width':1.2}));
      group.append(el('circle',{cx:x-radius*.25,cy:y-radius*.27,r:radius*.34,fill:stone==='B'?'#ffffff0b':'#ffffff88'}));
      if(square===last)group.append(el('circle',{cx:x,cy:y,r:Math.max(3,gap*.1),fill:'none',stroke:'#e96e55','stroke-width':2.5}));
      if(dead.has(square)){
        group.setAttribute('data-dead',state.phase==='finished'?'confirmed':'proposed');
        const d=radius*.65;
        group.append(el('path',{d:`M${x-d} ${y-d} L${x+d} ${y+d} M${x-d} ${y+d} L${x+d} ${y-d}`,stroke:'#e96e55','stroke-width':2.5}));
      }
      svg.append(group);
    }));
    container.replaceChildren(svg);
  }
  const formatAction=turn=>({pass:'停一手',resign:'认输',propose_dead:'提案',accept_dead:'确认',resume_play:'续弈'}[turn.action?.move]||turn.action?.move||'—');
  window.ArenaRenderers.go={
    title:'围棋',subtitle:'黑白对弈 · 双 Harness 对局',note:'面积计分 · 全局同形禁止',
    sideNames:{black:'黑方',white:'白方'},render,formatAction,
    describe:({state})=>`${state.board_size} 路 · 白贴 ${state.komi}\n提子 黑 ${state.captures.black} / 白 ${state.captures.white}\n面积计分 · 禁止自杀\n全局同形禁止\n`+(state.phase?
      `协商规则 · 参照中规2002\n${state.phase==='agreement'?'死子确认中':state.phase==='finished'?'终局已确认':'行棋中'}${state.negotiation?.proposal?' · 标记 '+state.negotiation.proposal.dead.length+' 子':''}`:
      '双停终局 · 死子须提净'),
    formatResult:(result,snapshot)=>result.reason==='resignation'?`${snapshot.ply} 手 · ${result.resigned==='black'?'黑方':'白方'}认输`:
      `${snapshot.ply} 步 · ${result.reason==='agreement'?'双方确认数子':'双停终局'} · 黑 ${result.score.black} / 白 ${result.score.white}（含贴目） · 面积差 ${result.margin} 点${result.reason==='agreement'?'（'+result.margin/2+' 子）':''}`
  };
  const style=document.createElement('style');
  style.textContent='#stage[data-game="go"] .red-move{color:#e5c596}#stage[data-game="go"] .seat{color:#dfdbd1;border-color:#817661;background:#d7b77d18}#stage[data-game="go"] .seat.second{color:#f4f3ef;border-color:#8c929c;background:#e9edf51c}';
  document.head.append(style);
})();
