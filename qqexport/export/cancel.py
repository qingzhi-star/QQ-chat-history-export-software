"""可协作取消；只在发布目录前检查，已提交产物保持完整。"""
class ExportCancelled(Exception):
    pass


def check_cancel(event):
    if event is not None and event.is_set():
        raise ExportCancelled("已取消导出")
