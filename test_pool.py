# -*- coding: utf-8 -*-
"""文案池 e2e：真跑 send_to_list_multi，抓每次实际发出的文案。
（_pick_text 是 send_to_list_multi 里的闭包，不能当模块函数单测。）

覆盖：
 A) 传 pool -> 每人发的都在池内，且池够大时确实出现多种
 B) 不传 pool -> 全部用固定文案（= 关随机轮换时 bot.py 的行为）
 C) pool=[] -> 回落固定文案，不崩
 D) 补发第二轮同样走池（换号补发的调用点也用了 _pick_text）
 E) bot.py 侧接线：pool_random 默认值 + 开池但池空时不许开跑
"""
import asyncio
import inspect
import io
import os
import re
import sys

os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "abc")
os.environ.setdefault("BOT_TOKEN", "***")
os.environ.setdefault("OWNER_ID", "111")
os.environ.setdefault("DATABASE_URL", "postgresql://u:***@127.0.0.1:5432/none")

D = r"C:\Users\amdin\.openclaw\workspace-group-bot\projects\tg-sender-zeabur"
sys.path.insert(0, D)
import sender as S            # noqa: E402

FAILS = []
def ck(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  << " + str(extra)[:150]))
    if not cond:
        FAILS.append(name)

# ---------------- 打桩（同 verify_queue 骨架） ----------------
S.db_add_sent = lambda a, u: None
S.db_bump_sent = lambda a: None
S.db_mark_cooldown = lambda *a, **k: None
S.db_clear_cooldown = lambda *a, **k: None
S.db_cooldowns = lambda: {}
_store, _stats = {}, {}
S.db_load_sent = lambda a: _store.setdefault(a, set())
S.db_load_stats = lambda a: _stats.setdefault(a, {"sent_today": 0, "total_sent": 0})
_keep = {k: S.state.get(k) for k in
         ("stop", "paused", "daily_limit", "min_delay", "max_delay",
          "allow_repeat", "parse_mode")}
S.state.update({"stop": False, "paused": False, "daily_limit": 9999,
                "min_delay": 0, "max_delay": 0, "allow_repeat": True, "parse_mode": None})

SENT = []          # 每次发送的 (acc_no, text)
BLK = set()        # 哪些账号永远撞 430

class Cli:
    def __init__(self, no): self.no = str(no)
    async def get_entity(self, e): return f"E:{e}"
    async def get_input_entity(self, e): return f"I:{e}"
    async def send_message(self, *a, **k): return None

async def media(client, entity, text, file=None, image=None):
    who = str(getattr(client, "no", "?"))
    SENT.append((who, text))
    if who in BLK:
        return False, "Too many requests (caused by SendMessageRequest)"
    return True, None
S.safe_send_media = media

_real_sleep = asyncio.sleep
async def fast(x):
    await _real_sleep(0)

def accs(n):
    return [(i, Cli(i), f"p{i}") for i in range(1, n + 1)]

def tgs(n):
    return {f"u{i}": {"username": f"nam{i}", "access_hash": i} for i in range(1, n + 1)}

POOL = ["AAA优惠券", "BBB折扣券", "CCC免费领"]

# ---------------- A) 传 pool：每人随机挑一条 ----------------
asyncio.sleep = fast
try:
    SENT.clear(); BLK.clear(); _store.clear(); _stats.clear()
    r = asyncio.run(S.send_to_list_multi(accs(3), tgs(12), "FIXED", Cli(0), bot=Cli(0), pool=POOL))
    texts = [t for _, t in SENT]
    ck("A: 12个目标全送达", "成功 12，失败 0" in r, r.replace("\n", " | ")[:120])
    ck("A: 发出的全在池内", all(t in POOL for t in texts), set(texts) - set(POOL))
    ck("A: 确实出现多种文案", len(set(texts)) >= 2, sorted(set(texts)))
    ck("A: 没把固定文案发出去", "FIXED" not in texts, set(texts))

    # ---------------- B) 不传 pool：全用固定文案 ----------------
    SENT.clear(); _store.clear(); _stats.clear()
    r = asyncio.run(S.send_to_list_multi(accs(2), tgs(8), "FIXED", Cli(0), bot=Cli(0)))
    texts = [t for _, t in SENT]
    ck("B: 8个全送达", "成功 8，失败 0" in r, r.replace("\n", " | ")[:120])
    ck("B: 全部用固定文案", all(t == "FIXED" for t in texts), sorted(set(texts)))

    # ---------------- C) pool=[] 回落不崩 ----------------
    SENT.clear(); _store.clear(); _stats.clear()
    r = asyncio.run(S.send_to_list_multi(accs(2), tgs(4), "FIXED", Cli(0), bot=Cli(0), pool=[]))
    ck("C: 空 pool 回落固定文案", all(t == "FIXED" for _, t in SENT), sorted({t for _, t in SENT}))
    ck("C: 空 pool 不影响送达", "成功 4" in r, r.replace("\n", " | ")[:110])

    # ---------------- D) 补发第二轮也走池 ----------------
    SENT.clear(); BLK.clear(); _store.clear(); _stats.clear()
    BLK = {"1"}                       # 账号 1 永远 430 → 要换号补发
    r = asyncio.run(S.send_to_list_multi(accs(3), tgs(6), "FIXED", Cli(0), bot=Cli(0), pool=POOL))
    texts = [t for _, t in SENT]
    ck("D: 换号补发后仍全送达", "成功 6，失败 0" in r, r.replace("\n", " | ")[:120])
    ck("D: 补发的文案也在池内", all(t in POOL for t in texts), set(texts) - set(POOL))
    ck("D: 没漏发固定文案", "FIXED" not in texts, sorted(set(texts)))
finally:
    asyncio.sleep = _real_sleep
    for k, v in _keep.items():
        S.state[k] = v

# ---------------- E) bot.py 侧接线 ----------------
B = io.open(D + r"\bot.py", encoding="utf-8", newline="\n").read()
ck("E: pool_random 有默认值", 's.setdefault("pool_random", False)' in B, "无默认")
ck("E: 开池必取文案", "db_pool_texts()" in B, "未取池")
ck("E: 池空时不许开跑",
   bool(re.search(r'pool_random"\)\s+and\s+not\s+pool', B)), "\u7f3a\u7a7a\u6c60\u6821\u9a8c")
ck("E: pool 传到发送", "pool=pool" in B, "pool 未下传")
ck("E: 池入口在菜单", '"menu_pool"' in B and "pool_menu_kb()" in B, "无入口")

ms = inspect.getsource(S.send_to_list_multi)
ck("F: 签名含 pool", "pool=None" in ms, ms[:120])
ck("F: 两处调用点都用 _pick_text", ms.count("_pick_text()") >= 2, ms.count("_pick_text()"))

print("\n" + ("POOL_ALL_OK" if not FAILS else "POOL_FAILED: %s" % FAILS))
sys.exit(1 if FAILS else 0)
