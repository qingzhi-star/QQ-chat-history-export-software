"""冻结后的管道内核入口；--classic 兼容已有完整 P2 工具。"""
import sys
from qqexport.temporary import cleanup_stale

if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--scheduled-export":
        from pathlib import Path
        from qqexport.scheduled import run
        raise SystemExit(run(Path(sys.argv[2])))
    cleanup_stale()
    from qqexport.electron_backend import main
    main()
