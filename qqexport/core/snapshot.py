"""db + wal + shm 成组复制：尽力获取一致快照（参照 spec §6.6、修正案 A §5）。

QQ 运行时正在写库。采样时 nt_msg.db-wal 约 1.9 MB 且持续被写入，主库 369 MB。
**「主库 page 1 能解密成功」并不能证明 db / wal / shm 三件套来自同一时刻**：
若在复制 .db 与 .wal 之间发生 checkpoint，就会得到「db 比 wal 新」的错配组合，
产物能正常解密，却对最近的消息静默不一致。所以本模块做的是**尽力获取一致快照**，
而不是声称一致性已被证明。

校验流程（修正案 A §5.1 的四个层次，展开成 6 步；顺序不得调整）：
    1. 复制**前**记录源 db/wal/shm 三者的 FileIdentity
    2. 复制三件套（-shm 落在锁窗口之外，原因见下文实测说明）
    3. 复制**后**再次记录源三者的 FileIdentity
    4. 前后不一致 → 判定「复制期间源发生变化」→ 清理本轮副本 → 重试
    5. 一致 → 再做内容级校验：主库形状/口令 + 对**副本**做 WAL 结构交叉校验
    6. 全部通过才返回 SnapshotResult，并携带 consistency 三态

三次重试本身**不构成**一致性证据——只有第 4 层的 identity 比对与第 5 层的
WAL 校验才是（§5.2）。consistency 取值：
    "proven"      拿到了 shm 锁 **且** 该轮前后 identity 一致 **且** WAL 校验通过
    "best_effort" 未拿到锁，但该轮前后 identity 一致且 WAL 校验通过
    "unverified"  identity 一致但 WAL 校验失败（仍返回，由上层决定如何呈现）

WAL 交叉校验不需要口令：wal.inspect_wal 只做结构校验（累计校验和 + salt），
因此这一层在没有密钥时同样有效。

铁律：对源文件只读。唯一以 "r+b" 打开的是源 -shm，仅为取字节范围锁，
不写任何字节。目标目录里的同名旧副本必须在复制**前**清掉，否则上一轮的
旧 -wal 会与本轮的新 .db 配对（§5.4）；每一轮失败后也必须清掉不完整副本。

SQLite WAL-index 锁区布局的核对结论（修正案 A §5.3 / R23 要求，实现前完成）
------------------------------------------------------------------------
结论：**偏移 120 起、每个锁 1 字节；[120, 123) 恰好覆盖 WAL_WRITE_LOCK、
WAL_CKPT_LOCK、WAL_RECOVER_LOCK 三把锁，且不触及读锁区。与「偏移 120、
长度 3」相符。** 出处：

* sqlite.org/walformat.html —「The WAL-Index header is 136 bytes. Locks
  occupy offsets 120-127」，并给出 xShmLock 索引到文件偏移的对照表：
  WAL_WRITE_LOCK 0→120、WAL_CKPT_LOCK 1→121、WAL_RECOVER_LOCK 2→122、
  WAL_READ_LOCK(0..4) 3..7→123..127。
* wal.c —「#define WALINDEX_LOCK_OFFSET
  (sizeof(WalIndexHdr)*2+offsetof(WalCkptInfo,aLock))」、
  「#define SQLITE_SHM_NLOCK 8」，加锁偏移为 lockIdx + WALINDEX_LOCK_OFFSET
  （即**每锁 1 字节**）。
* SQLite check-in f6c9273cab877b8b —「assert( 120==WALINDEX_LOCK_OFFSET );
  assert( 136==WALINDEX_HDR_SIZE );」并断言 WIN_SHM_BASE 与 UNIX_SHM_BASE
  都等于 WALINDEX_LOCK_OFFSET，注释写明「in case the lock offset ever
  changes from its initial design value of 120」——即 Windows 与 Unix 用
  同一个基址，Windows 侧**没有**额外的字节跨度放大。
* 本机实测（Python 3.14.6 / SQLite 3.50.4 / win32，合成 WAL 库，未触碰 QQ）：
  直接解码 -shm 得到 WalIndexHdr 48 字节两份（0..47、48..95 完全相同），
  WalCkptInfo 为 isInit@96、nBackfill@100、nBackfillAttempted@104、
  mxFrame@108、aReadMark[0]@112、aReadMark[1]@116、aLock@120 —— 与
  WALINDEX_LOCK_OFFSET = 96 + 24 = 120 完全吻合。逐字节非阻塞探测整个
  32768 字节 -shm：写事务进行中仅 120、124（以及一个与本模块无关、且不在
  [120,123) 内的 128）被占用，且 119/121/122/123 全部自由 → 证实每锁 1 字节，
  120 = 写锁、124 = 读锁。故 [120,123) 会与写锁及检查点锁冲突（写入方与
  checkpoint 在复制期间静默），而与读锁区 123 起不冲突（不阻塞 QQ 的读事务）。

注意（本机实测，决定了复制次序）：Windows 的字节范围锁是**按字节精确**生效的——
另一个句柄可以正常读不相交的区间，但任何**跨越** [120,123) 的读都会以 errno 13
失败；Python 的缓冲读一次取 8192 字节，必然跨越该区间，所以**持锁期间 -shm
对其它句柄整体不可读**，只有持锁句柄自己能读完整文件。因此 -shm 必须在锁窗口
之外复制。这不削弱一致性：-shm 只是可重建的 wal-index 缓存，在本管线里仅作
交叉校验用（wal.py 铁律：它出任何问题都不得导致 WAL 恢复失败），而真正必须
同时刻的 .db / -wal 这一对仍然全程在锁内复制。

另注意：锁只在整个复制期间持有。369 MB 的复制可能持续数秒，期间 QQ 的写入会
收到 SQLITE_BUSY；这属于「尽力」的代价，拿不到锁或不宜持锁时降级为
best_effort，由 identity 比对兜底。
"""
from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from . import sqlcipher as sc
from . import wal

SHM_LOCK_OFFSET = 120        # WALINDEX_LOCK_OFFSET：WAL-index 锁区首字节
SHM_LOCK_LENGTH = 3          # 写锁 + 检查点锁 + 恢复锁，各 1 字节；不含读锁区
DEFAULT_ATTEMPTS = 3
COPY_CHUNK = 1024 * 1024

_FAMILY_ALL = ("", "-wal", "-shm")             # 主库 + 两个 sidecar
_SHM_LOCK_MIN_SIZE = SHM_LOCK_OFFSET + SHM_LOCK_LENGTH   # 123


class SnapshotError(RuntimeError):
    """重试耗尽仍无法尽力获取一致快照。消息必须提示用户完全退出 QQ。"""


@dataclass(frozen=True)
class FileIdentity:
    """一个文件在某一时刻的身份。四元组全部来自 os.stat（修正案 A R22）。

    Windows 上 st_ino 就是文件索引、st_dev 就是卷序列号的映射，
    因此无需 ctypes / CreateFileW，也无需处理 FILE_SHARE_WRITE。
    """
    exists: bool
    size: int
    mtime_ns: int
    dev: int
    ino: int


@dataclass
class SnapshotResult:
    """尽力获取一致快照的结果。consistency 三态见模块 docstring。

    字段顺序是兼容性硬约束：前 7 项不得增删或重排，consistency 追加在末尾。
    """
    db_path: Path
    wal_path: Path | None
    shm_path: Path | None
    attempts: int
    locked: bool
    validator_ok: bool
    bytes_copied: int
    consistency: str


def file_identity(path: Path) -> FileIdentity:
    """取文件身份。文件不存在（或取不到）时返回 exists=False，绝不抛异常。"""
    try:
        st = os.stat(path)
    except OSError:
        return FileIdentity(exists=False, size=0, mtime_ns=0, dev=0, ino=0)
    return FileIdentity(exists=True, size=st.st_size, mtime_ns=st.st_mtime_ns,
                        dev=st.st_dev, ino=st.st_ino)


@contextlib.contextmanager
def shm_lock(shm_path: Path | None) -> Iterator[bool]:
    """非阻塞锁住 -shm 偏移 120 起的 3 字节（写锁+检查点锁+恢复锁）。

    拿不到就 yield False，绝不阻塞、绝不抛异常：文件缺失、长度不足 123 字节、
    打不开（QQ 独占）、加锁失败，一律优雅降级。取得与释放都不改动文件内容。
    """
    if shm_path is None or os.name != "nt":
        yield False
        return
    path = Path(shm_path)
    try:
        # Windows 允许锁 EOF 之后的字节，所以必须显式挡下过短的文件。
        # stat 失败（路径不存在、是目录、不可访问）同样走这条降级。
        if path.stat().st_size < _SHM_LOCK_MIN_SIZE:
            yield False
            return
    except OSError:
        yield False
        return

    import msvcrt
    handle = None
    try:
        handle = open(path, "r+b")            # 唯一允许的源文件写模式打开，零字节写入
        handle.seek(SHM_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, SHM_LOCK_LENGTH)
    except OSError:
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()
        yield False
        return
    try:
        yield True
    finally:
        with contextlib.suppress(OSError):
            handle.seek(SHM_LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, SHM_LOCK_LENGTH)
        with contextlib.suppress(OSError):
            handle.close()


def _copy_one(src: Path, dst: Path) -> int:
    """分块复制，返回字节数。不在内存中持有整个文件（主库可达 369 MB）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            chunk = fin.read(COPY_CHUNK)
            if not chunk:
                break
            fout.write(chunk)
            total += len(chunk)
    return total


def _family_map(dst_dir: Path, name: str) -> dict[str, Path]:
    """后缀 -> 目标路径。键为 ""（主库）、"-wal"、"-shm"。"""
    return {sfx: dst_dir / (name + sfx) for sfx in _FAMILY_ALL}


def _remove_family(dst_dir: Path, name: str) -> None:
    """清掉目标目录里 db/-wal/-shm 三个同名文件（不存在就跳过）。"""
    for p in _family_map(dst_dir, name).values():
        with contextlib.suppress(OSError):
            if p.exists():
                p.unlink()


def copy_family(src_db: Path, dst_dir: Path, *,
                members: tuple[str, ...] = _FAMILY_ALL
                ) -> tuple[Path | None, Path | None, Path | None]:
    """复制 <db> 及其 -wal / -shm（存在才复制）。返回三个目标路径。

    members 可以只选其中一部分（未选中的成员返回 None，也不会被清理）。
    snapshot 用它把 -shm 放在锁窗口之外复制，见模块 docstring。

    复制前先清掉目标目录里**本次要复制的**那些同名旧副本（§5.4）：否则上一轮
    的旧 -wal 会与本轮的新 .db 配对，得到「能解密但静默不一致」的库。中途失败
    则清掉本轮已产生的文件再抛出，绝不留下半截副本。
    """
    src_db = Path(src_db)
    dst_dir = Path(dst_dir)
    if "" in members and not src_db.is_file():
        # 只报文件名，不报完整路径（与 SnapshotError 的口径一致）
        raise FileNotFoundError(src_db.name)
    paths = _family_map(dst_dir, src_db.name)
    for sfx in _FAMILY_ALL:
        if sfx in members:
            with contextlib.suppress(OSError):
                if paths[sfx].exists():
                    paths[sfx].unlink()

    made: list[Path] = []

    def _copy(src: Path, dst: Path) -> None:
        made.append(dst)                 # 先登记再复制，半途失败也能清掉
        _copy_one(src, dst)

    out: dict[str, Path | None] = dict.fromkeys(_FAMILY_ALL)
    try:
        for sfx in _FAMILY_ALL:
            if sfx not in members:
                continue
            side = src_db if sfx == "" else src_db.with_name(src_db.name + sfx)
            if side.is_file():
                _copy(side, paths[sfx])
                out[sfx] = paths[sfx]
    except OSError:
        for p in made:
            with contextlib.suppress(OSError):
                if p.exists():
                    p.unlink()
        raise
    return out[""], out["-wal"], out["-shm"]


def make_validator(passphrase: bytes | None = None) -> Callable[[Path], bool]:
    """返回校验函数：判断复制出的主库是否是可用的 QQ NT 加密库。

    给了口令就做完整的 detect_params 校验（最强）；
    没给口令只校验形状：size % 4096 == 1024 且 page-1 salt 非全零。
    任何异常都判 False，绝不向上抛。
    """
    def validate(db_path: Path) -> bool:
        try:
            path = Path(db_path)
            # stat 失败（不存在、是目录、不可访问）与形状不符一律判 False
            if not sc.has_wrapper_header(path.stat().st_size):
                return False
            head = sc.read_header_and_page1(path)
        except OSError:
            return False
        # 只有 1024 字节头、一页都没有时 size % 4096 == 1024 依然成立，
        # 必须靠这条长度检查挡下，否则后面会把空 page1 当成 salt 非全零放过
        if len(head) < sc.WRAPPER_SZ + sc.PAGE_SZ:
            return False
        page1 = head[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.PAGE_SZ]
        if page1[:sc.SALT_SZ] == b"\x00" * sc.SALT_SZ:
            return False
        if passphrase is None:
            return True
        return sc.detect_params(passphrase, head) is not None
    return validate


def _wal_ok(dst_db: Path, dst_wal: Path | None, dst_shm: Path | None) -> bool:
    """对**副本**做 WAL 结构交叉校验（不需要口令）。

    没有 WAL 是合法状态：inspect_wal 会给出 wal_absent，此时视为通过。
    其余情况要求 valid 为真且 page_size 等于 4096。
    """
    wal_path = dst_wal if dst_wal is not None \
        else dst_db.with_name(dst_db.name + "-wal")
    shm_path = dst_shm if dst_shm is not None \
        else dst_db.with_name(dst_db.name + "-shm")
    plan = wal.inspect_wal(wal_path, shm_path)
    if plan.reason == "wal_absent":
        return True
    return bool(plan.valid) and plan.page_size == sc.PAGE_SZ


def snapshot(src_db: Path, dst_dir: Path, *,
             validator: Callable[[Path], bool] | None = None,
             attempts: int = DEFAULT_ATTEMPTS,
             use_shm_lock: bool = False) -> SnapshotResult:
    """成组复制 db/-wal/-shm 并尽力获取一致快照；失败重试，耗尽后抛 SnapshotError。

    每轮顺序（修正案 A §5.1）：记录源身份 → 锁外复制 -shm → 持锁复制 .db/-wal
    （copy_family 复制前先清掉目标目录里的同名旧副本，§5.4）→ 再记录源身份 →
    比对 → 内容级校验 → 返回。任何一条失败路径都会清掉本轮的全部副本。

    锁与 consistency / locked 反映的都是**成功那一轮**的状态，不是「历史上
    有任意一轮拿到过锁」——否则会把没被锁保护的副本说成 proven。

    `use_shm_lock` 默认 **False**（R25）。锁会在整个主库复制期间（本机 369 MB，
    数秒）一直持有，而 QQ 自己的写事务与 checkpoint 也要抢偏移 120 的同一字节，
    于是用户可能看到「数据库已被锁定」。一致性证明实际来自第 1/3/4 层的
    identity 比对 + 重试 + 第 5 层的 WAL 交叉校验，锁只是锦上添花，
    代价却是干扰正在运行的客户端。需要最强保证时显式传 True，
    此时 consistency 才可能取到 "proven"。
    """
    src_db = Path(src_db)
    dst_dir = Path(dst_dir)
    validate = validator if validator is not None else make_validator(None)
    src_wal = src_db.with_name(src_db.name + "-wal")
    src_shm = src_db.with_name(src_db.name + "-shm")

    if not file_identity(src_db).exists:
        # 源库根本不存在，跟「QQ 正在写」是两回事，不能给同一条误导性提示
        raise SnapshotError(
            f"源数据库不存在或不可读：{src_db.name}。"
            f"请确认数据目录选对了，再重新尝试尽力获取一致快照。")

    def _identities():
        # -shm 不参与比对（R24）：它是可重建的 wal-index 缓存，被 QQ mmap 后
        # 持续变动，纳入比对会让每一轮都失败，而这对 db/wal 是否成对毫无意义。
        # wal.py 的铁律本就是「-shm 出任何问题都不得导致 WAL 恢复失败」。
        return (file_identity(src_db), file_identity(src_wal))

    for attempt in range(1, max(1, attempts) + 1):
        # 第 1 层：记录源三件套的身份（窗口覆盖本轮全部复制动作）。
        # 陈旧副本的清理由 copy_family 按成员负责（§5.4），失败路径另见下文。
        before = _identities()

        # -shm 在锁外复制：持锁后它对其它句柄整体不可读（见模块 docstring）。
        # 复制不到就降级为「本次没有 -shm 副本」，绝不让它拖垮整轮——QQ 写事务
        # 进行中同样锁着 120 字节，此时我们的缓冲读会失败；而 -shm 只是可重建的
        # wal-index 缓存，wal.py 的铁律是它出任何问题都不得导致 WAL 恢复失败。
        try:
            _db, _wal, dst_shm = copy_family(src_db, dst_dir, members=("-shm",))
        except OSError:
            dst_shm = None

        locker = shm_lock(src_shm) if use_shm_lock \
            else contextlib.nullcontext(False)
        with locker as got_lock:
            try:
                # 第 2 层：复制真正需要同时刻的那一对
                dst_db, dst_wal, _s = copy_family(src_db, dst_dir,
                                                  members=("", "-wal"))
            except OSError:
                _remove_family(dst_dir, src_db.name)
                continue
            # 第 3 层：复制后再次记录源身份（仍在锁内，窗口受锁保护）
            after = _identities()

        # 第 4 层：前后不一致 → 本轮副本作废，重试
        if before != after:
            _remove_family(dst_dir, src_db.name)
            continue

        # 第 5 层：内容级校验（全部针对副本）
        if not validate(dst_db):
            _remove_family(dst_dir, src_db.name)
            continue
        wal_validated = _wal_ok(dst_db, dst_wal, dst_shm)

        # 第 6 层：只有走到这里才返回
        if not wal_validated:
            consistency = "unverified"
        elif got_lock:
            consistency = "proven"
        else:
            consistency = "best_effort"
        total = sum(p.stat().st_size for p in (dst_db, dst_wal, dst_shm)
                    if p is not None)
        return SnapshotResult(dst_db, dst_wal, dst_shm, attempt, bool(got_lock),
                              True, total, consistency)

    raise SnapshotError(
        f"尽力获取一致快照失败：复制 {src_db.name} 后连续 {attempts} 次未能通过"
        f"校验（源文件在复制期间一直在变，或副本内容不可用）。"
        f"最可能的原因是 QQ 正在写入数据库。请完全退出 QQ（托盘图标右键退出，"
        f"不是只关窗口）后重试；干净退出会触发 WAL checkpoint，也能让导出更完整。"
    )
