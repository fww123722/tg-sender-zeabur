# -*- coding: utf-8 -*-
"""AI 举报「卡住不动」修复验证。

复现根因并证明修好：
 1) 旧行为：client(...) 撞 FloodWaitError 时 Telethon 会自己 sleep(最多60s)，
    且 _do_report_once 里 except: pass 把错误吞掉 → 界面只看到「执行举报」不动
 2) 新行为：flood_sleep_threshold 临时置 0（立刻抛错）+ wait_for 硬超时
    + 长限流立即收手换号 + 失败原因回显到结果
"""
import asyncio
import os
import sys
import time

os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "abc")
os.environ.setdefault("BOT_TOKEN", "***")
os.environ.setdefault("OWNER_ID", "111")
os.environ.setdefault("DATABASE_URL", "postgresql://u:***@127.0.0.1:5432/none")

D = r"C:\Users\amdin\.openclaw\workspace-group-bot\projects\tg-sender-zeabur"
sys.path.insert(0, D)

import reporter as RP          # noqa: E402
from telethon.errors import FloodWaitError   # noqa: E402

FAILS = []


def ck(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  << " + str(extra)[:170]))
    if not cond:
        FAILS.append(name)


class FakeClient:
    """模拟真实 Telethon：flood_sleep_threshold<=0 时限流立刻抛错，
    否则自己 sleep(e.seconds)（这就是原来卡住的机制）。"""
    def __init__(self, no, flood=None, hang=False):
        self.no = str(no)
        self.flood_sleep_threshold = 60        # Telethon 默认值
        self.flood = flood
        self.hang = hang
        self.seen_threshold = []

    async def get_input_entity(self, t):
        if self.hang:
            await asyncio.sleep(600)
        await asyncio.sleep(0)
        return f"PEER:{t}"

    async def get_entity(self, t):
        if self.hang:
            await asyncio.sleep(600)
        await asyncio.sleep(0)
        return f"E:{t}"

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        self.seen_threshold.append(self.flood_sleep_threshold)
        if self.hang:
            await asyncio.sleep(600)           # 网络挂死
        if self.flood is not None:
            e = FloodWaitError(request=request, capture=self.flood)
            # Telethon 语义：秒数 <= 阈值就自己睡
            if e.seconds <= self.flood_sleep_threshold:
                await asyncio.sleep(e.seconds)
                return None                    # 睡完重试成功
            raise e
        return None


# ---------- 1) 常量存在 ----------
ck("REPORT_FLOOD_MAX 存在", isinstance(RP.REPORT_FLOOD_MAX, int), RP.REPORT_FLOOD_MAX)
ck("REPORT_API_TIMEOUT 存在", isinstance(RP.REPORT_API_TIMEOUT, int), RP.REPORT_API_TIMEOUT)
ck("REPORT_TEXT_MAX 存在", RP.REPORT_TEXT_MAX == 900, RP.REPORT_TEXT_MAX)
ck("_resolve 存在", callable(getattr(RP, "_resolve", None)))
ck("_api 存在", asyncio.iscoroutinefunction(getattr(RP, "_api", None)))

# ---------- 2) 限流 45 秒：旧版会睡 45 秒，新版必须立刻返回 ----------
real_sleep = asyncio.sleep
c_fast = FakeClient(1, flood=45)
t0 = time.time()
ok, total, err = asyncio.run(RP._do_report_once(c_fast, "P", "R", "text"))
cost = time.time() - t0
print("   \u9650\u6d4145\u79d2\uff1a\u8017\u65f6 %.2fs ok=%s total=%s err=%r" % (cost, ok, total, err))
ck("限流 45s 不再睡 45 秒", cost < 5, "%.1fs" % cost)
ck("限流原因如实回报", "限流等45秒" in (err or ""), err)
ck("调用期间阈值被临时改 0", 0 in c_fast.seen_threshold, c_fast.seen_threshold[:4])
ck("退出后阈值恢复 60", c_fast.flood_sleep_threshold == 60, c_fast.flood_sleep_threshold)

# ---------- 3) 长限流（>阈值）：立刻收手，不再试剩下两个请求 ----------
c_long = FakeClient(2, flood=900)
t0 = time.time()
ok, total, err = asyncio.run(RP._do_report_once(c_long, "P", "R", "text", msg_ids=[1, 2]))
cost = time.time() - t0
print("   \u9650\u6d41900\u79d2\uff1a\u8017\u65f6 %.2fs ok=%d total=%d err=%r" % (cost, ok, total, err))
ck("长限流立刻收手", cost < 5, "%.1fs" % cost)
ck("长限流只撞1次（不再硬试）", len(c_long.seen_threshold) == 1, c_long.seen_threshold)
ck("判定为超阈值", RP._give_up_early(err), err)
ck("短限流不提前收手", not RP._give_up_early("限流等45秒"), "")
ck("超时同样提前收手", RP._give_up_early("超时"), "")

# ---------- 4) 网络挂死：wait_for 必须掐断 ----------
c_hang = FakeClient(3, hang=True)
t0 = time.time()
ok, total, err = asyncio.run(RP._do_report_once(c_hang, "P", "R", "text"))
cost = time.time() - t0
print("   \u8fde\u63a5\u6302\u6b7b\uff1a\u8017\u65f6 %.2fs err=%r" % (cost, err))
ck("挂死只等一个超时就收手",
   cost < RP.REPORT_API_TIMEOUT + 5, "%.1fs" % cost)
ck("挂死时不再试后续请求", len(c_hang.seen_threshold) == 1,
   c_hang.seen_threshold)
ck("超时原因可见", "超时" in (err or ""), err)

# ---------- 5) 全健康：正常 3/3 ----------
c_ok = FakeClient(4)
ok, total, err = asyncio.run(RP._do_report_once(c_ok, "P", "R", "text", msg_ids=[1]))
ck("健康账号 3/3", (ok, total) == (3, 3), (ok, total, err))
ck("健康无错误", err == "", err)

# ---------- 6) _resolve 有超时 ----------
c_r = FakeClient(5, hang=True)
t0 = time.time()
try:
    asyncio.run(RP._resolve(c_r, "x"))
    ck("_resolve 会超时报错", False, "没抛")
except Exception as e:
    ck("_resolve 会超时报错", True, type(e).__name__)
ck("_resolve 不永挂", time.time() - t0 < RP.REPORT_API_TIMEOUT * 2 + 5, "%.1fs" % (time.time() - t0))

# ---------- 7) 结果里带原因（原来静默 pass 看不见）----------
r = []
RP._score(r, "1", 0, 3, "限流等45秒")
ck("失败原因进结果文本", "限流等45秒" in r[0][1], r)
r2 = []
RP._score(r2, "2", 3, 3, "")
ck("成功不拖原因", r2[0][1].startswith("✅"), r2)

# ---------- 8) 进度每号刷新 ----------
src = open(D + r"\reporter.py", encoding="utf-8").read()
ck("刷新间隔可配", "REPORT_REFRESH_EVERY" in src, "")
ck("不再攒 3 个才刷", "done % 3 == 0" not in src, "还有")

# ---------- 9) AI 输出长度与实际用量对齐 ----------
A = open(D + r"\ai.py", encoding="utf-8").read()
ck("AI 不再要 300-450 词", "300 to 450 words" not in A, "旧要求还在")
ck("AI 改为 ~100 词", "90 to 110 words" in A and "80 to 100 words" in A, "")
ck("max_tokens 可传参", "max_tokens: int = 4096" in A and '"max_tokens": max_tokens' in A, "")
ck("生成请求降到 900", A.count("max_tokens=900") == 2, A.count("max_tokens=900"))

print("\n" + ("REPORT_SLOW_OK" if not FAILS else "FAILED: %s" % FAILS))
sys.exit(1 if FAILS else 0)
