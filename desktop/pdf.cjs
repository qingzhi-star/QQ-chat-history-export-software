const { BrowserWindow, session } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
class PdfPrinter {
  constructor() { this.windows=new Map(); }
  async print({id,html,output}) {
    if (!path.isAbsolute(html) || !path.isAbsolute(output) || path.extname(html)!=='.html' || path.extname(output)!=='.pdf') throw new Error('PDF 参数无效');
    const partition=session.fromPartition(`qilu-pdf-${randomUUID()}`);
    partition.setPermissionRequestHandler((_webContents,_permission,callback)=>callback(false));
    partition.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_details,callback)=>callback({cancel:true}));
    const window=new BrowserWindow({show:false,webPreferences:{session:partition,sandbox:true,contextIsolation:true,nodeIntegration:false,javascript:false,webSecurity:true}});
    this.windows.set(id,window);
    window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    try {
      await window.loadFile(html);
      const data=await window.webContents.printToPDF({printBackground:true,pageSize:'A4',preferCSSPageSize:true});
      if(window.isDestroyed())throw new Error('PDF 已取消');
      await fs.writeFile(output,data);
    } finally { this.cancel(id); }
  }
  cancel(id) {const window=this.windows.get(id);this.windows.delete(id);if(window&&!window.isDestroyed())window.destroy();}
  close() {for(const id of this.windows.keys())this.cancel(id);}
}
module.exports={PdfPrinter};
