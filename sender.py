#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群发引擎：多账号并发私聊、群组广播、频道转发。支持纯文本与图文/文件群发。"""
import asyncio
import os
import random
import time

from telethon.errors import FloodWaitError
from telethon.tl.types import InputPeerUser

from config import BATCH_SIZE, BATCH_SLEEP, COOLDOWN_SEC, MAX_FLOOD_WAIT, log, state

# 群发全程只维护「一条」进度消息（老板要求）：
#   REPORT_INTERVAL 默认 20 秒心跳一次；状态变化（暂停/达上限）走 dirty 提前刷一次。
#   两次真实编辑之间强制拉开 EDIT_MIN_INTERVAL，避免自己撞 Telegram 的 edit 频控。
REPORT_INTERVAL = float(os.environ.get("REPORT_INTERVAL", "20"))
EDIT_MIN_INTERVAL = float(os.environ.get("EDIT_MIN_INTERVAL", "1.2"))
# 暂停标记（多个 worker 共用，靠它保证只刷一次）
PAUSE_NOTE = "⏸已暂停"
from db import (
    db_add_sent,
    db_bump_sent,
    db_clear_cooldown,
    db_cooldowns,
    db_load_sent,
    db_load_stats,
    db_mark_cooldown,
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


def _needs_cooldown(err) -> bool:
    """是否该给账号记冷却：430/慢模式，以及 FloodWait 超过 MAX_FLOOD_WAIT 放弃等待的情况"""
    e = (err or "").lower()
    return _is_rate_limited(e) or "flood_wait_too_long" in e


def _cooldown_seconds(err) -> int:
    """冷却时长：FloodWait 给了真实秒数就按它（不超过 COOLDOWN_SEC 的 4 倍），否则用默认"""
    import re
    m = re.search(r"flood_wait_too_long:(\d+)s", err or "")
    if m:
        return min(int(m.group(1)), COOLDOWN_SEC * 4)
    return COOLDOWN_SEC


def _is_not_modified(e) -> bool:
    """内容无变化时 Telegram 会报错，当作正常处理（避免因此新发一条消息）。"""
    return (type(e).__name__ == "MessageNotModifiedError"
            or "not modified" in str(e).lower())


def _mark_cooldown(acc_no, err):
    """撞限流→写冷却记账（跨批次、跨重启生效；DB 故障绝不影响群发）"""
    try:
        db_mark_cooldown(acc_no, _cooldown_seconds(err), str(err or "")[:80])
    except Exception as e:
        log.warning(f"[账号{acc_no}] 冷却记账失败: {e}")


# 进度消息的按钮占位：不传就等于「保留原键盘」，不许偷偷清空。
_KEEP = object()
_edit_at = {"t": 0.0}


async def _throttle_edit():
    """同一条进度消息两次真编辑之间拉开 EDIT_MIN_INTERVAL，从源头不自造限流。"""
    delta = EDIT_MIN_INTERVAL - (time.time() - _edit_at["t"])
    if delta > 0:
        await asyncio.sleep(delta)
    _edit_at["t"] = time.time()


def _is_gone(e) -> bool:
    """原消息已不存在/无权改（被删、被清）：这种情况才允许重发一条顶上去。"""
    low = str(e).lower()
    return ("message_id_invalid" in low or "message_to_edit_not_found" in low
            or "chat_write_forbidden" in low or "messages_not_modified" in low)


async def send_to_list_multi(accounts, targets, text, owner_entity, file=None, image=None,
                             bot=None, msg=None, pool=None, ctl_kb=None):
    """多账号从共享队列领目标并发发送。

    全程只维护**一条**进度消息（老板要求）：开始/进度/暂停/汇总全部原地编辑
    msg 那一条，编辑撞限流就退避重试，绝不因为改不动而退化成新发一条。
    pool：文案池文本列表，传了则每个目标随机挑一条（轮换文案）。
    ctl_kb：paused -> 内联键盘，让进度消息自己带「暂停/取消」按钮。
    汇报统一走控制 Bot（bot 参数），不占用群发账号；bot 缺失时回退账号1。"""
    # targets: {uid: {"username": ..., "access_hash": ...}}
    ctl_kb = ctl_kb if callable(ctl_kb) else None

    def _kb_now():
        """进度的键盘：带了控制键盘就跟着状态切，没带则保留原样（不清空）。"""
        if ctl_kb is None:
            return _KEEP
        return ctl_kb("paused" if state.get("paused") else "run")

    async def _report(msg_text, buttons=_KEEP):
        """只改那一条消息：节流 -> 撞限流退避重试 -> 才考虑重发。"""
        nonlocal msg
        sender = bot or (accounts[0][1] if accounts else None)
        if sender is None:
            return
        if msg is None:
            try:
                kb = _kb_now()
                msg = await sender.send_message(
                    owner_entity, msg_text,
                    **({} if kb is _KEEP else {"buttons": kb}))
            except Exception:
                log.warning("汇报消息发送失败")
            return
        for _attempt in range(5):
            await _throttle_edit()
            try:
                msg = await (msg.edit(msg_text) if buttons is _KEEP
                             else msg.edit(msg_text, buttons=buttons))
                return
            except Exception as e:
                if _is_not_modified(e):
                    return
                secs = getattr(e, "seconds", None)
                low = str(e).lower()
                if secs is not None or "flood" in low or "slowdown" in low \
                        or "try again later" in low:
                    await asyncio.sleep(min(int(secs or 0) or 2, 6))
                    continue
                if _is_gone(e):
                    # 原消息已被删：重发一条顶上去，仍然全场只有这一条
                    log.info("进度消息已不在，重发一条顶上去：%s", e)
                    try:
                        kb = _kb_now()
                        msg = await sender.send_message(
                            owner_entity, msg_text,
                            **({} if kb is _KEEP else {"buttons": kb}))
                    except Exception:
                        log.warning("汇报消息发送失败")
                    return
                log.warning("进度消息编辑失败，稍后重试：%s", str(e)[:120])
                await asyncio.sleep(1)
        log.warning("进度消息多次编辑失败，保留旧文等下个周期再改")

    def _pick_text():
        return random.choice(pool) if pool else text
    uid_list = list(targets.keys())

    # 冷却记账：上一批撞过 430 的号，到点前不参与本轮分配（护号，避免反复硬撞拉长限流窗口）
    try:
        cooling = db_cooldowns()
    except Exception as e:
        log.warning(f"读取冷却记账失败，按全部可用处理: {e}")
        cooling = {}
    avail = [a for a in accounts if a[0] not in cooling]
    if not avail:
        # 全在冷却中：不能因此罢工，按原样跑，但在结果里提醒老板
        cooling_note = f"全部 {len(accounts)} 个账号都在冷却中，仍强制开跑"
        avail = list(accounts)
    elif cooling:
        cooling_note = (f"{len(accounts) - len(avail)} 个账号冷却中，本轮不派活"
                        f"（剩 {'、'.join(str(cooling[a[0]]['seconds'] // 60) + '分钟' for a in accounts if a[0] in cooling)[:60]}）")
    else:
        cooling_note = ""
    if cooling_note:
        log.info(f"⏳ 冷却记账：{cooling_note}")

    # 共享任务队列：不再预先切分目标。健康账号发完一条立刻领下一条，
    # 额度多的号自然多发货（老板要求：一个账号尽可能多发）；
    # 撞 430 的号把目标交还队列，由其他号接手，自己收手不再硬试。
    task_q = asyncio.Queue()
    for uid in uid_list:
        task_q.put_nowait(uid)

    # 共享进度（asyncio 单线程，普通 dict 安全）
    progress = {
        "total": len(uid_list),
        "done": 0,          # 已处理（含跳过/失败）
        "sent": 0,          # 成功发送
        "fail": 0,          # 发送失败
        "skipped": 0,       # 跳过（已发过/解析失败）
        "per_acc": {acc_no: {"sent": 0, "fail": 0} for acc_no, *_ in accounts},
        "note": "",         # 临时状态（暂停/限流/补发），挤在同一条进度里
    }
    # 第一轮发送失败的目标：{uid: (acc_no, err)}，供第二轮换账号补发判断
    first_round_fail = {}
    # 本轮已撞过 430 的账号：430 是账号级限流，同轮内不再拿它硬试剩余目标
    rate_limited_acc = set()
    # 每个目标已被真实尝试的次数 / 已终局(成功·永久失败·试遍)的集合
    attempts = {uid: 0 for uid in uid_list}
    finished = set()

    async def progress_reporter():
        """后台协程：每 REPORT_INTERVAL 秒刷一次那唯一的进度消息；
        bot 那边点了暂停/继续/取消会置 state['refresh']，这里秒级响应。"""
        while True:
            slept = 0.0
            while slept < REPORT_INTERVAL:
                await asyncio.sleep(0.5)
                slept += 0.5
                if state.get("refresh") or state["stop"]:
                    break
            state["refresh"] = False
            if progress["done"] >= progress["total"] or state["stop"]:
                return
            await _report_progress()

    def _snap():
        """进度文本（精简到两行，不列每账号明细）。"""
        total = progress["total"] or 1
        done = progress["done"]
        pct = done / total * 100
        filled = int(min(100.0, pct) * 8 // 100)
        bar = "▰" * filled + "▱" * (8 - filled)
        line = f"📊 {bar} {pct:.0f}%  {done}/{total}"
        tail = f"✅{progress['sent']} ❌{progress['fail']} ⏭{progress['skipped']}"
        # 状态自己读全局标：worker 在睡眠里还没发现暂停时，这条也不会漏说
        note = progress.get("note") or ("🛑停止中" if state["stop"]
                                        else (PAUSE_NOTE if state.get("paused") else ""))
        if note:
            tail += f"  {note}"
        return line + "\n" + tail

    async def _report_progress():
        snap = _snap()
        # 给「📈 查看进度」按钮读（它不能自己编一份假的）；带上 msg 让控制动作能原地改它
        state["live"] = {"text": snap, "at": int(time.time()), "msg": msg}
        await _report(snap, buttons=_kb_now())

    async def worker(client, acc_no):
        """从共享队列不断领目标：能发就一直发（尽可能多发），
        撞 430 立刻交还队列换号并收手，不占着目标死等。"""
        sent_set = db_load_sent(acc_no)
        stats = db_load_stats(acc_no)
        warmed = False
        while not state["stop"]:
            # 本号已撞过 430（账号级限流）→ 收手，把剩余目标留给其他号
            if len(avail) > 1 and acc_no in rate_limited_acc:
                return
            if stats["sent_today"] >= state["daily_limit"]:
                progress["note"] = f"🚫 账号{acc_no} 达今日上限"
                await _report_progress()
                return
            try:
                uid = task_q.get_nowait()
            except asyncio.QueueEmpty:
                return
            if not warmed:
                # 预热：账号刚连上立刻对外发信最容易吃 430，先随机错开几秒
                warmed = True
                await asyncio.sleep(random.uniform(3, 8))
            if state["paused"]:
                # 多个 worker 同时发现暂停：只有第一个立刻刷那条消息，不重复编辑
                if progress["note"] != PAUSE_NOTE:
                    progress["note"] = PAUSE_NOTE
                    await _report_progress()
                while state["paused"] and not state["stop"]:
                    await asyncio.sleep(5)
                progress["note"] = ""
                if state["stop"]:
                    task_q.put_nowait(uid)
                    return
            if uid in sent_set and not state.get("allow_repeat"):
                progress["skipped"] += 1
                progress["done"] += 1
                finished.add(uid)
                continue
            attempts[uid] += 1
            status, err = await _send_one(client, acc_no, uid, targets.get(uid, {}),
                                          _pick_text(), file=file, image=image,
                                          backoff=len(avail) == 1)
            if status == "skip":
                progress["skipped"] += 1
                progress["done"] += 1
                finished.add(uid)
                continue
            # 限流/实体类失败且还有号可换 → 交还队列让别人接手
            if status == "fail" and (_is_rate_limited(err) or _is_bad_peer(err)) \
                    and attempts[uid] < max(1, len(avail)):
                if _needs_cooldown(err):
                    rate_limited_acc.add(acc_no)
                    _mark_cooldown(acc_no, err)
                task_q.put_nowait(uid)
                log.info(f"[账号{acc_no}] {uid} 失败({str(err)[:32]})，"
                         f"已交还队列换号（第{attempts[uid]}次尝试）")
                if acc_no in rate_limited_acc and len(avail) > 1:
                    return
                continue
            # 终局：成功 / 永久失败 / 已试遍可用账号
            finished.add(uid)
            progress["done"] += 1
            if status == "ok":
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
                if _needs_cooldown(err):
                    rate_limited_acc.add(acc_no)
                    _mark_cooldown(acc_no, err)
                log.warning(f"[账号{acc_no}] 发送失败 {uid}: {err}")
            delay = random.uniform(state["min_delay"], state["max_delay"])
            await asyncio.sleep(delay)
            if (progress["done"] % BATCH_SIZE) == 0:
                await asyncio.sleep(BATCH_SLEEP)

    # 启动进度汇报协程
    reporter = asyncio.create_task(progress_reporter())

    tasks = [
        asyncio.create_task(worker(client, acc_no))
        for acc_no, client, _ph in avail
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    # 收尾：所有可用号都提前收手（全限流/全达上限）时队列里可能还有没派发的目标，
    # 计入失败并交给第二轮，保证 total/done 对得上、不漏人
    for uid in uid_list:
        if uid in finished:
            continue
        progress["done"] += 1
        progress["fail"] += 1
        finished.add(uid)
        first_round_fail[uid] = (0, "Too many requests (未派发：可用账号已全部限流或达上限)")

    # 收尾：停掉汇报协程，发最终汇总
    reporter.cancel()
    try:
        await reporter
    except asyncio.CancelledError:
        pass

    # ===== 第二轮补发：兜住队列里"试遍可用账号仍失败"与"未派发"的目标 =====
    # 正常路径下队列已自动完成换号交接（worker 撞 430 会把目标放回队列），
    # 这里只处理 worker 全部收手后仍未送达的目标，用 avail 轮转再试一轮
    retryable = {u: v for u, v in first_round_fail.items()
                 if _is_rate_limited(v[1]) or _is_bad_peer(v[1])}
    if retryable and len(avail) > 1 and not state["stop"]:
        progress["note"] = f"🔁 换号补发 {len(retryable)}"
        await _report_progress()
        for i, (uid, (orig_acc, orig_err)) in enumerate(retryable.items()):
            info = targets.get(uid, {})
            tried = {orig_acc}
            cands = [avail[(i + j) % len(avail)] for j in range(1, len(avail))]
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
                st, err2 = await _send_one(client2, acc_no2, uid, info, _pick_text(),
                                           file=file, image=image, backoff=False)
                if st == "fail" and _needs_cooldown(err2):
                    rate_limited_acc.add(acc_no2)
                    _mark_cooldown(acc_no2, err2)
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
                    # 该号实测能发出去 → 解除它身上的冷却（防止误判长期锁死好号）
                    try:
                        db_clear_cooldown(acc_no2)
                        rate_limited_acc.discard(acc_no2)
                    except Exception:
                        pass
                    log.info(f"[账号{acc_no2}] 第二轮补发成功 {uid}"
                             f"（原账号{orig_acc} 失败：{str(orig_err)[:40]}）")
                    break
                await asyncio.sleep(random.uniform(state["min_delay"], state["max_delay"]))

    # 收尾：结果也写回同一条消息（不另发），文本精简到两行
    progress["note"] = ""
    total = progress["total"] or 1
    pct = progress["done"] / total * 100
    filled = int(min(100.0, pct) * 8 // 100)
    bar = "▰" * filled + "▱" * (8 - filled)
    summary = (f"✅ 群发完成 {bar} {pct:.0f}%\n"
               f"{progress['sent']}/{total}  ✅成功 {progress['sent']}"
               f" ❌失败 {progress['fail']} ⏭跳过 {progress['skipped']}")
    tail = []
    if cooling_note:
        tail.append(f"⏳ {len(accounts) - len(avail)} 号冷却未派活")
    if rate_limited_acc:
        tail.append(f"⏳ {len(rate_limited_acc)} 号记了冷却"
                    f"（约 {COOLDOWN_SEC // 60} 分）")
    if tail:
        summary += "\n" + "；".join(tail)
    state["live"] = {"text": summary, "at": int(time.time()), "msg": msg}
    # 收尾：控制按钮摘掉（没活了），没带控制键盘的旧调用点则保留原样
    await _report(summary, buttons=(None if ctl_kb is not None else _KEEP))
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
