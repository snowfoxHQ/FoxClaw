"""文件路径准入 —— 把「调用方给的路径」收敛到允许的根目录内。

为什么需要单独一层:领域导入接口(`/api/{finance,learning,itinerary}/import`)接受
请求体里的 ``csv_path``。如果把它原样交给 ``open()``,HTTP 面就等于开放了「读本机
任意文件」的能力 —— 而且读到的内容会被解析进知识库/账本,再从 ``/api`` 读回来,
构成完整的信息泄露链(``csv_path=/etc/passwd`` 这类)。

分层原则(与项目其余部分一致):
- **HTTP 边界是信任边界**,用户可控的路径必须校验;
- **本地库调用(CLI / 测试 / 示例)不校验**:调用方代码本身已有文件权限,
  给它加限制只会破坏用法,不增加安全性。

允许的根 = ``FOXCLAW_DATA_DIR``(默认 ``./foxclaw_data``)
          + ``FOXCLAW_IMPORT_ROOTS``(逗号或分号分隔,给「从下载目录导入」留出口)。

校验用 ``resolve(strict=True)``:先解掉符号链接再比前缀,避免造一个指向 ``/etc``
的软链绕过检查。比较用 ``normcase``:Windows 路径大小写不敏感,直接比字符串会漏。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "DEFAULT_IMPORT_SUFFIXES",
    "allowed_import_roots",
    "is_within",
    "resolve_import_path",
]

_DEFAULT_DATA_DIR = "./foxclaw_data"
DEFAULT_IMPORT_SUFFIXES: tuple[str, ...] = (".csv", ".txt")


def _split_paths(raw: str) -> list[str]:
    """按 , ; 切分路径列表(Windows 的盘符冒号不能当分隔符,故不用 :)。"""
    return [x.strip().strip("\"'") for x in re.split(r"[;,]", raw or "") if x.strip()]


def allowed_import_roots() -> list[Path]:
    """允许作为导入来源的根目录(绝对路径、已 resolve)。"""
    raw = [os.environ.get("FOXCLAW_DATA_DIR") or _DEFAULT_DATA_DIR]
    raw += _split_paths(os.environ.get("FOXCLAW_IMPORT_ROOTS", ""))
    roots: list[Path] = []
    for item in raw:
        try:
            roots.append(Path(item).expanduser().resolve())
        except (OSError, RuntimeError):
            continue        # 无效配置不值当让请求 500,忽略即可(settings check 会提示)
    return roots


def is_within(path: Path | str, roots: Sequence[Path]) -> bool:
    """path 是否落在 roots 的任意一个之下(已按平台规范化大小写)。"""
    try:
        target = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            if target.is_relative_to(root):
                return True
        except (OSError, RuntimeError):
            continue
    return False


def resolve_import_path(raw: str,
                        suffixes: Iterable[str] = DEFAULT_IMPORT_SUFFIXES,
                        roots: Sequence[Path] | None = None) -> Path:
    """校验并返回可安全导入的绝对路径;不合法一律抛 ``ValueError``。

    依次检查:非空 → 存在且是普通文件 → 后缀在白名单 → 落在允许根内。
    """
    text = (raw or "").strip().strip("\"'")
    if not text:
        raise ValueError("路径为空")

    try:
        path = Path(text).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"文件不存在或不可访问: {text}") from exc

    if not path.is_file():
        raise ValueError(f"不是普通文件: {path}")

    allowed_suffixes = {s.lower() for s in suffixes}
    if allowed_suffixes and path.suffix.lower() not in allowed_suffixes:
        raise ValueError(
            f"不支持的扩展名 {path.suffix or '(无)'};允许:"
            f"{' '.join(sorted(allowed_suffixes))}")

    bases = list(roots) if roots is not None else allowed_import_roots()
    if not is_within(path, bases):
        shown = ", ".join(str(b) for b in bases) or "(无)"
        raise ValueError(
            f"路径不在允许的导入目录内: {path}\n"
            f"允许的目录: {shown}\n"
            f"如需从其它目录导入,设置 FOXCLAW_IMPORT_ROOTS=<目录>(多个用逗号分隔)")
    return path
