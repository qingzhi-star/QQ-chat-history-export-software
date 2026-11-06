"""带 PID 的应用私有明文工作目录；清理仅限已结束进程的遗留目录。"""
import os
import tempfile
from qqexport import config


KINDS = ("exports","archives","archive-build")


def workspace(kind):
    if kind not in KINDS:
        raise ValueError("unknown temporary workspace")
    root = config.app_dir()/"work"/kind
    root.mkdir(parents=True,exist_ok=True)
    from qqexport.export.orchestrator import _cleanup_stale
    _cleanup_stale(root)
    return tempfile.TemporaryDirectory(prefix=f".tmp-{os.getpid()}-",dir=root)


def cleanup_stale():
    from qqexport.export.orchestrator import _cleanup_stale
    for kind in KINDS:
        root = config.app_dir()/"work"/kind
        if root.is_dir():
            _cleanup_stale(root)
