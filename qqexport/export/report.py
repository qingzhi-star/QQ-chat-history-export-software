"""P1 导出报告（spec §11）。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class ExportReport:
    started_at: str = ""
    finished_at: str = ""
    duration_s: float = 0.0
    qq_version: str = ""
    uin: str = ""
    cipher_params: tuple[str, str, int] = ("", "", 0)
    db_size_bytes: int = 0
    page_count: int = 0
    corrupt_pages: int = 0
    wal_frames: int = 0
    wal_last_commit_frame: int = 0
    wal_truncated: bool = False
    chats_requested: int = 0
    chats_exported: int = 0
    messages_total: int = 0
    messages_ok: int = 0
    messages_degraded: int = 0
    messages_failed: int = 0
    segments_by_kind: dict[str, int] = field(default_factory=dict)
    unknown_fields: dict[int, int] = field(default_factory=dict)
    media_referenced: int = 0
    media_found: int = 0
    media_missing: int = 0
    media_bytes: int = 0
    forwards_nt_unexpanded: int = 0
    forwards_legacy_rendered: int = 0
    voice_decoded: int = 0
    voice_raw: int = 0
    pdf_pages: int | None = None
    pdf_error: str | None = None
    warnings: list[str] = field(default_factory=list)
    exported_dirs: list[str] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return self.messages_total == (self.messages_ok + self.messages_degraded
                                       + self.messages_failed)

    def to_json(self) -> dict:
        return asdict(self)

    def to_text(self) -> str:
        lines = [
            "=" * 52,
            f"导出完成：{self.chats_exported}/{self.chats_requested} 个会话",
            f"消息 {self.messages_total}（正常 {self.messages_ok} / "
            f"降级 {self.messages_degraded} / 失败 {self.messages_failed}）",
            f"媒体 引用 {self.media_referenced} / 找到 {self.media_found} / "
            f"缺失 {self.media_missing}（{self.media_bytes} B）",
            f"耗时 {self.duration_s:.1f}s",
        ]
        if self.wal_frames:
            lines.append(f"WAL {self.wal_frames} 帧（最后 commit "
                         f"{self.wal_last_commit_frame}）")
        if self.pdf_error:
            lines.append(f"PDF：{self.pdf_error}")
        lines.extend(f"警告：{w}" for w in self.warnings)
        lines.append("=" * 52)
        return "\n".join(lines)

    def write(self, out_dir) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(
            json.dumps(self.to_json(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        (out_dir / "report.txt").write_text(self.to_text(), encoding="utf-8")
