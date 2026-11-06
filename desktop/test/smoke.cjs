const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const os = require('node:os');

(async () => {
  const root = path.resolve(__dirname, '../..');
  const home = await fs.mkdtemp(path.join(os.tmpdir(),'qilu-electron-smoke-'));
  const executablePath = process.env.QILU_PACKAGED_EXE || process.env.QILU_ELECTRON_RUNTIME || require('electron');
  const appEnv = {...process.env,QILU_DEMO:'1',QILU_TEST_HOME:path.join(home,'electron'),QQEXPORT_HOME:path.join(home,'core'),PYTHONIOENCODING:'utf-8'};
  if(process.env.QILU_PACKAGED_EXE){
    appEnv.PATH=[path.join(process.env.SystemRoot,'System32'),process.env.SystemRoot].join(path.delimiter);
    delete appEnv.QILU_PYTHON;delete appEnv.PYTHONPATH;delete appEnv.PYTHONHOME;
  }
  const app = await electron.launch({ executablePath, args: process.env.QILU_PACKAGED_EXE ? [] : [path.join(root,'desktop')],
    env: appEnv,timeout:45000 });
  const report = { packaged: Boolean(process.env.QILU_PACKAGED_EXE), systemRuntimeRemoved: Boolean(process.env.QILU_PACKAGED_EXE), errors: [] };
  try {
    const page = await app.firstWindow();
    await app.evaluate(({BrowserWindow})=>{const window=BrowserWindow.getAllWindows()[0];window.show();window.setAlwaysOnTop(true);window.focus();});
    page.on('pageerror', error=>report.errors.push(error.message));
    await page.locator('#privacy-dialog').waitFor();
    assert.equal(await page.locator('.chat').count(),0);
    assert.equal(await page.locator('#privacy-avatars').isChecked(),false);
    const denied=await page.evaluate(async()=>Promise.all([
      ()=>window.qilu.boot(),()=>window.qilu.refresh(),()=>window.qilu.messages({chat:'synthetic'}),
      ()=>window.qilu.media({token:'synthetic'}),()=>window.qilu.export({}),
      ()=>window.qilu.tool({action:'settingsGet'}),()=>window.qilu.chooseFolder(),()=>window.qilu.openOutput()
    ].map(async call=>{try{await call();return false;}catch(error){return error.message.includes('隐私条款');}})));
    assert.ok(denied.every(Boolean));
    await page.locator('#privacy-policy-version').filter({hasText:'条款版本'}).waitFor();
    await page.frameLocator('.privacy-document').locator('h1').waitFor();
    await page.screenshot({path:path.join(root,'build/electron-synthetic-privacy.png')});
    await page.locator('#privacy-sensitive').check();await page.locator('#privacy-accept').click();
    report.privacyGate=true;
    await page.locator('.chat').first().waitFor({timeout:30000});
    assert.equal(await page.title(),'绮录QQ聊天记录导出软件');
    const isolation = await page.evaluate(()=>({node:typeof require,process:typeof process,api:typeof window.qilu}));
    assert.deepEqual(isolation,{node:'undefined',process:'undefined',api:'object'});
    const firstMessageStarted=Date.now();
    await page.locator('.chat').first().click();
    await page.locator('.message .message-body').first().waitFor();
    report.firstMessageMs=Date.now()-firstMessageStarted;
    await page.locator('#messages').evaluate(element=>element.scrollTop=0);
    await page.locator('.message[data-index="0"]').waitFor();
    await page.locator('.quote').first().waitFor();
    await page.locator('.chat-picture').first().waitFor();
    await page.locator('.emoji').first().waitFor();
    await page.locator('.missing-media').first().waitFor();
    assert.equal(await page.locator('.emoji').first().evaluate(image=>image.getBoundingClientRect().width),28);
    const pictureText = await page.locator('.message').filter({has:page.locator('.chat-picture')}).first().innerText();
    assert.ok(!pictureText.includes('图片加载中'));
    await page.locator('#begin-select').click();
    await page.locator('.emoji').first().waitFor();
    await page.locator('.message').first().click({position:{x:500,y:20}});
    await page.locator('#selection-count').filter({hasText:'已选择 1 /'}).waitFor();
    await page.locator('#next').click();
    const txt=page.locator('.format-card').filter({hasText:'TXT · 纯文本'});
    await txt.click({position:{x:430,y:40}});
    assert.equal(await txt.locator('input').isChecked(),true);
    await txt.locator('small').click();
    assert.equal(await txt.locator('input').isChecked(),false);
    assert.equal(await page.locator('#begin-select').evaluate(element=>getComputedStyle(element).borderRadius),'9px');
    await page.locator('#cancel-export').click();
    await page.locator('#cancel-select').click();
    // 滚动坐标来自 DOM，只检验合成数据，不读取本人聊天记录。
    const metrics = await page.evaluate(async()=>{
      const viewport=document.getElementById('messages');
      const space=document.getElementById('message-space');
      const durations=[],gaps=[],frameTimes=[];let last=performance.now(),lastFrame=null;let maxNodes=0;
      for(let i=0;i<240;i++){
        viewport.scrollTop+=i<120?72:-72;
        const frameTime=await new Promise(resolve=>requestAnimationFrame(timestamp=>setTimeout(()=>resolve(timestamp),0)));
        if(lastFrame!==null)frameTimes.push(frameTime-lastFrame);lastFrame=frameTime;
        const now=performance.now();durations.push(now-last);last=now;
        maxNodes=Math.max(maxNodes,space.childElementCount);
        const rows=[...space.children].filter(row=>!row.textContent.includes('正在加载')).map(row=>({index:Number(row.dataset.index),box:row.getBoundingClientRect()})).sort((a,b)=>a.index-b.index);
        for(let j=1;j<rows.length;j++)if(rows[j].index===rows[j-1].index+1)gaps.push(rows[j].box.top-rows[j-1].box.bottom);
      }
      durations.sort((a,b)=>a-b);frameTimes.sort((a,b)=>a-b);
      return {frameP50:frameTimes[119],frameP95:frameTimes[227],frameMax:frameTimes[238],timerIntervalP95:durations[228],maxNodes,maxGap:Math.max(0,...gaps)};
    });
    assert.ok(metrics.maxNodes<=60,JSON.stringify(metrics));
    assert.ok(metrics.maxGap<5,JSON.stringify(metrics));
    report.scroll=metrics;
    await app.evaluate(({app})=>{
      const requireMain=process.getBuiltinModule('module').createRequire(process.getBuiltinModule('path').join(app.getAppPath(),'package.json'));
      const {CoreBridge}=requireMain('./bridge.cjs');
      const original=CoreBridge.prototype.call;
      globalThis.qiluMessageProbe={active:0,max:0,calls:0,sizes:[],restore:()=>{CoreBridge.prototype.call=original;}};
      CoreBridge.prototype.call=async function(method,params){
        if(method!=='messages')return original.call(this,method,params);
        const probe=globalThis.qiluMessageProbe;probe.calls++;probe.active++;probe.max=Math.max(probe.max,probe.active);probe.sizes.push(params.size);
        try{await new Promise(resolve=>setTimeout(resolve,40));return await original.call(this,method,params);}
        finally{probe.active--;}
      };
    });
    try{
      await page.locator('#messages').evaluate(async element=>{
        for(let i=1;i<=24;i++){
          element.scrollTop=(element.scrollHeight-element.clientHeight)*((i*.61803398875)%1);
          await new Promise(resolve=>requestAnimationFrame(resolve));
        }
      });
      const settling=Date.now();
      await page.waitForFunction(()=>{
        const viewport=document.getElementById('messages').getBoundingClientRect();
        const rows=[...document.querySelectorAll('.message')].filter(row=>{const r=row.getBoundingClientRect();return r.bottom>viewport.top&&r.top<viewport.bottom;});
        return rows.length>0&&rows.every(row=>!row.textContent.includes('正在加载…'));
      },null,{timeout:3000,polling:'raf'});
      const settledMs=Date.now()-settling;
      const probe=await app.evaluate(()=>({max:globalThis.qiluMessageProbe.max,calls:globalThis.qiluMessageProbe.calls,sizes:globalThis.qiluMessageProbe.sizes}));
      assert.ok(probe.max<=2);assert.ok(probe.sizes.every(size=>size===32));assert.ok(settledMs<750);
      report.randomJump={settledMs,maxRequests:probe.max,calls:probe.calls,simulatedPageDelayMs:40};
    }finally{await app.evaluate(()=>globalThis.qiluMessageProbe.restore());}
    await page.locator('#messages').evaluate(element=>element.scrollTop=0);
    await page.waitForTimeout(200);
    const bounds=await page.locator('#messages').evaluate(element=>{element.scrollTop=-100;return element.scrollTop;});
    assert.equal(bounds,0);
    await page.locator('#messages').evaluate(element=>element.scrollTop=0);
    await page.locator('.quote').first().waitFor();
    await page.screenshot({path:path.join(root,'build/electron-synthetic-messages.png')});
    const sidebar = await page.locator('#chats').evaluate(async element=>{
      let maxNodes=0;const frames=[],frameTimes=[];let last=performance.now(),lastFrame=null;
      for(let i=0;i<120;i++){
        element.scrollTop+=i<60?72:-72;
        const frameTime=await new Promise(resolve=>requestAnimationFrame(timestamp=>setTimeout(()=>resolve(timestamp),0)));
        if(lastFrame!==null)frameTimes.push(frameTime-lastFrame);lastFrame=frameTime;
        const now=performance.now();frames.push(now-last);last=now;
        maxNodes=Math.max(maxNodes,element.querySelectorAll('.chat').length);
      }
      element.scrollTop=-100;frames.sort((a,b)=>a-b);frameTimes.sort((a,b)=>a-b);
      return {p50:frameTimes[59],p95:frameTimes[113],timerIntervalP95:frames[114],maxNodes,top:element.scrollTop};
    });
    assert.ok(sidebar.maxNodes<40);assert.equal(sidebar.top,0);report.sidebar=sidebar;
    await page.locator('#switch-account').click();
    await page.locator('#accounts button').filter({hasText:'11111111'}).click();
    await page.locator('#account-uin').filter({hasText:'11111111'}).waitFor();
    report.switch=true;
    await page.locator('#more').click();
    assert.equal(await page.locator('#classic').count(),0);
    assert.ok(await page.locator('[data-tool]').count()>=15);
    const moreScroll=await page.locator('#more-dialog').evaluate(dialog=>{
      const heading=dialog.querySelector('.more-heading'),body=dialog.querySelector('.more-scroll');
      const before=heading.getBoundingClientRect().top;
      body.scrollTop=body.scrollHeight;
      return {fixed:before===heading.getBoundingClientRect().top,scrolled:body.scrollTop>0,
        outer:dialog.scrollTop,bottomClose:dialog.querySelector('.dialog-actions button')!==null};
    });
    assert.ok(moreScroll.fixed && moreScroll.scrolled);assert.equal(moreScroll.outer,0);assert.equal(moreScroll.bottomClose,false);
    await page.locator('#close-more').click();await page.locator('#more-dialog').waitFor({state:'hidden'});
    await page.locator('#more').click();
    report.moreFixedHeader=true;
    await page.locator('[data-tool="batch"]').click();
    await page.locator('#tool-title').filter({hasText:'批量导出'}).waitFor();
    assert.equal(await page.locator('.tool-chat-list input').count(),200);
    await page.locator('#tool-actions button').filter({hasText:'取消'}).click();
    await page.locator('#more').click();await page.locator('[data-tool="settings"]').click();
    await page.locator('#tool-title').filter({hasText:'软件设置'}).waitFor();
    await page.locator('#tool-actions button').filter({hasText:'取消'}).click();
    await page.locator('#more').click();await page.locator('[data-tool="queueState"]').click();
    await page.locator('#job-message').filter({hasText:'当前没有任务'}).waitFor();
    await page.locator('#job-close').click();
    report.integratedTools=true;
    const declined=await electron.launch({executablePath,args:process.env.QILU_PACKAGED_EXE?[]:[path.join(root,'desktop')],
      env:{...appEnv,QQEXPORT_HOME:path.join(home,'declined-core'),QILU_TEST_HOME:path.join(home,'declined-electron')},timeout:45000});
    const declinedProcess=declined.process();
    await declined.evaluate(({BrowserWindow})=>{const window=BrowserWindow.getAllWindows()[0];window.show();window.setAlwaysOnTop(true);window.focus();});
    try{
      const refusal=await declined.firstWindow();await refusal.locator('#privacy-dialog').waitFor();
      await Promise.all([declined.waitForEvent('close'),refusal.locator('#privacy-decline').click()]);
      assert.equal(declinedProcess.exitCode,0);
      assert.equal(await fs.access(path.join(home,'declined-core')).then(()=>true,()=>false),false);
      report.privacyDeclined=true;
    }finally{if(declinedProcess.exitCode===null)await declined.close();}
    assert.deepEqual(report.errors,[]);
    report.frameClock='requestAnimationFrame DOMHighResTimeStamp; timer intervals retained separately';report.pass=true;
    await fs.mkdir(path.join(root,'build'),{recursive:true});
    const output=path.join(root,'build',process.env.QILU_PACKAGED_EXE?'electron-packaged-smoke.json':'electron-ui-smoke.json');
    await fs.writeFile(output,JSON.stringify(report,null,2));
    await page.screenshot({path:path.join(root,'build/electron-synthetic-ui.png')});
    console.log(JSON.stringify(report));
  } finally {await app.close();await fs.rm(home,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
