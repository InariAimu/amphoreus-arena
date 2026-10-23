/* Offline browser verification against an existing replay; never calls a harness.
 * ARENA_PLAYWRIGHT can point at a bundled Playwright package. */
const {chromium}=require(process.env.ARENA_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=process.argv[2]||'http://127.0.0.1:8766';
const output=path.resolve(process.argv[3]||'output/broadcast-validation');
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.ARENA_BROWSER||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 try {
  const context=await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1});
  const page=await context.newPage(), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(root+'/broadcast.html?ply=24');
  await page.waitForFunction(()=>window.arena?.ready&&[...document.images].every(i=>i.complete&&i.naturalWidth>0));
  await page.evaluate(()=>document.fonts.ready);
  const data=await page.evaluate(()=>JSON.parse(document.querySelector('#initial-data').textContent));
  await page.screenshot({path:path.join(output,'broadcast-1600x900.png')});
  const bounds=await page.locator('#stage').boundingBox();assert.deepEqual(bounds,{x:0,y:0,width:1600,height:900});
  const overflows=[];
  for(let i=0;i<data.snapshots.length;i++) {
   const result=await page.evaluate(index=>{
    window.arena.setFrame(index);const $=s=>document.querySelector(s);
    return {pieces:[...document.querySelectorAll('[data-piece]')].map(n=>[n.dataset.square,n.dataset.piece]),
     tokens:[...document.querySelectorAll('.token-value')].map(n=>n.textContent),
     bubbles:[...document.querySelectorAll('.bubble p')].map(n=>n.textContent),
     clipping:[...document.querySelectorAll('.bubble p')].some(n=>n.scrollHeight>n.clientHeight+1),
     result:!$('#result').hidden};
   },i);
   const expected=[];
   data.snapshots[i].state.fen.split(' ')[0].split('/').forEach((row,r)=>{let c=0;for(const p of row){if(/\d/.test(p))c+=Number(p);else {expected.push([String.fromCharCode(97+c)+(10-r),p]);c++;}}});
   assert.deepEqual(result.pieces,expected,'board at ply '+i);
   for(const [seat,id] of Object.keys(data.players).entries()){
    const last=data.turns.filter(t=>t.player===id&&t.ply<=i).at(-1), u=last?.usage;
    const tokens=u?.kind==='context'?u.context_tokens:u?.kind==='session'?u.input_tokens+u.output_tokens:null;
    assert.equal(result.tokens[seat],tokens===null?'—':tokens.toLocaleString('en-US'),'tokens at ply '+i);
    assert.equal(result.bubbles[seat],last?.explanation||'等待落子说明…','explanation at ply '+i);
   }
   assert.equal(result.result,i===data.snapshots.length-1&&!!data.result);
   if(result.clipping)overflows.push(i);
  }
  assert.deepEqual(overflows,[],'explanations must fit the broadcast bubbles');
  await page.screenshot({path:path.join(output,'broadcast-final-1600x900.png')});
  // Real elapsed clocks from a synthetic active turn, without running a game.
  const fixture=structuredClone(data);fixture.status='running';fixture.result=null;fixture.snapshots=fixture.snapshots.slice(0,25);fixture.turns=fixture.turns.slice(0,24);
  fixture.active_turn={player:'red',ply:25,started_at:new Date(Date.now()-5000).toISOString()};
  await page.route('**/match.json',route=>route.fulfill({json:fixture}));
  await page.evaluate(record=>{window.arena.setRecord(record,{live:true});},fixture);
  assert.equal(await page.locator('#player-0 .player-state').textContent(),'THINKING');
  const before=await page.locator('#player-0 .turn-time').textContent();
  await page.waitForTimeout(1100);
  assert.notEqual(await page.locator('#player-0 .turn-time').textContent(),before);
  assert.equal(await page.locator('#result').isVisible(),false);
  // Verify the standalone live data source consumes updated snapshots.
  fixture.snapshots=data.snapshots.slice(0,26);fixture.turns=data.turns.slice(0,25);fixture.active_turn=null;fixture.updated_at=new Date().toISOString();
  await page.waitForFunction(()=>window.arena.getState().ply===25);
  await page.evaluate(record=>{window.arena.setRecord(record,{live:false});window.arena.setFrame(24);},data);
  await page.setViewportSize({width:1280,height:720});
  await page.waitForFunction(()=>Math.round(document.querySelector('#stage').getBoundingClientRect().width)===1280);
  assert.deepEqual(await page.locator('#stage').boundingBox(),{x:0,y:0,width:1280,height:720});
  await page.setViewportSize({width:1000,height:800});
  await page.waitForFunction(()=>Math.round(document.querySelector('#stage').getBoundingClientRect().width)===1000);
  assert.deepEqual(await page.locator('#stage').boundingBox(),{x:0,y:118.75,width:1000,height:562.5});
  // Console controls and a separate broadcast window stay in sync.
  const consolePage=await context.newPage();consolePage.on('pageerror',e=>errors.push(e.message));
  await consolePage.goto(root+'/replay.html');await consolePage.waitForFunction(()=>window.replay&&document.querySelector('#feed').contentWindow.arena?.ready);
  const clean=await context.newPage();clean.on('pageerror',e=>errors.push(e.message));await clean.goto(root+'/broadcast.html?follow=replay');
  await clean.waitForFunction(()=>window.arena?.getState().ply===0);
  await consolePage.evaluate(()=>window.replay.seek(24));await clean.waitForFunction(()=>window.arena.getState().ply===24);
  await consolePage.selectOption('#speed','1200');await consolePage.click('#play');await clean.waitForFunction(()=>window.arena.getState().ply>=25);
  await consolePage.click('#play');const paused=await clean.evaluate(()=>window.arena.getState().ply);await clean.waitForTimeout(1400);assert.equal(await clean.evaluate(()=>window.arena.getState().ply),paused);
  await consolePage.evaluate(()=>window.replay.seek(24));await clean.waitForFunction(()=>window.arena.getState().ply===24);
  await consolePage.screenshot({path:path.join(output,'replay-console.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  const report={snapshots_verified:data.snapshots.length,viewport:'1600x900',scaled_viewports:['1280x720','1000x800 letterboxed'],checks:['SVG matches every FEN','historical usage and explanations','no result spoiler','no clipped explanations','live clock','live polling','controller-to-display sync','play and pause'],page_errors:errors,real_matches_started:0,video_exported:false};
  fs.writeFileSync(path.join(output,'verification.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
