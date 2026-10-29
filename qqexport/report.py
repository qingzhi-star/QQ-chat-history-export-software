"""诊断报告的数据结构与渲染。参照 spec §9.3、§11。

`to_text(ascii_only=True)` 是给中文 Windows 控制台（常见 cp936）用的：
emoji 会直接抛 UnicodeEncodeError，必须有纯 ASCII 的状态标记退路。
注意它只保证**状态标记**是 ASCII；检查名与详情可能仍含中文——cp936 能显示
中文，只是不能显示 emoji。

`to_json()` 带 `schema_version`（修正案 A §9.5），且**不负责脱敏**：路径与 uin
必须在 doctor 构造 detail 时就已掩码，report 只做序列化。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

SCHEMA_VERSION = 1
_VALID_STATUS = ("ok", "warn", "fail", "skip")

STATUS_GLYPH = {"ok": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭"}
STATUS_ASCII = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[SKIP]"}


@dataclass(frozen=True)
class CheckResult:
    index: int
    name: str
    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUS:
            raise ValueError(f"unknown status: {self.status!r}")


@dataclass
class DoctorReport:
    checks: list[CheckResult]
    generated_at: str

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_json(self) -> dict:
        return {"schema_version": SCHEMA_VERSION,
                "generated_at": self.generated_at, "ok": self.ok,
                "checks": [asdict(c) for c in self.checks]}

    def to_text(self, ascii_only: bool = False) -> str:
        glyphs = STATUS_ASCII if ascii_only else STATUS_GLYPH
        width = max((len(c.name) for c in self.checks), default=0)
        lines = []
        for c in self.checks:
            lines.append(f"{glyphs[c.status]} {c.index:>2}. "
                         f"{c.name:<{width}}  {c.detail}".rstrip())
        summary = ("all checks passed" if self.ok
                   else f"{len(self.failed)} check(s) failed")
        if ascii_only:
            lines.append("")
            lines.append(summary)
        else:
            lines.append("")
            lines.append("全部检查通过" if self.ok
                         else f"{len(self.failed)} 项检查未通过")
        return "\n".join(lines)
