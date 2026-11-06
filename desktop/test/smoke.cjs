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
    page.on('pageerror', error=>report.errors.push(error.message));
    await page.locator('.chat').first().waitFor({timeout:30000});
    assert.equal(await page.title(),'绮录QQ聊天记录导出软件');
    const isolation = await page.evaluate(()=>({node:typeof require,process:typeof process,api:typeof window.qilu}));
    assert.deepEqual(isolation,{node:'undefined',process:'undefined',api:'object'});
    await page.locator('.chat').first().click();
    await page.locator('.message .message-body').first().waitFor();
    await page.locator('#messages').evaluate(element=>element.scrollTop=0);
    await page.locator('.quote').first().waitFor();
    await page.locator('.chat-picture').first().waitFor();
    await page.locator('.emoji').first().waitFor();
    assert.ok(await page.locator('.missing-media').count()>0);
    assert.equal(await page.locator('.emoji').first().evaluate(image=>image.getBoundingClientRect().width),28);
    const pictureText = await page.locator('.message').filter({has:page.locator('.chat-picture')}).first().innerText();
    assert.ok(!pictureText.includes('图片加载中'));
    await page.locator('#begin-select').click();
    assert.ok(await page.locator('.emoji').count()>0);
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
      const durations=[],gaps=[];let last=performance.now();let maxNodes=0;
      for(let i=0;i<240;i++){
        viewport.scrollTop+=i<120?72:-72;
        await new Promise(resolve=>requestAnimationFrame(()=>setTimeout(resolve,0)));
        const now=performance.now();durations.push(now-last);last=now;
        maxNodes=Math.max(maxNodes,space.childElementCount);
        const rows=[...space.children].filter(row=>!row.textContent.includes('正在加载')).map(row=>({index:Number(row.dataset.index),box:row.getBoundingClientRect()})).sort((a,b)=>a.index-b.index);
        for(let j=1;j<rows.length;j++)if(rows[j].index===rows[j-1].index+1)gaps.push(rows[j].box.top-rows[j-1].box.bottom);
      }
      durations.sort((a,b)=>a-b);
      return {frameP50:durations[120],frameP95:durations[228],frameMax:durations[239],maxNodes,maxGap:Math.max(0,...gaps)};
    });
    assert.ok(metrics.maxNodes<=60,JSON.stringify(metrics));
    assert.ok(metrics.maxGap<5,JSON.stringify(metrics));
    report.scroll=metrics;
    await page.locator('#messages').evaluate(element=>element.scrollTop=0);
    await page.waitForTimeout(200);
    const bounds=await page.locator('#messages').evaluate(element=>{element.scrollTop=-100;return element.scrollTop;});
    assert.equal(bounds,0);
    await page.locator('#messages').evaluate(element=>element.scrollTop=0);
    await page.locator('.quote').first().waitFor();
    await page.screenshot({path:path.join(root,'build/electron-synthetic-messages.png')});
    const sidebar = await page.locator('#chats').evaluate(async element=>{
      let maxNodes=0;const frames=[];let last=performance.now();
      for(let i=0;i<120;i++){
        element.scrollTop+=i<60?72:-72;
        await new Promise(resolve=>requestAnimationFrame(()=>setTimeout(resolve,0)));
        const now=performance.now();frames.push(now-last);last=now;
        maxNodes=Math.max(maxNodes,element.querySelectorAll('.chat').length);
      }
      element.scrollTop=-100;frames.sort((a,b)=>a-b);
      return {p50:frames[60],p95:frames[114],maxNodes,top:element.scrollTop};
    });
    assert.ok(sidebar.maxNodes<40);assert.equal(sidebar.top,0);report.sidebar=sidebar;
    await page.locator('#switch-account').click();
    await page.locator('#accounts button').filter({hasText:'11111111'}).click();
    await page.locator('#account-uin').filter({hasText:'11111111'}).waitFor();
    report.switch=true;
    assert.deepEqual(report.errors,[]);
    report.pass=true;
    await fs.mkdir(path.join(root,'build'),{recursive:true});
    const output=path.join(root,'build',process.env.QILU_PACKAGED_EXE?'electron-packaged-smoke.json':'electron-ui-smoke.json');
    await fs.writeFile(output,JSON.stringify(report,null,2));
    await page.screenshot({path:path.join(root,'build/electron-synthetic-ui.png')});
    console.log(JSON.stringify(report));
  } finally {await app.close();await fs.rm(home,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
