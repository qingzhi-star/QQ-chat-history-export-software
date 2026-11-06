"""Tk 主线程结果队列与数据源租约；后台线程不调用 Tk。"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import queue
import threading


class TaskRunner:
    def __init__(self, root, workers=2):
        self.root, self.results = root, queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="qqexport")
        self.closed = False
        self.timer = root.after(15, self.drain)

    def submit(self, work, done, error):
        future = self.pool.submit(work)
        def completed(f):
            if self.closed:
                if not f.cancelled() and f.exception() is None:
                    value = f.result()
                    if hasattr(value, "retire"):
                        value.retire()
                return
            self.results.put((f, done, error))
        future.add_done_callback(completed)
        return future

    def drain(self):
        for _ in range(32):
            try:
                future, done, error = self.results.get_nowait()
            except queue.Empty:
                break
            if future.cancelled():
                continue
            try:
                result = future.result()
            except Exception as exc:
                error(exc)
            else:
                done(result)
        if not self.closed:
            self.timer = self.root.after(15, self.drain)

    def close(self):
        self.closed = True
        self.root.after_cancel(self.timer)
        self.pool.shutdown(wait=False, cancel_futures=True)
        while not self.results.empty():
            future, _, _ = self.results.get_nowait()
            if not future.cancelled() and future.exception() is None:
                value = future.result()
                if hasattr(value, "retire"):
                    value.retire()


class DataSession:
    def __init__(self, source, repository):
        self.source, self.repository = source, repository
        self.lock, self.pins, self.retired = threading.RLock(), 0, False

    def pin(self):
        with self.lock:
            if self.retired:
                raise RuntimeError("数据源已切换")
            self.pins += 1
        released = False

        def release():
            nonlocal released
            with self.lock:
                if released:
                    return
                released = True
                self.pins -= 1
                self._cleanup()
        return release

    def retire(self):
        with self.lock:
            self.retired = True
            self._cleanup()

    def _cleanup(self):
        if self.retired and self.pins == 0 and self.source is not None:
            try:
                if hasattr(self,"search"):
                    self.search.close()
                self.repository.close()
            finally:
                self.source.close()
                self.source = None
