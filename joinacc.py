# -*- coding: utf-8 -*-
"""加群账号：一个号专职加群，不参与群发。

老板 09-15：不用手动设——自动把「加入群最多」的那个号定为加群号，在账号管理里
标明；该号还没进去的群，自动补加进去。

设计：
  - 选定的号记在 ops_state（key=join_acc），跨重启生效；进程内缓存，发送热路径不查库。
  - ensure()：号还活着就沿用；掉线/没定过 → 重新选（在线号里群最多的那个）。
  - send_pool()：群发选号时把加群号剔出去（加群号只加群，不发货）。
  - pick_for_join()：加群优先只用加群号，它不活着才退回全池逐号试。
  - backfill()：groups_info 里有、但加群号不在里的公开群 → 自动补加。
"""
import asyncio

import ops_state
from config import log

K_JOIN_ACC = "join_acc"
_CACHE = {"v": None, "loaded": False}   # 进程序缓存：DB 只读一次


async def _alive(client):
    """客户端还能用吗？

    坑：is_user_authorized() 是 async，漏 await 会让判定永远为真（filter.py 栽过）。
    """
    try:
        if await client.is_user_authorized():
            return True
        await client.connect()
        return bool(await client.is_user_authorized())
    except Exception:
        return False


def get_join_acc():
    """当前加群号序号；没定过/读不到返回 None（读库失败绝不拦发送）。"""
    if _CACHE["loaded"]:
        return _CACHE["v"]
    try:
        v = ops_state.get(K_JOIN_ACC)
        v = int(v) if v else None
    except Exception:
        v = None
    _CACHE.update({"v": v, "loaded": True})
    return v


def set_join_acc(no, reason=""):
    try:
        ops_state.set(K_JOIN_ACC, int(no))
        _CACHE.update({"v": int(no), "loaded": True})
        log.info(f"[加群号] 定为 账号{no}（{reason}）")
        return True
    except Exception as e:
        log.warning(f"[加群号] 写入失败: {e}")
        return False


def send_pool(accounts):
    """群发用号池：剔掉加群号。只剩加群号时不剔（宁可让它发，也别罢工）。"""
    accs = list(accounts or [])
    jo = get_join_acc()
    if jo is None or len(accs) <= 1:
        return accs
    keep = [a for a in accs if a[0] != jo]
    return keep or accs


def join_row(accounts):
    """取加群号那一行 (acc_no, client, phone)；没定过/不在线返回 None。"""
    jo = get_join_acc()
    if jo is None:
        return None
    return next((a for a in list(accounts or []) if a[0] == jo), None)


async def pick_for_join(accounts):
    """加群选号：加群号活着就只用它；否则退回全池逐号试。"""
    row = join_row(accounts)
    if row and await _alive(row[1]):
        return [row]
    return list(accounts or [])


async def _group_count(client):
    """该账号在本系统里登了多少个群（老板 09-15：只算 groups_info 登记的群，
    账号自己历史加的杂群/频道不算，否则数字虚高没法比）。

    返回 (登记群数, 全部群/频道数)；第二个值只用于日志兜底说明。
    """
    from db import db_get_all_groups
    try:
        registered = {abs(int(g[0])) for g in (db_get_all_groups() or [])}
    except Exception as e:
        log.warning(f"[加群号] 读 groups_info 失败: {type(e).__name__}: {e}")
        registered = set()
    mine, total = 0, 0
    async for d in client.iter_dialogs():
        if getattr(d.entity, "title", None) is None:
            continue
        total += 1
        if not registered or abs(int(d.id)) in registered:
            mine += 1
    return mine, total


async def ensure(accounts, notify=None):
    """确保有可用的加群号：沿用 → 否则选群最多的。返回一行简报文本。"""
    accs = list(accounts or [])
    if not accs:
        return "⚠️ 没有在线账号，加群号待定。"
    row = join_row(accs)
    if row and await _alive(row[1]):
        return f"🔑 加群号：账号{row[0]}（沿用）"

    scored = []
    for acc_no, client, _ph in accs:
        if not await _alive(client):
            log.info(f"[加群号] 账号{acc_no} 不可用，跳过")
            continue
        try:
            n, _total = await _group_count(client)
        except Exception as e:
            log.warning(f"[加群号] 账号{acc_no} 数群失败 {type(e).__name__}: {e}")
            n = -1
        scored.append((n, acc_no))
        await asyncio.sleep(0.2)

    if not scored:
        return "⚠️ 所有账号都连不上，加群号待定。"
    scored.sort(key=lambda x: (-x[0], x[1]))
    best_n, best_no = scored[0]
    detail = "、".join(f"账号{n}={c}群" for c, n in scored if c >= 0)[:120]
    set_join_acc(best_no, f"在群最多（{detail}）")
    tip = (f"🔑 加群号：账号{best_no}（在群 {max(best_n, 0)} 个，自动选定）\n"
           f"　口径：只数本系统登记的群，账号自带的杂群不算")
    if notify:
        try:
            await notify(tip)
        except Exception:
            pass
    return tip


async def _in_group(client, gid):
    """加群号是否已在该群（能解析到实体=在）。"""
    try:
        from collector import _resolve_entity
        await _resolve_entity(client, int(gid))
        return True
    except Exception:
        return False


async def backfill(accounts, notify=None):
    """把加群号没进去的群补加上（私密邀请链接没存档，补不了，只报数）。

    返回 (成功数, 没加上/跳过数, 简报文本)。"""
    from db import db_get_all_groups
    rows_all = db_get_all_groups() or []
    accs = list(accounts or [])
    row = join_row(accs)
    if not row or not await _alive(row[1]):
        return 0, 0, "🔑 加群号还没定或不在线，跳过补加。"
    acc_no, client, _ph = row

    missing = []
    for gid, title, username, _mc, _c in rows_all:
        if await _in_group(client, gid):
            continue
        missing.append((int(gid), title or "(无标题)", username or ""))
    if not missing:
        return 0, 0, f"🔑 加群号账号{acc_no}：{len(rows_all)} 个群都在，无需补加。"

    ok, skipped, lines = 0, 0, []
    for gid, title, uname in missing:
        if not uname:
            skipped += 1
            lines.append(f"  ⏭ 「{title}」私密群（无存档链接）")
            continue
        try:
            from collector import join_group_by_link
            txt, ent = await join_group_by_link(client, f"t.me/{uname}", acc=str(acc_no))
            first = str(txt).splitlines()[0][:70]
        except Exception as e:
            first = f"❌ {type(e).__name__}: {str(e)[:50]}"
            ent = None
        if ent is not None or first.startswith("✅"):
            ok += 1
            lines.append(f"  ✅ 「{title}」")
        else:
            skipped += 1
            lines.append(f"  ⚠️ 「{title}」{first}")
        await asyncio.sleep(1.5)

    body = (f"🔑 加群号账号{acc_no} 补加：成功 {ok}，没加上/跳过 {skipped}"
            f"（共缺 {len(missing)}）\n" + "\n".join(lines[:12]))
    if len(lines) > 12:
        body += f"\n  …另 {len(lines) - 12} 条见日志"
    log.info(body.replace("\n", " | "))
    if notify and (ok or skipped):
        try:
            await notify(body)
        except Exception:
            pass
    return ok, skipped, body


async def boot(accounts, bot=None, owner=None):
    """启动时跑一轮：定加群号 → 补缺口群。失败不影响主流程。"""
    async def notify(text):
        if bot is None or not owner:
            return
        try:
            await bot.send_message(owner, text)
        except Exception as e:
            log.info(f"[加群号] 通知失败(忽略): {type(e).__name__}")
    try:
        tip = await ensure(accounts, notify=notify)
        log.info(tip)
        await asyncio.sleep(3)
        await backfill(accounts, notify=notify)
    except Exception as e:
        log.error(f"[加群号] 启动处理失败（不影响群发）: {type(e).__name__}: {e}")
