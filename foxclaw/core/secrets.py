"""本地密钥存储:优先 OS 凭据库(keyring),不可用时回退 base64 混淆。

为什么值得单独一层:自定义模型的 API key 之前只做 base64 落到
`custom_models.json`。base64 是**编码**不是加密 —— 任何读到该文件的人
(含备份、网盘同步、误提交)一行 python 就能还原成明文。

keyring 把密钥交给操作系统保管:Windows DPAPI / macOS Keychain / Linux
Secret Service。密钥材料不再出现在项目目录里,备份也不会带走它。

回退是刻意的:keyring 是可选依赖,且 Linux 无桌面会话时常常没有可用后端
(`NoKeyringError`)。这种情况下继续 base64,而不是拒绝保存 —— 否则用户
连「改个 key」都做不到。`store()` 返回 False 即表示没接管,调用方据此决定
要不要继续写 `api_key_obf`。

命名空间:`SERVICE` 固定,username = 模型名。改名必须调 `rename()`,
否则 `load(新名)` 取不到,key 会静默丢失。
"""

from __future__ import annotations

import base64

SERVICE = "foxclaw.llm"

# 后端探测结果缓存(含失败):每个请求都 import + 探测一次没必要。
# 失败也缓存是有意的 —— 没有可用后端时不该反复重试。
_kr_cache = None
_kr_probed = False


def _keyring():
    """取得可用的 keyring 模块;无可用后端返回 None。"""
    global _kr_cache, _kr_probed
    if _kr_probed:
        return _kr_cache
    _kr_probed = True
    try:
        import keyring
        from keyring.backends.fail import Keyring as _FailBackend
        # 默认后端是 Fail 时说明系统上没有可用凭据库(典型:无桌面会话的 Linux)
        if isinstance(keyring.get_keyring(), _FailBackend):
            _kr_cache = None
        else:
            _kr_cache = keyring
    except Exception:
        _kr_cache = None
    return _kr_cache


def available() -> bool:
    """是否有可用的 OS 凭据库。"""
    return _keyring() is not None


def reset_probe() -> None:
    """清掉后端探测缓存(测试里替换 keyring 后需要重探)。"""
    global _kr_cache, _kr_probed
    _kr_cache = None
    _kr_probed = False


def obf(text: str) -> str:
    """base64 混淆:只防 grep,不提供任何机密性。"""
    if not text:
        return ""
    return base64.b64encode(text.encode()).decode()


def deobf(text: str) -> str:
    if not text:
        return ""
    try:
        return base64.b64decode(text.encode()).decode()
    except Exception:
        return text


def store(name: str, key: str) -> bool:
    """把 key 写进 OS 凭据库。True = 已接管,调用方不该再落盘密文。"""
    if not name or not key:
        return False
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(SERVICE, name, key)
        return True
    except Exception:
        return False


def load(name: str, obf_key: str = "") -> str:
    """取 key:keyring 优先,回退到 base64(旧记录 / 无后端时写入的)。"""
    if name:
        kr = _keyring()
        if kr is not None:
            try:
                got = kr.get_password(SERVICE, name)
                if got:
                    return got
            except Exception:
                pass
    return deobf(obf_key)


def delete(name: str) -> None:
    if not name:
        return
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(SERVICE, name)
    except Exception:
        pass


def rename(old: str, new: str) -> bool:
    """改名时搬迁凭据。True = 已搬过去。"""
    if not old or not new or old == new:
        return False
    kr = _keyring()
    if kr is None:
        return False
    try:
        key = kr.get_password(SERVICE, old)
    except Exception:
        return False
    if not key:
        return False
    if store(new, key):
        delete(old)
        return True
    return False
