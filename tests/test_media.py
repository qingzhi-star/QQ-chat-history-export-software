"""T7：媒体索引/解析/复制测试。"""
from __future__ import annotations

from pathlib import Path

from qqexport.export import media_copy
from qqexport.qq import media

MD5A = "a" * 32
MD5B = "b" * 32


def tree(tmp_path):
    root = tmp_path / "nt_data"
    ori = root / "Pic" / "2024-01" / "Ori"
    thumb = root / "Pic" / "2024-01" / "Thumb"
    ori.mkdir(parents=True)
    thumb.mkdir(parents=True)
    (ori / f"{MD5A}.jpg").write_bytes(b"ori-data")
    (thumb / f"{MD5A}_0.jpg").write_bytes(b"thumb")
    (root / "Ptt" / "2024-01" / "Ori").mkdir(parents=True)
    (root / "Ptt" / "2024-01" / "Ori" / f"{MD5B}.amr").write_bytes(b"voice")
    return root


def test_index_and_ori_preferred(tmp_path):
    root = tree(tmp_path)
    idx = media.build_index(root)
    assert MD5A in idx.by_md5 and MD5B in idx.by_md5
    ref = media.resolve(idx, "image", MD5A, None, None)
    assert ref.missing is False
    assert "Ori" in str(ref.source)


def test_local_path_wins_and_filename_fallback(tmp_path):
    root = tree(tmp_path)
    idx = media.build_index(root)
    local = tmp_path / "local.jpg"
    local.write_bytes(b"x")
    ref = media.resolve(idx, "image", None, str(local), "local.jpg")
    assert ref.source == local
    ref2 = media.resolve(idx, "image", None, None, f"{MD5A}.jpg")
    assert ref2.missing is False and ref2.source is not None


def test_missing_media(tmp_path):
    idx = media.build_index(tmp_path / "empty")
    ref = media.resolve(idx, "image", "c" * 32, None, "nope.jpg")
    assert ref.missing is True and ref.source is None


def test_copy_only_referenced_and_source_unchanged(tmp_path):
    root = tree(tmp_path)
    idx = media.build_index(root)
    ref = media.resolve(idx, "image", MD5A, None, None)
    src_before = (ref.source.stat().st_size, ref.source.stat().st_mtime_ns)
    out = tmp_path / "out"
    res = media_copy.copy_media([ref, ref], out)
    assert res.copied == 1
    assert res.missing == 0
    assert res.bytes_copied == len(b"ori-data")
    name = res.rel_by_source[ref.source]
    assert (out / name).read_bytes() == b"ori-data"
    assert (ref.source.stat().st_size, ref.source.stat().st_mtime_ns) == src_before


def test_refresh_reuses_unchanged_folders_and_detects_added_deleted_files(tmp_path,monkeypatch):
    root = tree(tmp_path)
    first = media.build_index(root)
    original = media.os.scandir
    calls = []
    def scan(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(media.os,"scandir",scan)
    second = media.build_index(root,previous=first)
    assert second.by_md5==first.by_md5 and calls==[]
    folder = root/"Pic"/"2024-01"/"Ori"
    new_file = folder/("c"*32+".jpg")
    new_file.write_bytes(b"synthetic")
    # 显式改变目录时间，避免文件系统时间粒度影响增量索引测试。
    stamp = folder.stat().st_mtime_ns+1000000
    media.os.utime(folder,ns=(stamp,stamp))
    third = media.build_index(root,previous=second)
    assert "c"*32 in third.by_md5 and "c"*32 not in second.by_md5
    new_file.unlink()
    stamp += 1000000
    media.os.utime(folder,ns=(stamp,stamp))
    assert "c"*32 not in media.build_index(root,previous=third).by_md5


def test_filename_lookup_does_not_scan_all_media(tmp_path):
    from dataclasses import replace
    index = media.build_index(tree(tmp_path))
    class NoLinearScan(dict):
        def values(self):
            raise AssertionError("all-media scan")
    index = replace(index,by_md5=NoLinearScan(index.by_md5))
    assert not media.resolve(index,"image",None,None,MD5A+".jpg").missing
    assert not media.resolve(index,"image",None,None,MD5A[:24]+".jpg").missing
    assert media.resolve(index,"image",None,None,"synthetic-missing.jpg").missing
