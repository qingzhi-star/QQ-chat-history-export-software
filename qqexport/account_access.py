"""用户在 GUI 明确选择账号时，自动取得并验证该账号的本机口令。"""
from qqexport.core import keystore,keyscan,sqlcipher


class AccountAccessError(RuntimeError):
    MESSAGES = {
        "missing": "该账号尚无可用口令，请点击左下角选择账号，软件会自动获取。",
        "database": "无法读取所选账号的数据库，请先在 QQ 登录该账号后重试。",
        "not_found": "未能自动获取所选账号的口令，请先在 QQ 登录该账号后重试。",
        "permission": "无法读取 QQ 进程，请以与 QQ 相同的权限运行本软件后重试。",
        "cache": "本机口令缓存无法读取或保存，未改动原缓存；当前账号仍然保留。",
    }

    def __init__(self,code):
        self.code = code
        self.user_message = self.MESSAGES[code]
        super().__init__(code)


def account_key(account,*,allow_scan=False):
    store = keystore.KeyStore()
    try:
        cached = store.load(account.uin)
    except (keystore.KeyStoreCorrupt,keystore.KeyStoreUnavailable,OSError):
        raise AccountAccessError("cache") from None
    # 普通启动和后台刷新不触发扫描，数据库打开过程仍会验证缓存口令。
    if not allow_scan:
        if not cached:
            raise AccountAccessError("missing")
        return cached
    try:
        header = sqlcipher.read_header_and_page1(account.msg_db)
    except (OSError,ValueError):
        raise AccountAccessError("database") from None
    def verify(candidate):
        return sqlcipher.detect_params(candidate,header) is not None
    if cached and verify(cached):
        return cached
    try:
        found = keyscan.scan_key(verify)
    except keyscan.ProcessAccessDenied:
        raise AccountAccessError("permission") from None
    if found is None or not verify(found):
        raise AccountAccessError("not_found")
    try:
        store.save(account.uin,found)
    except (keystore.KeyStoreCorrupt,keystore.KeyStoreUnavailable,OSError):
        raise AccountAccessError("cache") from None
    return found
