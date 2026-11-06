// Fenwick 树维护实际行高，加载中、空消息与图片尺寸变化使用同一个坐标系。
export class Heights {
  constructor(count, estimate = 106) {
    this.count = count;
    this.values = new Float64Array(count).fill(estimate);
    this.tree = new Float64Array(count + 1);
    for (let i = 1; i <= count; i++) this.tree[i] = (i & -i) * estimate;
  }
  sum(end) { let value = 0; for (; end > 0; end -= end & -end) value += this.tree[end]; return value; }
  set(index, height) {
    const delta = height - this.values[index];
    if (Math.abs(delta) < .5) return false;
    this.values[index] = height;
    for (let i = index + 1; i <= this.count; i += i & -i) this.tree[i] += delta;
    return true;
  }
  locate(pixel) {
    let index = 0, total = 0;
    for (let bit = 2 ** Math.floor(Math.log2(this.count || 1)); bit; bit = Math.floor(bit / 2)) {
      const next = index + bit;
      if (next <= this.count && total + this.tree[next] <= pixel) { index = next; total += this.tree[next]; }
    }
    return Math.min(index, Math.max(0, this.count - 1));
  }
}

export class MessageList {
  constructor(viewport, space, load, render) {
    Object.assign(this, { viewport, space, load, render });
    this.nodes = new Map(); this.pages = new Map(); this.pending = new Map();
    this.queued=new Set();this.failed=new Set();this.active=0;this.pageSize=32;
    this.heights = new Heights(0); this.generation = 0; this.scheduled = false;
    this.follow = true; this.selectionVersion = 0;
    viewport.addEventListener('scroll', () => {
      this.failed.clear();
      if (!this.restoring) this.follow = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 3;
      this.schedule();
    }, { passive: true });
    this.observer = new ResizeObserver(entries => {
      const anchor = this.anchor(); let changed = false;
      for (const entry of entries) {
        const index = Number(entry.target.dataset.index);
        if (this.nodes.get(index)?.element !== entry.target) continue;
        const height = entry.borderBoxSize?.[0]?.blockSize ?? entry.target.getBoundingClientRect().height;
        changed = this.heights.set(index, Math.ceil(height)) || changed;
      }
      if (changed) { this.region(); this.position(); this.restore(anchor); this.schedule(); }
    });
    new ResizeObserver(() => this.schedule()).observe(viewport);
  }
  reset(count, { bottom = true, anchor = null, preserve = false } = {}) {
    const old = this.heights;
    this.generation++; this.pages.clear(); this.pending.clear();this.queued.clear();this.failed.clear();
    for (const { element } of this.nodes.values()) { this.observer.unobserve(element); element.remove(); }
    this.nodes.clear(); this.heights = new Heights(count);
    if (preserve) for (let i = 0; i < Math.min(old.count, count); i++) this.heights.set(i, old.values[i]);
    this.follow = bottom; this.region();
    if (anchor) this.restore(anchor); else this.viewport.scrollTop = bottom ? this.heights.sum(count) : 0;
    this.schedule();
  }
  anchor() { const i = this.heights.locate(this.viewport.scrollTop); return [i, this.viewport.scrollTop - this.heights.sum(i)]; }
  region() { this.space.style.height = `${Math.max(this.viewport.clientHeight, this.heights.sum(this.heights.count))}px`; }
  restore(anchor) {
    this.restoring = true;
    this.viewport.scrollTop = this.follow ? this.heights.sum(this.heights.count) : this.heights.sum(Math.min(anchor[0], this.heights.count)) + anchor[1];
    this.restoring = false;
  }
  position() {
    for (const [index,row] of this.nodes) row.element.style.transform = `translateY(${this.heights.sum(index)}px)`;
  }
  schedule() {
    if (this.scheduled) return;
    this.scheduled = true;
    requestAnimationFrame(() => { this.scheduled = false; this.draw(); });
  }
  invalidate() { this.selectionVersion++; this.schedule(); }
  get(index) {
    const offset = Math.floor(index / this.pageSize) * this.pageSize;
    if (this.pages.has(offset)) {const page=this.pages.get(offset);this.pages.delete(offset);this.pages.set(offset,page);return page.get(index);}
    if (!this.pending.has(offset)&&!this.failed.has(offset))this.queued.add(offset);
    return null;
  }
  pump() {
    const center=this.heights.locate(this.viewport.scrollTop+this.viewport.clientHeight/2);
    while(this.active<2&&this.queued.size){
      const offset=[...this.queued].sort((a,b)=>Math.abs(a+this.pageSize/2-center)-Math.abs(b+this.pageSize/2-center))[0];
      this.queued.delete(offset);
      const generation=this.generation,token={generation};
      this.pending.set(offset,token);this.active++;
      this.load(offset, this.pageSize).then(page => {
        if (generation !== this.generation) return;
        const anchor = this.anchor();
        const rows = new Map(page.messages.map(message => [message.index, message]));
        this.pages.set(offset, rows);
        for (let i = offset; i < Math.min(offset + this.pageSize, this.heights.count); i++) {
          if (!rows.has(i)) this.heights.set(i, 0);
        }
        while (this.pages.size > 48) this.pages.delete(this.pages.keys().next().value);
        this.region(); this.position(); this.restore(anchor); this.schedule();
      }).catch(error => {
        if (generation === this.generation){this.failed.add(offset);this.onError?.(error);}
      }).finally(()=>{
        if(this.pending.get(offset)===token)this.pending.delete(offset);
        this.active--;this.schedule();
      });
    }
  }
  draw() {
    if (!this.heights.count) return;
    const top = Math.max(0, this.viewport.scrollTop - 450), bottom = this.viewport.scrollTop + this.viewport.clientHeight + 450;
    let index = this.heights.locate(top), visible = new Set(),needed=new Set();
    for (; index < this.heights.count && this.heights.sum(index) < bottom && visible.size < 60; index++) {
      const message = this.get(index);
      needed.add(Math.floor(index/this.pageSize)*this.pageSize);
      if (!this.heights.values[index]) continue;
      visible.add(index);
      let row = this.nodes.get(index);
      if (!row || row.message !== message || row.version !== this.selectionVersion) {
        if (row) { this.observer.unobserve(row.element); row.element.remove(); }
        const element = this.render(message, index);
        element.dataset.index = index;
        this.space.append(element);
        this.nodes.set(index, row = { element, message, version: this.selectionVersion });
        this.observer.observe(element);
      }
      row.element.style.transform = `translateY(${this.heights.sum(index)}px)`;
    }
    for (const [i, row] of this.nodes) if (!visible.has(i)) { this.observer.unobserve(row.element); row.element.remove(); this.nodes.delete(i); }
    for(const offset of this.queued)if(!needed.has(offset))this.queued.delete(offset);
    this.pump();
  }
  see(index) { this.follow = false; this.viewport.scrollTop = this.heights.sum(index); this.schedule(); }
}
