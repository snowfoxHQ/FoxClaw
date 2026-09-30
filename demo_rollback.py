"""
#4 Composition 回滚补偿 — 演示脚本

演示多步任务执行时,若关键步骤失败,自动逆序回滚已执行的有副作用步骤。
用法:在项目根目录运行  python demo_rollback.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from hestia.skills import SkillRegistry, SkillSpec, SkillAdapter
from hestia.skills.pipeline import Plan, PlanTask, compose

print("=" * 60)
print("#4 Composition 回滚补偿演示")
print("=" * 60)

# 用一个"数据库"模拟副作用,看回滚是否真的撤销了
fake_db = {"orders": [], "payments": [], "emails_sent": []}


def make_skills(fail_at_step):
    """构造一条三步链:建订单 → 扣款 → 发确认邮件,在指定步骤失败。"""
    reg = SkillRegistry()

    # 步骤1:创建订单(有副作用 + 有补偿)
    def create_order(**k):
        fake_db["orders"].append("order_001")
        return {"order_id": "order_001"}
    def undo_order(output):
        fake_db["orders"].remove("order_001")   # 补偿:删除订单
    reg.register(SkillSpec(id="order", name="建订单", description="创建订单",
        kind="local", side_effect="write", handler=create_order,
        compensate=undo_order, params={"x": "str"}))

    # 步骤2:扣款(有副作用 + 有补偿)
    def charge(**k):
        if fail_at_step == 2:
            raise RuntimeError("银行接口超时,扣款失败")
        fake_db["payments"].append("pay_001")
        return {"payment_id": "pay_001"}
    def refund(output):
        fake_db["payments"].remove("pay_001")   # 补偿:退款
    reg.register(SkillSpec(id="charge", name="扣款", description="支付扣款",
        kind="local", side_effect="pay", handler=charge,
        compensate=refund, params={"x": "str"}))

    # 步骤3:发确认邮件(有副作用,但无补偿——邮件发出去收不回)
    def send_email(**k):
        if fail_at_step == 3:
            raise RuntimeError("邮件服务器拒绝")
        fake_db["emails_sent"].append("email_001")
        return {"email": "sent"}
    reg.register(SkillSpec(id="email", name="发邮件", description="确认邮件",
        kind="local", side_effect="send", handler=send_email,
        params={"x": "str"}))   # 注意:没有 compensate
    return reg


def run_scenario(title, fail_at_step):
    print(f"\n{'─'*60}")
    print(f"场景:{title}")
    print(f"{'─'*60}")
    # 重置数据库
    fake_db["orders"].clear(); fake_db["payments"].clear(); fake_db["emails_sent"].clear()

    reg = make_skills(fail_at_step)
    ad = SkillAdapter(reg)
    plan = Plan(intent="下单流程", tasks=[
        PlanTask(id="T1", name="创建订单", suggested_skill="order"),
        PlanTask(id="T2", name="扣款", suggested_skill="charge"),
        PlanTask(id="T3", name="发确认邮件", suggested_skill="email"),
    ], steps=["T1", "T2", "T3"])

    res = compose(plan, reg, ad, confirmed=True)

    print(f"成功步骤数:{len(res.stages) - len(res.failed)} / {len(plan.tasks)}")
    print(f"失败步骤:{res.failed}")
    print(f"是否触发回滚:{res.rolled_back}")
    print(f"回滚是否干净:{res.rollback_clean}")
    if res.rollback_log:
        print("回滚明细:")
        for log in res.rollback_log:
            print(f"   • {log['skill']:8} [{log['status']}] {log['detail']}")
    if res.needs_manual:
        print(f"⚠ 需人工处理:{res.needs_manual}")
    print(f"\n执行后数据库状态(应已回滚干净):")
    print(f"   订单: {fake_db['orders']}  扣款: {fake_db['payments']}  已发邮件: {fake_db['emails_sent']}")


# 场景 A:第2步(扣款)失败 → 应回滚第1步(订单)
run_scenario("第2步扣款失败 → 自动撤销已创建的订单", fail_at_step=2)

# 场景 B:第3步(发邮件)失败 → 应回滚第1、2步(订单+扣款都退)
run_scenario("第3步发邮件失败 → 撤销订单+退款", fail_at_step=3)

# 场景 C:全部成功 → 不回滚
run_scenario("全部成功 → 无需回滚", fail_at_step=0)

print("\n" + "=" * 60)
print("演示完成。关键看点:")
print("  • 场景A:扣款失败,订单被自动删除(补偿生效)")
print("  • 场景B:邮件失败,订单删除+退款都执行了(逆序回滚)")
print("  • 场景C:全部成功,数据库保留所有记录")
print("  • 有副作用但无补偿的步骤(发邮件)会标记'需人工'")
print("=" * 60)
