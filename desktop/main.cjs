const { app, BrowserWindow, ipcMain, dialog, shell, protocol, net, session } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { CoreBridge } = require('./bridge.cjs');
const { PdfPrinter } = require('./pdf.cjs');
const NAME = '绮录QQ聊天记录导出软件';
const ROOT = path.resolve(__dirname, '..');
const DEVELOPMENT_PYTHON = process.env.QILU_PYTHON || 'python';
let window, core, outputDirectory, quitting = false;
const printer = new PdfPrinter();
const allowed = new Set(['boot', 'switch', 'refresh', 'messages', 'media', 'openMedia', 'search', 'range']);
protocol.registerSchemesAsPrivileged([{ scheme: 'qilu', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
app.setName(NAME);
app.setAppUserModelId('qingzhi-star.Qilu');
const isolated = process.env.QILU_TEST_HOME;
if (isolated) app.setPath('userData', isolated);

function senderAllowed(event) {
  return window && event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame
    && event.senderFrame.url.startsWith('qilu://app/');
}
function validate(event) {
  if (!senderAllowed(event)) throw new Error('不允许的窗口请求');
}
function launchCore() {
  const executable = app.isPackaged ? path.join(process.resourcesPath, 'core', 'qilu-core.exe') : DEVELOPMENT_PYTHON;
  const args = app.isPackaged ? [] : ['-m', 'qqexport.electron_backend'];
  return new CoreBridge(executable, args, { cwd: app.isPackaged ? process.resourcesPath : ROOT,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1',QILU_ELECTRON_PDF:'1' },
    printPdf: request=>printer.print(request),cancelPdf:id=>printer.cancel(id) });
}
app.whenReady().then(async () => {
  protocol.handle('qilu', request => {
    const url = new URL(request.url);
    if (url.hostname !== 'app') return new Response('', { status: 404 });
    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '') || 'index.html';
    const root = relative.startsWith('brand/') ? (app.isPackaged ? path.join(process.resourcesPath, 'brand') : path.join(ROOT, 'qqexport', 'assets')) : path.join(__dirname, 'renderer');
    const file = path.resolve(root, relative.startsWith('brand/') ? relative.slice(6) : relative);
    if (!file.startsWith(root + path.sep)) return new Response('', { status: 403 });
    return net.fetch(pathToFileURL(file).href);
  });
  core = launchCore();
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  window = new BrowserWindow({ width: 1160, height: 780, minWidth: 900, minHeight: 600,
    title: NAME, backgroundColor: '#f4f2f7', frame: false, show: false,
    icon: app.isPackaged ? path.join(process.resourcesPath, 'brand', 'qilu.ico') : path.join(ROOT, 'qqexport/assets/qilu.ico'),
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true,
      nodeIntegration: false, sandbox: true, webSecurity: true, spellcheck: false } });
  window.removeMenu();
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.once('ready-to-show', () => window.show());
  ipcMain.handle('core', (event, method, params) => {
    validate(event);
    if (!allowed.has(method)) throw new Error('不支持的操作');
    return core.call(method, params);
  });
  ipcMain.handle('chooseFolder', async event => {
    validate(event);
    const result = await dialog.showOpenDialog(window, { title: '选择保存位置', properties: ['openDirectory', 'createDirectory'] });
    if (!result.canceled) outputDirectory = result.filePaths[0];
    return result.canceled ? null : outputDirectory;
  });
  ipcMain.handle('export', async (event, params) => {
    validate(event);
    if (typeof params !== 'object' || !params) throw new Error('导出参数无效');
    // 输出路径只能来自原生文件夹选择器，渲染进程不能指定任意路径。
    if (!outputDirectory) {
      const result = await dialog.showOpenDialog(window, { title: '选择保存位置', properties: ['openDirectory', 'createDirectory'] });
      if (result.canceled) return { canceled: true };
      outputDirectory = result.filePaths[0];
    }
    return core.call('export', { ...params, out: outputDirectory });
  });
  ipcMain.handle('openOutput', event => {
    validate(event);
    return outputDirectory ? shell.openPath(outputDirectory) : null;
  });
  ipcMain.handle('tool', async (event, params) => {
    validate(event);
    const actions = new Set(['batch','queueState','queueCancel','queueRetry','queueResume','archiveCreate',
      'archiveOpen','archiveVerify','libraryImport','libraryAccounts','libraryOpen','libraryClear','cacheClear',
      'missing','settingsGet','settingsSave','searchClear','searchHistory','searchExport','schedule']);
    if (!params || !actions.has(params.action)) throw new Error('不支持的工具操作');
    const safe = {...params}; delete safe.path; delete safe.out;
    const action = safe.action;
    const open = ['archiveOpen','archiveVerify','libraryImport','queueResume'].includes(action);
    const save = ['archiveCreate','schedule'].includes(action);
    if (open || save) {
      const extension = action==='queueResume'?'json':action==='schedule'?'xml':'qqarchive';
      const options = { title: {archiveOpen:'打开离线归档',archiveVerify:'校验归档',libraryImport:'导入归档到资料库',queueResume:'恢复导出队列',archiveCreate:'保存聊天归档',schedule:'保存任务计划'}[action],
        filters:[{name:extension.toUpperCase(),extensions:[extension]}] };
      const result = open ? await dialog.showOpenDialog(window,{...options,properties:['openFile']}) : await dialog.showSaveDialog(window,{...options,defaultPath:`绮录.${extension}`});
      if (result.canceled) return { canceled:true };
      safe.path = open ? result.filePaths[0] : result.filePath;
    }
    if (['batch','searchExport','schedule'].includes(action)) {
      if (!outputDirectory) {
        const result = await dialog.showOpenDialog(window,{title:'选择保存位置',properties:['openDirectory','createDirectory']});
        if (result.canceled) return {canceled:true};
        outputDirectory=result.filePaths[0];
      }
      safe.out=outputDirectory;
    }
    if (['libraryClear','cacheClear'].includes(action)) {
      const result=await dialog.showMessageBox(window,{type:'question',buttons:['取消','确认清理'],defaultId:0,cancelId:0,
        message:action==='libraryClear'?'清空本机资料库内所有明文消息及附件？归档和 QQ 原始数据保留。':'清理缩略图和系统查看器的图片副本？再次浏览时会重新生成。'});
      if(result.response!==1)return {canceled:true};
    }
    if(action==='batch' && safe.rebuild){
      const result=await dialog.showMessageBox(window,{type:'question',buttons:['取消','全量重建'],defaultId:0,cancelId:0,message:'将完整替换选中会话的已有增量备份，是否继续？'});
      if(result.response!==1)return {canceled:true};
    }
    if(action==='settingsSave') {
      delete safe.last_out;
      if(outputDirectory)safe.last_out=outputDirectory;
    }
    return core.call('tool',safe);
  });
  ipcMain.on('window', (event, action) => {
    if (!senderAllowed(event)) return;
    if (action === 'minimize') window.minimize();
    if (action === 'maximize') window.isMaximized() ? window.unmaximize() : window.maximize();
    if (action === 'close') window.close();
  });
  await window.loadURL('qilu://app/index.html');
});
app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if (quitting || !core) return;
  event.preventDefault();
  quitting = true;
  printer.close();
  core.close().finally(() => app.quit());
});
