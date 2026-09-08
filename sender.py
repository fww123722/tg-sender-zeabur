#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群发引擎：多账号并发私聊、群组广播、频道转发。支持纯文本与图文/文件群发。"""
import asyncio
import random

from telethon.errors import FloodWaitError
from telethon.tl.types import InputPeerUser

from config import BATCH_SIZE, BATCH_SLEEP, MAX_FLOOD_WAIT, log, state
from db import (
    db_add_sent,
    db_bump_sent,
    db_load_sent,
    db_load_stats,
)


async def safe_send(client, entity, text):
    """带 FLOOD_WAIT 自动等待的发送封装（按 state.parse_mode 解析文本格式）。"""
    pm = state.get("parse_mode") or None
    try:
        await client.send_message(entity, text, parse_mode=pm)
        return True, None
    except FloodWaitError as e:
        wait = e.seconds
        log.warning(f"⏳ flood wait {wait}s")
        if wait <= MAX_FLOOD_WAIT:
            await asyncio.sleep(wait)
            try:
                await client.send_message(entity, text, parse_mode=pm)
                return True, None
            except Exception as e2:
                return False, str(e2)
        return False, f"flood_wait_too_long:{wait}s"
    except Exception as e:
        return False, str(e)


async def safe_send_media(client, entity, text, file=None, image=None):
    """带图文/文件/纯文本的发送（按 state.parse_mode 解析格式）。优先发文件，其次图片，最后纯文本。
    返回 (ok, err)。"""
    pm = state.get("parse_mode") or None
    try:
        if file:
            await client.send_file(entity, file, caption=text, parse_mode=pm)
        elif image:
            await client.send_file(entity, image, caption=text, parse_mode=pm)
        else:
            await client.send_message(entity, text, parse_mode=pm)
        return True, None
    except FloodWaitError as e:
        wait = e.seconds
        log.warning(f"⏳ flood wait {wait}s")
        if wait <= MAX_FLOOD_WAIT:
            await asyncio.sleep(wait)
            try:
                if file:
                    await client.send_file(entity, file, caption=text, parse_mode=pm)
                elif image:
                    await client.send_file(entity, image, caption=text, parse_mode=pm)
                else:
                    await client.send_message(entity, text, parse_mode=pm)
                return True, None
            except Exception as e2:
                return False, str(e2)
        return False, f"flood_wait_too_long:{wait}s"
    except Exception as e:
        return False, str(e)


def _is_bad_peer(err) -> bool:
    """判定是否为 access_hash 失效/实体不可达类错误（可尝试重新解析重试）"""
    e = (err or "").lower()
    return (
        "invalid peer" in e
        or "peer_id_invalid" in e
        or "could not find" in e
        or "peer user is invalid" in e
    )


def _is_rate_limited(err) -> bool:
    """判定是否为限流类错误（430 Too many requests / 慢模式等），值得退避后重试。
    靠错误文本判定，不依赖具体 TL 异常类（telethon 未导出 TooManyRequests 类）。
    FloodWaitError 已在 safe_send* 内部按秒等待过，这里兜住没有 seconds 的硬限流；
    flood_wait_too_long 说明等待已超上限，重试无意义，故排除。"""
    e = (err or "").lower()
    if "flood_wait_too_long" in e:
        return False
    return (
        "too many requests" in e
        or "slowmode" in e
        or "slow mode" in e
        or "rate limit" in e
    )


async def _send_one(client, acc_no, uid, info, text, file=None, image=None, backoff=False):
    """用指定账号给单个目标发一条：实体三级解析 + 限流退避重试 + access_hash 失效兜底。
    返回 (status, err)，status ∈ "ok"/"fail"/"skip"（skip=实体压根解析不出来，不算发送失败）。"""
    uname = (info.get("username") or "").strip().lstrip("@")
    ah = info.get("access_hash", 0) or 0
    entity = None
    # ① 优先 @username：让 Telegram 自己解析，不吃跨账号 access_hash 失效
    if uname:
        try:
            entity = await client.get_entity(uname)
        except Exception as e:
            log.info(f"[账号{acc_no}] @{uname} 解析失败，回退 access_hash: {e}")
            entity = None
    # ② 回退名单里存的 access_hash
    if entity is None and ah:
        try:
            entity = InputPeerUser(int(uid), int(ah))
        except Exception as e:
            log.warning(f"[账号{acc_no}] 构造 InputPeerUser 失败 {uid}: {e}")
            entity = None
    # ③ 再回退：从本账号实体缓存里解析数字 ID
    if entity is None:
        try:
            entity = await client.get_entity(int(uid))
        except Exception as e:
            log.warning(f"[账号{acc_no}] 跳过 {uid}: {e}")
            return "skip", str(e)
    ok, err = await safe_send_media(client, entity, text, file=file, image=image)
    # ④ 限流（430）是**账号级**的：同一个号退避重发实测几乎必败（白等 33s/目标），
    # 正确解法是立即换其他账号（send_to_list_multi 第二轮补发）。
    # backoff=True 仅用于全局只剩 1 个账号、没号可换的场景。
    if backoff:
        for _w in (8, 25):
            if ok or not _is_rate_limited(err):
                break
            log.info(f"[账号{acc_no}] 限流等待 {_w}s 后重试 {uid}（{str(err)[:40]}）")
            await asyncio.sleep(_w)
            ok, err = await safe_send_media(client, entity, text, file=file, image=image)
    # ⑤ access_hash 失效/实体不可达兜底：换一种解析方式再试一次
    if not ok and _is_bad_peer(err):
        try:
            if uname:
                entity = await client.get_entity(uname)
            else:
                entity = await client.get_input_entity(int(uid))
            ok, err = await safe_send_media(client, entity, text, file=file, image=image)
        except Exception as e2:
            err = f"{err} | 重新解析实体也失败: {e2}"
    return ("ok" if ok else "fail"), (None if ok else err)


async def send_to_list_multi(accounts, targets, text, owner_entity, file=None, image=None, bot=None):
    """多个账号轮流派发目标，各自控制频率，并发执行。
    群发过程中定期向 owner 汇总推送进度（百分比 + 各账号明细）。
    汇报统一走控制 Bot（bot 参数），不占用群发账号；bot 缺失时回退账号1。"""
    # targets: {uid: {"username": ..., "access_hash": ...}}

    async def _report(msg_text):
        """统一汇报通道：优先控制 Bot，避免群发账号给 owner 发汇报消息"""
        sender = bot or (accounts[0][1] if accounts else None)
        if sender is None:
            return
        try:
            await sender.send_message(owner_entity, msg_text)
        except Exception:
            log.warning("汇报消息发送失败")
    uid_list = list(targets.keys())

    # 每个账号分配到的子集：轮流均匀分配
    per_account = {acc_no: [] for acc_no, *_ in accounts}
    for i, uid in enumerate(uid_list):
        acc_no = accounts[i % len(accounts)][0]
        per_account[acc_no].append(uid)

    # 共享进度（asyncio 单线程，普通 dict 安全）
    progress = {
        "total": len(uid_list),
        "done": 0,          # 已处理（含跳过/失败）
        "sent": 0,          # 成功发送
        "fail": 0,          # 发送失败
        "skipped": 0,       # 跳过（已发过/解析失败）
        "per_acc": {acc_no: {"sent": 0, "fail": 0} for acc_no, *_ in accounts},
    }
    # 第一轮发送失败的目标：{uid: (acc_no, err)}，供第二轮换账号补发判断
    first_round_fail = {}
    # 本轮已撞过 430 的账号：430 是账号级限流，同轮内不再拿它硬试剩余目标
    rate_limited_acc = set()

    async def progress_reporter():
        """后台协程：每 15 秒向 owner 汇总推送一次进度"""
        while True:
            await asyncio.sleep(15)
            if progress["done"] >= progress["total"] or state["stop"]:
                return
            await _report_progress(owner_entity, accounts, progress)

    async def _report_progress(owner_entity, accounts, progress):
        total = progress["total"] or 1
        done = progress["done"]
        pct = done / total * 100
        # 进度条：10 格，▰ 完成 ▱ 剩余
        filled = int(pct // 10)
        bar = "▰" * filled + "▱" * (10 - filled)
        lines = [
            f"📊 群发进度 {bar} {pct:.0f}%",
            f"   {done}/{total} | ✅ {progress['sent']} ❌ {progress['fail']} ⏭ {progress['skipped']}",
        ]
        # 老板要求：进度不列每账号明细，只报总计
        await _report("\n".join(lines))

    async def worker(client, acc_no, my_uids):
        """单个账号的处理循环"""
        sent_set = db_load_sent(acc_no)
        stats = db_load_stats(acc_no)
        if my_uids:
            # 预热：账号刚连上立刻对外发信最容易吃 430，先随机错开几秒再开跑
            await asyncio.sleep(random.uniform(3, 8))
        for uid in my_uids:
            if state["stop"]:
                return
            if state["paused"]:
                await _report(f"⏸ 账号{acc_no} 已暂停")
                # 暂停时循环等待，不退出
                while state["paused"] and not state["stop"]:
                    await asyncio.sleep(5)
                if state["stop"]:
                    return
            if stats["sent_today"] >= state["daily_limit"]:
                await _report(f"🚫 账号{acc_no} 今日已达上限 {state['daily_limit']} 条，该账号停止")
                return
            if uid in sent_set and not state.get("allow_repeat"):
                progress["skipped"] += 1
                progress["done"] += 1
                continue
            # 同轮内该账号已撞过 430 → 不再硬试（省掉每个目标白等的几十秒），
            # 直接记为限流失败，交给第二轮换号补发
            if len(accounts) > 1 and acc_no in rate_limited_acc:
                progress["done"] += 1
                progress["fail"] += 1
                progress["per_acc"][acc_no]["fail"] += 1
                first_round_fail[uid] = (acc_no, "Too many requests (账号本轮已限流，跳过未试)")
                log.info(f"[账号{acc_no}] 本轮已限流，{uid} 直接转第二轮补发")
                continue
            status, err = await _send_one(client, acc_no, uid, targets.get(uid, {}),
                                          text, file=file, image=image,
                                          backoff=len(accounts) == 1)
            if status == "skip":
                progress["skipped"] += 1
                progress["done"] += 1
                continue
            ok = status == "ok"
            progress["done"] += 1
            if ok:
                progress["sent"] += 1
                progress["per_acc"][acc_no]["sent"] += 1
                db_add_sent(acc_no, uid)
                db_bump_sent(acc_no)
                stats["sent_today"] += 1
                stats["total_sent"] += 1
            else:
                progress["fail"] += 1
                progress["per_acc"][acc_no]["fail"] += 1
                first_round_fail[uid] = (acc_no, err)
                if _is_rate_limited(err):
                    rate_limited_acc.add(acc_no)
                log.warning(f"[账号{acc_no}] 发送失败 {uid}: {err}")
            delay = random.uniform(state["min_delay"], state["max_delay"])
            await asyncio.sleep(delay)
            if (progress["done"] % BATCH_SIZE) == 0:
                await asyncio.sleep(BATCH_SLEEP)

    # 启动进度汇报协程
    reporter = asyncio.create_task(progress_reporter())

    tasks = [
        asyncio.create_task(worker(client, acc_no, per_account[acc_no]))
        for acc_no, client, _ph in accounts
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    # 收尾：停掉汇报协程，发最终汇总
    reporter.cancel()
    try:
        await reporter
    except asyncio.CancelledError:
        pass

    # ===== 第二轮补发：把因限流/实体失效失败的目标，换别的账号再试 =====
    # 场景：目标数 < 账号数时轮转分配，被限流的账号抱着目标死，其余账号全程空跑没用到额度
    retryable = {u: v for u, v in first_round_fail.items()
                 if _is_rate_limited(v[1]) or _is_bad_peer(v[1])}
    if retryable and len(accounts) > 1 and not state["stop"]:
        await _report(f"🔁 第二轮补发：{len(retryable)} 个目标换账号重试")
        for i, (uid, (orig_acc, orig_err)) in enumerate(retryable.items()):
            info = targets.get(uid, {})
            tried = {orig_acc}
            cands = [accounts[(i + j) % len(accounts)] for j in range(1, len(accounts))]
            # 稳定排序：本轮没撞过 430 的账号排前面，已限流的垫后（仍会试，不放过机会）
            cands.sort(key=lambda a: a[0] in rate_limited_acc)
            for acc_no2, client2, _ph2 in cands:
                if state["stop"]:
                    break
                if acc_no2 in tried:
                    continue
                tried.add(acc_no2)
                if uid in db_load_sent(acc_no2):
                    continue
                st, err2 = await _send_one(client2, acc_no2, uid, info, text,
                                           file=file, image=image, backoff=False)
                if st == "fail" and _is_rate_limited(err2):
                    rate_limited_acc.add(acc_no2)
                if st == "ok":
                    pa = progress["per_acc"].get(orig_acc)
                    if pa and pa["fail"] > 0:
                        pa["fail"] -= 1
                    progress["fail"] = max(0, progress["fail"] - 1)
                    progress["sent"] += 1
                    pa2 = progress["per_acc"].get(acc_no2)
                    if pa2 is not None:
                        pa2["sent"] += 1
                    db_add_sent(acc_no2, uid)
                    db_bump_sent(acc_no2)
                    log.info(f"[账号{acc_no2}] 第二轮补发成功 {uid}"
                             f"（原账号{orig_acc} 失败：{str(orig_err)[:40]}）")
                    break
                await asyncio.sleep(random.uniform(state["min_delay"], state["max_delay"]))

    pct = 100.0 if not progress["total"] else (progress["done"] / progress["total"] * 100)
    # 老板要求：结果不列每账号明细；总计用全局计数器（per_acc 仍内部维护，供补发回冲）
    summary = (f"✅ 多账号群发完成（{len(accounts)}个账号，{pct:.0f}%）\n"
               f"合计：成功 {progress['sent']}，失败 {progress['fail']}，跳过 {progress['skipped']}")
    await _report(summary)
    return summary


async def broadcast_to_groups(client, group_args, text, owner_entity, file=None, image=None):
    groups = [g.strip() for g in group_args.split(",") if g.strip()]
    ok_cnt = 0
    fail_cnt = 0
    for g in groups:
        try:
            entity = await client.get_entity(g)
            ok, err = await safe_send_media(client, entity, text, file=file, image=image)
            if ok:
                ok_cnt += 1
            else:
                fail_cnt += 1
                log.warning(f"广播失败 {g}: {err}")
        except Exception as e:
            fail_cnt += 1
            log.warning(f"找不到群 {g}: {e}")
        await asyncio.sleep(random.uniform(state["min_delay"], state["max_delay"]))
    return f"✅ 广播完成：成功 {ok_cnt} 个群，失败 {fail_cnt} 个"


async def forward_from_channel(client, src_arg, dst_arg, owner_entity, count=5):
    try:
        src = await client.get_entity(src_arg)
        dst = await client.get_entity(dst_arg)
    except Exception as e:
        return f"❌ 无法解析源/目标: {e}"
    ok_cnt = 0
    try:
        async for msg in client.iter_messages(src, limit=count):
            if msg.media or msg.message or msg.entities:
                try:
                    await client.send_message(dst, msg.text, file=msg.media)
                    ok_cnt += 1
                except Exception as e:
                    log.warning(f"转发失败: {e}")
                await asyncio.sleep(random.uniform(state["min_delay"], state["max_delay"]))
    except Exception as e:
        return f"❌ 读取频道失败: {e}"
    return f"✅ 转发完成：成功 {ok_cnt} 条到目标"
