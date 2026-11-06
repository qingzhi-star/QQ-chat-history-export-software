"""仅把合成 SQLite 转为可真实解密的 QQ 格式；不读取本人数据。"""
import ctypes
import _sqlite3
import os
from pathlib import Path
import sqlite3

from tests.helpers.fake_sqlcipher import build_encrypted_db


def encrypt_fixture(path,passphrase):
    # SQLite 官方 file-control 38 为每页预留 SQLCipher 所需的 48 字节。
    # https://www.sqlite.org/c3ref/c_fcntl_begin_atomic_write.html
    library=Path(_sqlite3.__file__)
    library=library.with_name("sqlite3.dll") if os.name=="nt" else library
    api=ctypes.CDLL(str(library))
    pointer=ctypes.c_void_p
    api.sqlite3_open.argtypes=[ctypes.c_char_p,ctypes.POINTER(pointer)]
    api.sqlite3_open.restype=ctypes.c_int
    api.sqlite3_file_control.argtypes=[pointer,ctypes.c_char_p,ctypes.c_int,pointer]
    api.sqlite3_file_control.restype=ctypes.c_int
    api.sqlite3_exec.argtypes=[pointer,ctypes.c_char_p,pointer,pointer,pointer]
    api.sqlite3_exec.restype=ctypes.c_int
    api.sqlite3_close.argtypes=[pointer]
    api.sqlite3_close.restype=ctypes.c_int
    connection=pointer()
    assert api.sqlite3_open(str(path).encode("utf-8"),ctypes.byref(connection))==0
    try:
        assert api.sqlite3_exec(connection,b"PRAGMA page_size=4096; SELECT count(*) FROM sqlite_master",None,None,None)==0
        reserve=ctypes.c_int(48)
        assert api.sqlite3_file_control(connection,b"main",38,ctypes.byref(reserve))==0
        assert api.sqlite3_exec(connection,b"VACUUM",None,None,None)==0
    finally:
        assert api.sqlite3_close(connection)==0
    with sqlite3.connect(path) as con:
        assert con.execute("PRAGMA quick_check").fetchone()==("ok",)
    plain=path.read_bytes()
    assert plain[20]==48
    path.write_bytes(build_encrypted_db([plain[i:i+4096] for i in range(0,len(plain),4096)],passphrase))
