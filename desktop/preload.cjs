const { contextBridge, ipcRenderer } = require('electron');
const methods = ['boot', 'switch', 'refresh', 'messages', 'media', 'openMedia', 'search', 'range'];
const api = Object.fromEntries(methods.map(method => [method, params => ipcRenderer.invoke('core', method, params ?? {})]));
contextBridge.exposeInMainWorld('qilu', {
  ...api,
  privacyInfo:()=>ipcRenderer.invoke('privacyInfo'),
  privacyOpen:()=>ipcRenderer.invoke('privacyOpen'),
  privacyAccept:value=>ipcRenderer.invoke('privacyAccept',value),
  privacyDecline:()=>ipcRenderer.invoke('privacyDecline'),
  chooseFolder: () => ipcRenderer.invoke('chooseFolder'),
  export: params => ipcRenderer.invoke('export', params),
  openOutput: () => ipcRenderer.invoke('openOutput'),
  tool: params => ipcRenderer.invoke('tool', params),
  window: action => ipcRenderer.send('window', action),
});
