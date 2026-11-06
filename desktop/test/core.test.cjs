const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs/promises');
const { CoreBridge } = require('../bridge.cjs');
test('stdio bridge correlates concurrent responses and reports errors', async () => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'qilu-core-test-'));
  const bridge = new CoreBridge(process.env.QILU_PYTHON || 'python', ['-m','qqexport.electron_backend'], {
    cwd: path.resolve(__dirname,'../..'), env: {...process.env,QILU_DEMO:'1',QQEXPORT_HOME:home,PYTHONIOENCODING:'utf-8'}
  });
  try {
    const boot=await bridge.call('boot');
    assert.equal(boot.chats[0].count,100000);
    const [one,two]=await Promise.all([bridge.call('messages',{chat:boot.chats[0].id,offset:0,size:128}),bridge.call('messages',{chat:boot.chats[0].id,offset:1024,size:128})]);
    assert.equal(one.messages[0].index,0);assert.equal(two.messages[0].index,1024);
    await assert.rejects(bridge.call('not-allowed'),/不支持/);
    await bridge.call('switch',{uin:'11111111'});
    await assert.rejects(bridge.call('switch',{uin:'missing'}),/不存在/);
  } finally {await bridge.close();await fs.rm(home,{recursive:true,force:true});}
});
