/* The only Xiangqi-specific presentation code. No harness logic or game rules. */
(() => {
  const ns = 'http://www.w3.org/2000/svg';
  const names = Object.fromEntries([... 'rnbakcpRNBAKCP'].map((p,i)=>[p,[...'车马象士将炮卒车马相仕帅炮兵'][i]]));
  function el(name, attrs={}, text) { const node=document.createElementNS(ns,name); for(const [k,v] of Object.entries(attrs))node.setAttribute(k,v);if(text!==undefined)node.textContent=text;return node; }
  const point = square => [62+(square.charCodeAt(0)-97)*65, 38+(10-Number(square.slice(1)))*63];
  function render(container, snapshot) {
    const svg=el('svg',{viewBox:'0 0 644 640',role:'img','aria-label':'中国象棋棋盘，红方在下'});
    svg.append(el('rect',{x:15,y:2,width:614,height:636,rx:9,fill:'#e4d4b3'}));
    svg.append(el('rect',{x:23,y:10,width:598,height:620,rx:5,fill:'none',stroke:'#bfa77d'}));
    const board=el('g',{stroke:'#7b684b','stroke-width':1.3,fill:'none'});
    for(let y=0;y<10;y++)board.append(el('path',{d:`M62 ${38+y*63} H582`}));
    for(let x=0;x<9;x++){const cx=62+x*65;board.append(el('path',{d:x===0||x===8?`M${cx} 38 V605`:`M${cx} 38 V290 M${cx} 353 V605`}));}
    for(const top of [38,479]) board.append(el('path',{d:`M257 ${top} L387 ${top+126} M387 ${top} L257 ${top+126}`}));
    // Traditional pawn and cannon placement marks.
    for(const [col,row] of [[1,2],[7,2],[1,7],[7,7],...[0,2,4,6,8].flatMap(c=>[[c,3],[c,6]])]){
      const x=62+col*65,y=38+row*63;
      for(const dx of [-1,1])for(const dy of [-1,1])if((col>0||dx>0)&&(col<8||dx<0))board.append(el('path',{d:`M${x+dx*12} ${y+dy*5} H${x+dx*5} V${y+dy*12}`}));
    }
    svg.append(board);
    for(let i=0;i<10;i++)svg.append(el('text',{x:37,y:42+i*63,'text-anchor':'middle',fill:'#8c7754','font-size':10,'font-family':'Consolas'},10-i));
    for(let i=0;i<9;i++)svg.append(el('text',{x:62+i*65,y:625,'text-anchor':'middle',fill:'#8c7754','font-size':11,'font-family':'Consolas'},String.fromCharCode(97+i)));
    for(const [x,label] of [[192,'楚 河'],[452,'汉 界']])svg.append(el('text',{x,y:330,'text-anchor':'middle',fill:'#8b7959','font-size':25,'font-family':'KaiTi, STKaiti, Microsoft YaHei','letter-spacing':10},label));
    const state=snapshot.state, last=state.moves?.at(-1)?.match(/^([a-i]\d+)([a-i]\d+)$/);
    if(last){const [x,y]=point(last[1]),[tx,ty]=point(last[2]);svg.append(el('path',{d:`M${x} ${y} L${tx} ${ty}`,stroke:'#338680','stroke-width':4,opacity:.65}));svg.append(el('circle',{cx:x,cy:y,r:12,fill:'#338680',opacity:.28}));svg.append(el('circle',{cx:tx,cy:ty,r:30,fill:'#47a59c',opacity:.8}));}
    state.fen.split(' ')[0].split('/').forEach((rank,row)=>{let col=0;for(const piece of rank){if(/\d/.test(piece)){col+=Number(piece);continue;}const x=62+col*65,y=38+row*63,isRed=piece===piece.toUpperCase(),color=isRed?'#ad3438':'#27384b';
      const group=el('g',{class:'piece-shadow','data-piece':piece,'data-square':`${String.fromCharCode(97+col)}${10-row}`});
      group.append(el('circle',{cx:x,cy:y+2,r:25,fill:'#a58a61'}));group.append(el('circle',{cx:x,cy:y,r:25,fill:'#f9edcf',stroke:color,'stroke-width':1.8}));group.append(el('circle',{cx:x,cy:y,r:21,fill:'none',stroke:color,'stroke-width':.65,opacity:.7}));group.append(el('text',{x,y:y+10,'text-anchor':'middle',fill:color,'font-family':'KaiTi, STKaiti, Microsoft YaHei','font-weight':600,'font-size':30},names[piece]||piece));svg.append(group);col++;}});
    container.replaceChildren(svg);
  }
  window.ArenaRenderers.xiangqi={title:'中国象棋',subtitle:'楚河汉界 · 双 Harness 对弈',note:'红方在下 · a–i / 1–10\n规则 / Fairy-Stockfish',render,formatAction:turn=>turn.action?.move||'—'};
})();
