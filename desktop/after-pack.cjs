const fs = require('node:fs/promises');
const path = require('node:path');
// Electron 45 WER helper 与主程序同名，electron-builder 旧默认保留了 electron_wer。
module.exports = async context => {
  const oldName = path.join(context.appOutDir,'electron_wer.dll');
  const expected = path.join(context.appOutDir,`${context.packager.appInfo.productFilename}_wer.dll`);
  try { await fs.rename(oldName,expected); }
  catch (error) { if (error.code!=='ENOENT') throw error; }
};
