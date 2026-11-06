const fs = require('node:fs/promises');
const path = require('node:path');
const asar = require('@electron/asar');
const {createHash}=require('node:crypto');
// Electron 45 WER helper 与主程序同名，electron-builder 旧默认保留了 electron_wer。
module.exports = async context => {
  const archive=path.join(context.appOutDir,'resources','app.asar');
  const metadata=JSON.parse(asar.extractFile(archive,'privacy-policy.json').toString('utf8'));
  const policy=asar.extractFile(archive,'privacy.md').toString('utf8').replace(/\r\n/g,'\n');
  if(createHash('sha256').update(policy).digest('hex')!==metadata.sha256)throw new Error('Packaged privacy policy hash mismatch');
  if(!asar.extractFile(archive,'renderer/privacy.html').includes(Buffer.from('绮录QQ聊天记录导出软件')))throw new Error('Packaged privacy page missing');
  const oldName = path.join(context.appOutDir,'electron_wer.dll');
  const expected = path.join(context.appOutDir,`${context.packager.appInfo.productFilename}_wer.dll`);
  try { await fs.rename(oldName,expected); }
  catch (error) { if (error.code!=='ENOENT') throw error; }
  const root=path.resolve(__dirname,'..');
  for(const name of ['LICENSE','NOTICE'])await fs.copyFile(path.join(root,name),path.join(context.appOutDir,name));
};
