"""
foxclaw.browser —— HIR-Browser 融入 Foxclaw 的浏览器子系统（"AI 用的浏览器"）。

只做一件事：感知网页 → 压缩成 LLM 可读的语义结构 → 把动作翻译成真实浏览器
操作 → 校验执行结果。不做规划、不做决策——那是 Agent/Foxclaw 控制面的职责。

对外入口是 BrowserService（async 安全、可选依赖优雅降级、专用工作线程）。
`import foxclaw.browser` 不会拉入 playwright；只有真正开会话时才需要。
"""

from .service import BrowserService

__all__ = ["BrowserService"]
