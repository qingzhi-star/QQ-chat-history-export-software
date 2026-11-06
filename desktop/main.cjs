const { app, BrowserWindow, ipcMain, dialog, shell, protocol, net, session } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { spawn } = require('node:child_process');
const { CoreBridge } = require('./bridge.cjs');
const NAME = '绮录QQ聊天记录导出软件';
const ROOT = path.resolve(__dirname, '..');
const DEVELOPMENT_PYTHON = process.env.QILU_PYTHON || 'python';
let window, core, outputDirectory, quitting = false;
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
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } });
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
  ipcMain.handle('classic', event => {
    validate(event);
    const executable = app.isPackaged ? path.join(process.resourcesPath, 'core', 'qilu-core.exe') : DEVELOPMENT_PYTHON;
    const child = spawn(executable, app.isPackaged ? ['--classic'] : ['-m', 'qqexport', 'gui'],
      { cwd: app.isPackaged ? process.resourcesPath : ROOT, detached: true, windowsHide: true, stdio: 'ignore' });
    child.on('error', () => dialog.showErrorBox('工具窗口未能打开', '请检查程序文件是否完整，重新安装后再试。'));
    child.unref();
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
  core.close().finally(() => app.quit());
});
