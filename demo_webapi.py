"""
#3 Web API 真实调用 — 演示脚本

演示 web_api 类 skill 真正发 HTTP 请求,含:成功调用、错误处理、超时、SSRF防护。
用法:在项目根目录运行  python demo_webapi.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from hestia.skills import SkillRegistry, SkillSpec, SkillAdapter

print("=" * 60)
print("#3 Web API 真实调用演示")
print("=" * 60)

reg = SkillRegistry()
ad = SkillAdapter(reg)

# ---- 演示 1:真实调用一个公开 API(获取你的公网 IP)----
print("\n【1】真实 GET 请求:查询本机公网 IP")
reg.register(SkillSpec(
    id="myip", name="查IP", description="获取公网IP",
    kind="web_api", side_effect="read", external=True,
    endpoint="https://api.ipify.org", http_method="GET",
    http_headers={"User-Agent": "HestiaOS"}, timeout=10.0,
    params={"format": "x"}))   # ipify 支持 ?format=json
r = ad.invoke(reg.get("myip"), {"format": "json"}, confirmed=True)
print(f"   状态: {r.status}")
if r.success:
    print(f"   ✓ 真实调通! 响应数据: {r.output.get('data')}")
    print(f"   (这是真从 api.ipify.org 拿到的你的公网IP)")
else:
    print(f"   消息: {r.message}")
    print("   (如果失败,可能是你的网络没法访问外网,换下面的国内API试)")

# ---- 演示 2:错误处理(404)----
print("\n【2】错误处理:访问不存在的资源(预期 404)")
reg.register(SkillSpec(
    id="notfound", name="404", description="测试404",
    kind="web_api", side_effect="read", external=True,
    endpoint="https://httpbin.org/status/404", http_method="GET", timeout=10.0))
r2 = ad.invoke(reg.get("notfound"), {}, confirmed=True)
print(f"   状态: {r2.status}")
print(f"   ✓ 正确捕获错误: {r2.message}" if not r2.success else "   未捕获")

# ---- 演示 3:POST 请求(发 JSON body)----
print("\n【3】POST 请求:发送 JSON 数据(httpbin 会回显)")
reg.register(SkillSpec(
    id="post", name="提交", description="POST测试",
    kind="web_api", side_effect="write", external=True,
    endpoint="https://httpbin.org/post", http_method="POST", timeout=10.0))
r3 = ad.invoke(reg.get("post"), {"name": "snowfox", "project": "HestiaOS"}, confirmed=True)
print(f"   状态: {r3.status}")
if r3.success:
    sent = r3.output.get("data", {}).get("json")
    print(f"   ✓ POST 成功,服务器收到的 body: {sent}")

# ---- 演示 4:超时处理 ----
print("\n【4】超时处理:故意设极短超时(0.001秒)")
reg.register(SkillSpec(
    id="timeout", name="超时", description="测试超时",
    kind="web_api", side_effect="read", external=True,
    endpoint="https://api.ipify.org", http_method="GET", timeout=0.001))
r4 = ad.invoke(reg.get("timeout"), {}, confirmed=True)
print(f"   状态: {r4.status}")
print(f"   ✓ 超时被正确捕获: {r4.message}" if r4.status == "timeout" else f"   结果: {r4.message}")

# ---- 演示 5:SSRF 安全防护 ----
print("\n【5】SSRF 安全防护:尝试访问内网/危险地址(应被拦截)")
for url, desc in [
    ("http://169.254.169.254/latest/meta-data/", "云服务器元数据(偷密钥用)"),
    ("http://localhost:8000/admin", "本地服务"),
    ("http://192.168.1.1/", "内网路由器"),
    ("file:///etc/passwd", "本地文件"),
]:
    reg.register(SkillSpec(id=f"ssrf_{hash(url)%9999}", name="x", description="x",
        kind="web_api", side_effect="read", external=True, endpoint=url))
    rr = ad.invoke(reg.get(f"ssrf_{hash(url)%9999}"), {}, confirmed=True)
    blocked = "安全策略" in rr.message
    print(f"   {desc:22} → {'✓ 已拦截' if blocked else '✗ 漏过'}")

print("\n" + "=" * 60)
print("演示完成。关键看点:")
print("  • 演示1/3 真实发出了 HTTP 请求并拿到响应")
print("  • 演示2/4 错误和超时被正确处理")
print("  • 演示5 内网/危险地址被 SSRF 防护拦截")
print("=" * 60)
