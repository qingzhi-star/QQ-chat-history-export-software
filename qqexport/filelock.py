"""应用自有文件的共享租约；独占清理不与其他进程的读者并发。"""
import ctypes
import os
from qqexport.errors import UserError


class FileLease:
    def __init__(self,path,*,exclusive=False):
        self.stream = path.open("a+b")
        self.closed = False
        try:
            if os.name=="nt":
                import msvcrt
                from ctypes import wintypes
                class Overlapped(ctypes.Structure):
                    _fields_ = [("Internal",ctypes.c_size_t),("InternalHigh",ctypes.c_size_t),
                                ("Offset",wintypes.DWORD),("OffsetHigh",wintypes.DWORD),("hEvent",wintypes.HANDLE)]
                self.overlapped = Overlapped()
                self.handle = msvcrt.get_osfhandle(self.stream.fileno())
                self.kernel = ctypes.WinDLL("kernel32",use_last_error=True)
                self.kernel.LockFileEx.argtypes = [wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,
                    wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(Overlapped)]
                self.kernel.UnlockFileEx.argtypes = [wintypes.HANDLE,wintypes.DWORD,
                    wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(Overlapped)]
                if not self.kernel.LockFileEx(self.handle,1|(2 if exclusive else 0),0,1,0,ctypes.byref(self.overlapped)):
                    raise UserError("资料库正在使用或无法访问，请完成其他任务后重试")
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
        except BaseException:
            self.stream.close()
            raise

    def close(self):
        if not self.closed:
            if os.name=="nt":
                self.kernel.UnlockFileEx(self.handle,0,1,0,ctypes.byref(self.overlapped))
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_UN)
            self.stream.close()
            self.closed = True

    def __enter__(self):
        return self
    def __exit__(self,*args):
        self.close()
