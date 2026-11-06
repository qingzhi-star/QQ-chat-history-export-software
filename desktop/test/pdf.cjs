const {_electron:electron}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');

(async()=>{
  const home=await fs.mkdtemp(path.join(os.tmpdir(),'qilu-pdf-test-'));
  const executablePath=process.env.QILU_PACKAGED_EXE||process.env.QILU_ELECTRON_RUNTIME||require('electron');
  const env={...process.env,QILU_DEMO:'1',QQEXPORT_HOME:path.join(home,'core'),QILU_TEST_HOME:path.join(home,'electron')};
  if(process.env.QILU_PACKAGED_EXE){
    env.PATH=[path.join(process.env.SystemRoot,'System32'),process.env.SystemRoot].join(path.delimiter);
    delete env.QILU_PYTHON;delete env.PYTHONPATH;delete env.PYTHONHOME;
  }
  const application=await electron.launch({executablePath,args:process.env.QILU_PACKAGED_EXE?[]:[path.resolve(__dirname,'..')],env,timeout:45000});
  try{
    const page=await application.firstWindow();
    await page.locator('.chat').first().waitFor();
    const archive=process.env.QILU_PIPELINE_ARCHIVE,out=process.env.QILU_PIPELINE_OUTPUT;
    assert.ok(archive&&out);
    await application.evaluate(({dialog},{archive,out})=>{
      dialog.showOpenDialog=async(_window,options)=>({canceled:false,filePaths:options.filters?[archive]:[out]});
    },{archive,out});
    const call=params=>page.evaluate(params=>window.qilu.tool(params),params);
    async function done(job){
      const deadline=Date.now()+90000;
      while(Date.now()<deadline){
        const result=await call({action:'queueState',job:job.id});
        if(!result.running){assert.equal(result.error,null);return result.result;}
        await new Promise(resolve=>setTimeout(resolve,30));
      }
      throw new Error('Electron PDF export timeout');
    }
    const opened=await done(await call({action:'archiveOpen',password:'synthetic-smoke-passphrase'}));
    const chat=opened.state.chats[0];assert.equal(chat.count,2);
    const hits=await page.evaluate(chat=>window.qilu.search({query:'你好',chat}),chat.id);
    assert.equal(hits.length,1);
    const result=await done(await call({action:'batch',chats:[chat.id],formats:['txt','html','pdf']}));
    assert.equal(result.tasks[0].status,'done',result.tasks[0].error);
    async function find(directory,name){
      for(const entry of await fs.readdir(directory,{withFileTypes:true})){
        const target=path.join(directory,entry.name);
        if(entry.isDirectory()){const found=await find(target,name);if(found)return found;}
        else if(entry.name===name)return target;
      }
    }
    const pdf=await fs.readFile(await find(out,'chat.pdf'));
    assert.ok(pdf.subarray(0,5).toString()==='%PDF-'&&pdf.includes(Buffer.from('/Subtype /Image')));
    assert.ok(await find(out,'chat.txt'));assert.ok(await find(out,'chat.html'));
    const report={archive_decrypt:true,search:true,txt_html_export:true,real_pdf:true,pdf_bytes:pdf.length,
      without_system_runtime:Boolean(process.env.QILU_PACKAGED_EXE),pdf_engine:'Electron Chromium'};
    await fs.writeFile(process.env.QILU_PIPELINE_REPORT,JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  }finally{await application.close();await fs.rm(home,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
