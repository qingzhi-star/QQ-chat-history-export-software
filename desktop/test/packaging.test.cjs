const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {createHash}=require('node:crypto');
const asar=require('@electron/asar');
const afterPack=require('../after-pack.cjs');

test('real ASAR packaging rejects missing policy before producing release assets',async()=>{
  const home=await fs.mkdtemp(path.join(os.tmpdir(),'qilu-privacy-pack-'));
  try{
    const source=path.join(home,'source'),out=path.join(home,'bundle');
    await fs.mkdir(path.join(source,'renderer'),{recursive:true});
    await fs.mkdir(path.join(out,'resources'),{recursive:true});
    const policy='synthetic privacy\n';
    await fs.writeFile(path.join(source,'privacy-policy.json'),JSON.stringify({sha256:createHash('sha256').update(policy).digest('hex')}));
    await fs.writeFile(path.join(source,'renderer/privacy.html'),'绮录QQ聊天记录导出软件');
    const archive=path.join(out,'resources/app.asar');
    await asar.createPackage(source,archive);
    const context={appOutDir:out,packager:{appInfo:{productFilename:'synthetic'}}};
    await assert.rejects(afterPack(context),/privacy.md/);
    await fs.writeFile(path.join(source,'privacy.md'),policy);
    asar.uncache(archive);
    await asar.createPackage(source,archive);
    await afterPack(context);
    assert.ok(await fs.stat(path.join(out,'LICENSE')));
  }finally{
    assert.ok(path.resolve(home).startsWith(path.resolve(os.tmpdir())+path.sep+'qilu-privacy-pack-'));
    await fs.rm(home,{recursive:true,force:true});
  }
});
