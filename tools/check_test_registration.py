"""测试注册防回潮检查(修复方案批次一 T1)。

tests/test_*.py 必须能被至少一条回归路径覆盖:
1. pyproject.toml [tool.pytest.ini_options].python_files —— 单文件可收集白名单;
2. tests/run_all.py —— 全量回归注册表(每文件独立子进程)。

用法:
  python tools/check_test_registration.py           # 告警模式:列清单,exit 0
  python tools/check_test_registration.py --strict  # 硬门:未注册 = exit 1

v0.1.5 教训:治理批次新增 6 个测试文件全部漏注册(CI 与回归都跑不到),
靠手工维护两个清单没有"提交即注册"约束。本检查先把盲区显式化,
观察一个周期后由 ci.yml 加 --strict 转硬门。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _python_files() -> set:
    """pyproject python_files 列表(逐行解析,保持与 pytest 语义一致)。"""
    entries: set = set()
    in_list = False
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if t.startswith("python_files"):
            in_list = True
            continue
        if in_list:
            m = re.match(r'^"(.+)",?$', t)
            if m:
                entries.add(m.group(1))
            elif t == "]":
                in_list = False
    return entries


def _run_all() -> set:
    """run_all 注册表(字符串字面量提取)。"""
    text = (ROOT / "tests" / "run_all.py").read_text(encoding="utf-8")
    return set(re.findall(r'"(test_[^"]+\.py)"', text))


def main() -> int:
    disk = sorted(p.name for p in (ROOT / "tests").glob("test_*.py"))
    py, ra = _python_files(), _run_all()
    missing_py = [f for f in disk if f not in py]
    missing_ra = [f for f in disk if f not in ra]
    strict = "--strict" in sys.argv
    if not (missing_py or missing_ra):
        print(f"测试注册检查: {len(disk)} 个测试文件,两条回归路径全覆盖 OK")
        return 0
    print(f"测试注册检查: {len(disk)} 个测试文件,存在注册缺口")
    if missing_py:
        print(f"  不在 python_files({len(missing_py)}): {' '.join(missing_py)}")
    if missing_ra:
        print(f"  不在 run_all({len(missing_ra)}): {' '.join(missing_ra)}")
    if strict:
        print("strict 模式:未注册 = 失败。请补注册或明确归档(实验性/废弃)。")
        return 1
    print("(告警模式:不 fail。稳定后把 ci.yml 该步骤加 --strict 转硬门)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
