"""冻结后的管道内核入口；--classic 兼容已有完整 P2 工具。"""
import sys
from qqexport.temporary import cleanup_stale

if __name__=="__main__":
    if "--classic" in sys.argv:
        from qqexport.tk_runtime import create_root,prepare_frozen
        prepare_frozen()
        from qqexport.product_gui import ProductApp
        root = create_root()
        app = ProductApp(root)
        root.mainloop()
    else:
        cleanup_stale()
        from qqexport.electron_backend import main
        main()
