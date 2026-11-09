"""把 `.env` 灌进 `os.environ`,规则只有一条:**非空值优先,空值让路**。

为什么不用 `load_dotenv()` 的默认行为(`override=False`):
系统环境变量里只要有同名键,就会压住 `.env`。用户机器上残留一个早就失效的
旧 key 时,现象是「我明明在 .env 填了新 key,还是 401」,而报错信息完全指向
不了这一层 —— 排查成本极高。2026-11-08 就踩了这个:Machine 级的
DEEPSEEK_API_KEY 是失效的,`.env` 里是新 key,后端一路读到的都是旧的。

为什么也不用 `override=True`:
`.env` / `.env.example` 里有大量**空值占位**(`ANTHROPIC_API_KEY=` 这种)。
整份无条件覆盖会把这些键清成空串 —— 用户真在系统环境里配好的 key 被抹掉,
从「悄悄用错」变成「悄悄清空」,更糟。

所以折中:空值视为「未配置」,不参与覆盖;非空值一定生效,并回报它压掉了
哪些系统变量(只回报名字,不碰值),让这类「幽灵变量」可见。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List


def load_env_file(path: str | Path) -> List[str]:
    """读取 env 文件并写入 `os.environ`;返回被覆盖掉同名系统变量的键名列表。

    env 文件不存在 → 返回空列表(不抛)。未装 python-dotenv → 由 ImportError
    冒给调用方决定降级,本函数不吞。
    """
    from dotenv import dotenv_values

    target = Path(path)
    if not target.exists():
        return []

    shadows: List[str] = []
    for key, value in (dotenv_values(target) or {}).items():
        if not key:
            continue
        value = (value or "").strip()
        if not value:
            continue
        current = os.environ.get(key, "").strip()
        if current and current != value:
            shadows.append(key)
        os.environ[key] = value
    return shadows
