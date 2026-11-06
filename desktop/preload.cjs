const { contextBridge, ipcRenderer } = require('electron');
const methods = ['boot', 'switch', 'refresh', 'messages', 'media', 'openMedia', 'search', 'range'];
const api = Object.fromEntries(methods.map(method => [method, params => ipcRenderer.invoke('core', method, params ?? {})]));
contextBridge.exposeInMainWorld('qilu', {
  ...api,
  chooseFolder: () => ipcRenderer.invoke('chooseFolder'),
  export: params => ipcRenderer.invoke('export', params),
  openOutput: () => ipcRenderer.invoke('openOutput'),
  classic: () => ipcRenderer.invoke('classic'),
  window: action => ipcRenderer.send('window', action),
});
