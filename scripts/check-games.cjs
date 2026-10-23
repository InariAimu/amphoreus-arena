/* Verify short Go and Xiangqi replays. No model requests or full games. */
const {chromium}=require(process.env.ARENA_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=process.argv[2]||'http://127.0.0.1:8877';
const output=path.resolve(process.argv[3]||'output/go-implementation/browser');
const folders=process.argv.slice(4);
fs.mkdirSync(output,{recursive:true});
const columns='ABCDEFGHJKLMNOPQRST';

function expectedStones(state){
  const result=[];
  if(state.board)state.board.forEach((row,r)=>[...row].forEach((stone,c)=>{
    if(stone!=='.')result.push([columns[c]+(state.board_size-r),stone]);
  }));
  else state.fen.split(' ')[0].split('/').forEach((row,r)=>{
    let c=0;for(const stone of row){if(/\d/.test(stone))c+=Number(stone);else{result.push([String.fromCharCode(97+c)+(10-r),stone]);c++;}}
  });
  return result;
}

(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.ARENA_BROWSER||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const errors=[],summary=[];
  try{
    for(const folder of (folders.length?folders:['go-smoke','xiangqi-smoke','go-capture-fixture','go-score-fixture'])){
      const context=await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1});
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
      await page.goto(`${root}/${folder}/broadcast.html?ply=0`);
      await page.waitForFunction(()=>window.arena?.ready&&[...document.images].every(img=>img.complete&&img.naturalWidth>0));
      await page.evaluate(()=>document.fonts.ready);
      const record=await page.evaluate(()=>JSON.parse(document.querySelector('#initial-data').textContent));
      for(let i=0;i<record.snapshots.length;i++){
        const actual=await page.evaluate(index=>{
          window.arena.setFrame(index);
          return {stones:[...document.querySelectorAll('[data-stone],[data-piece]')].map(n=>[n.dataset.square,n.dataset.stone||n.dataset.piece]),
            result:!document.querySelector('#result').hidden,
            sides:[...document.querySelectorAll('.seat')].map(n=>n.textContent),
            marked:[...document.querySelectorAll('[data-last="true"]')].map(n=>n.dataset.square),
            dead:[...document.querySelectorAll('[data-dead]')].map(n=>[n.dataset.square,n.dataset.dead]),
            bubbles:[...document.querySelectorAll('.bubble p')].map(n=>n.textContent),
            resultText:document.querySelector('#result-detail').textContent,
            svg:!!document.querySelector('#game-view svg'),
            clipping:[...document.querySelectorAll('.bubble p')].some(n=>n.scrollHeight>n.clientHeight+1)};
        },i);
        const snapshot=record.snapshots[i],last=snapshot.state.moves.at(-1);
        assert.deepEqual(actual.stones,expectedStones(snapshot.state),`${folder} board ${i}`);
        assert.equal(actual.result,i===record.snapshots.length-1&&!!record.result);
        assert.equal(actual.svg,true);assert.equal(actual.clipping,false);
        assert.deepEqual(actual.sides,record.game_id==='go'?['黑方','白方']:['红方','黑方']);
        if(record.game_id==='go')assert.deepEqual(actual.marked,last&&/^[A-HJ-T]\d+$/.test(last)?[last]:[]);
        if(snapshot.state.phase){
          const state=snapshot.state,dead=state.adjudicated_dead?.length?state.adjudicated_dead:state.negotiation?.proposal?.dead||[];
          assert.deepEqual(actual.dead,dead.map(c=>[c,state.phase==='finished'?'confirmed':'proposed']));
          if(actual.result&&record.result.reason==='agreement')assert.ok(actual.resultText.includes('双方确认数子'));
        }
        if(actual.result&&record.result.score)assert.ok(actual.resultText.includes(`黑 ${record.result.score.black} / 白 ${record.result.score.white}`));
        for(const [seat,id] of Object.keys(record.players).entries()){
          const turn=record.turns.filter(t=>t.player===id&&t.ply<=snapshot.ply).at(-1);
          assert.equal(actual.bubbles[seat],turn?.explanation||'等待落子说明…');
        }
      }
      await page.screenshot({path:path.join(output,`${folder}-1600x900.png`)});
      await page.setViewportSize({width:1000,height:700});
      const bounds=await page.locator('#stage').boundingBox();
      assert.ok(Math.abs(bounds.width-1000)<1&&Math.abs(bounds.height-562.5)<1);
      await page.screenshot({path:path.join(output,`${folder}-scaled.png`)});
      // Exercise the real controller, embedded board, and BroadcastChannel peer.
      await page.setViewportSize({width:1600,height:1000});
      await page.goto(`${root}/${folder}/replay.html`);
      await page.waitForFunction(()=>window.replay&&document.querySelector('#feed').contentWindow.arena?.ready);
      const popup=await context.newPage();popup.on('pageerror',e=>errors.push(e.message));
      await popup.goto(`${root}/${folder}/broadcast.html?follow=replay`);
      await popup.waitForFunction(()=>window.arena?.ready);
      await page.locator('#next').click();
      await popup.waitForFunction(()=>window.arena.getState().index===1);
      await page.waitForFunction(()=>document.querySelector('#feed').contentWindow.arena.getState().index===1);
      await page.locator('#seek').evaluate((input,last)=>{input.value=last;input.dispatchEvent(new Event('input',{bubbles:true}));},record.snapshots.length-1);
      await popup.waitForFunction(last=>window.arena.getState().index===last,record.snapshots.length-1);
      await page.screenshot({path:path.join(output,`${folder}-console.png`)});
      await page.locator('#start').click();
      await page.locator('#speed').selectOption('1200');
      await page.locator('#play').click();
      await popup.waitForFunction(()=>window.arena.getState().index>=1);
      await page.locator('#play').click();
      assert.equal(await page.evaluate(()=>window.replay.getState().playing),false);
      summary.push({folder,game:record.game_id,snapshots:record.snapshots.length,status:record.status,
        board:true,seatLabels:true,resultTiming:true,controller:true,windowSync:true,scaling:true});
      await context.close();
    }
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'verification.json'),JSON.stringify({passed:true,checks:summary,errors},null,2));
    console.log(JSON.stringify({passed:true,checks:summary.length,output}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
