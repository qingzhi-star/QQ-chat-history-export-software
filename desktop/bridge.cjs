const { spawn } = require('node:child_process');
const readline = require('node:readline');

class CoreBridge {
  constructor(command, args, options = {}) {
    this.pending = new Map();
    this.sequence = 0;
    const { printPdf, cancelPdf, ...spawnOptions } = options;
    this.child = spawn(command, args, { ...spawnOptions, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    this.lines = readline.createInterface({ input: this.child.stdout });
    this.lines.on('line', line => {
      let result;
      try { result = JSON.parse(line); } catch { return; }
      if (result.event === 'printPdf') {
        Promise.resolve().then(() => printPdf(result)).then(
          () => this.pdfResult(result.id,true), () => this.pdfResult(result.id,false));
        return;
      }
      if (result.event === 'cancelPdf') { cancelPdf?.(result.id); return; }
      const request = this.pending.get(result.id);
      if (!request) return;
      clearTimeout(request.timeout);
      this.pending.delete(result.id);
      result.error ? request.reject(new Error(result.error)) : request.resolve(result.result);
    });
    // 不保存子进程日志，避免第三方库把本机路径写进发行包旁。
    this.child.stderr.resume();
    this.child.on('error', error => this.fail(error));
    this.child.on('exit', () => this.fail(new Error('本机内核已退出，请重新打开软件')));
    this.child.stdin.on('error', error => this.fail(error));
  }
  pdfResult(id,ok) {
    if (!this.child.stdin.destroyed && !this.child.stdin.writableEnded) this.child.stdin.write(JSON.stringify({event:'pdfResult',id,ok})+'\n','utf8');
  }
  call(method, params = {}) {
    return new Promise((resolve, reject) => {
      if (!this.child || this.child.exitCode !== null) return reject(new Error('本机内核未启动'));
      const id = ++this.sequence;
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('操作耗时较长，请稍后重试'));
      }, method === 'export' || method === 'search' ? 30 * 60_000 : 180_000);
      this.pending.set(id, { resolve, reject, timeout });
      this.child.stdin.write(JSON.stringify({ id, method, params }) + '\n', 'utf8');
    });
  }
  fail(error) {
    for (const request of this.pending.values()) {
      clearTimeout(request.timeout);
      request.reject(error);
    }
    this.pending.clear();
  }
  async close() {
    if (!this.child || this.child.exitCode !== null) return;
    const child = this.child;
    await new Promise(resolve => {
      const timer = setTimeout(() => { child.kill(); resolve(); }, 5000);
      child.once('exit', () => { clearTimeout(timer); resolve(); });
      child.stdin.end();
    });
  }
}
module.exports = { CoreBridge };
