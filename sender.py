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
    """带 FLOOD_WAIT 自动等待的发送封装。"""
    try:
        await client.send_message(entity, text)
        return True, None
    except FloodWaitError as e:
        wait = e.seconds
        log.warning(f"⏳ flood wait {wait}s")
        if wait <= MAX_FLOOD_WAIT:
            await asyncio.sleep(wait)
            try:
                await client.send_message(entity, text)
                return True, None
            except Exception as e2:
                return False, str(e2)
        return False, f"flood_wait_too_long:{wait}s"
    except Exception as e:
        return False, str(e)


async def safe_send_media(client, entity, text, file=None, image=None):
    """带图文/文件/纯文本的发送。优先发文件，其次图片，最后纯文本。
    返回 (ok, err)。"""
    try:
        if file:
            await client.send_file(entity, file, caption=text)
        elif image:
            await client.send_file(entity, image, caption=text)
        else:
            await client.send_message(entity, text)
        return True, None
    except FloodWaitError as e:
        wait = e.seconds
        log.warning(f"⏳ flood wait {wait}s")
        if wait <= MAX_FLOOD_WAIT:
            await asyncio.sleep(wait)
            try:
                if file:
                    await client.send_file(entity, file, caption=text)
                elif image:
                    await client.send_file(entity, image, caption=text)
                else:
                    await client.send_message(entity, text)
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


async def send_to_list_multi(accounts, targets, text, owner_entity, file=None, image=None):
    """多个账号轮流派发目标，各自控制频率，并发执行。
    群发过程中定期向 owner 汇总推送进度（百分比 + 各账号明细）。"""
    # targets: {uid: {"username": ..., "access_hash": ...}}
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

    async def progress_reporter():
        """后台协程：每 15 秒向 owner 汇总推送一次进度"""
        while True:
            await asyncio.sleep(15)
            if progress["done"] >= progress["total"] or state["stop"]:
                return
            await _report_progress(owner_entity, accounts, progress)

    async def _report_progress(owner_entity, accounts, progress):
        pct = (progress["done"] / progress["total"] * 100) if progress["total"] else 100.0
        lines = [
            f"📊 群发进度：{progress['done']}/{progress['total']}（{pct:.0f}%）",
            f"   成功 {progress['sent']} | 失败 {progress['fail']} | 跳过 {progress['skipped']}",
        ]
        for acc_no, _client, _ph in accounts:
            pa = progress["per_acc"].get(acc_no, {"sent": 0, "fail": 0})
            lines.append(f"   [账号{acc_no}] 成功 {pa['sent']} | 失败 {pa['fail']}")
        try:
            await accounts[0][1].send_message(owner_entity, "\n".join(lines))
        except Exception:
            log.warning("进度消息发送失败")

    async def worker(client, acc_no, my_uids):
        """单个账号的处理循环"""
        sent_set = db_load_sent(acc_no)
        stats = db_load_stats(acc_no)
        for uid in my_uids:
            if state["stop"]:
                return
            if state["paused"]:
                try:
                    await client.send_message(owner_entity, f"⏸ 账号{acc_no} 已暂停")
                except Exception:
                    pass
                # 暂停时循环等待，不退出
                while state["paused"] and not state["stop"]:
                    await asyncio.sleep(5)
                if state["stop"]:
                    return
            if stats["sent_today"] >= state["daily_limit"]:
                try:
                    await client.send_message(
                        owner_entity,
                        f"🚫 账号{acc_no} 今日已达上限 {state['daily_limit']} 条，该账号停止",
                    )
                except Exception:
                    pass
                return
            if uid in sent_set:
                progress["skipped"] += 1
                progress["done"] += 1
                continue
            info = targets.get(uid, {})
            ah = info.get("access_hash", 0) or 0
            if ah:
                try:
                    entity = InputPeerUser(int(uid), int(ah))
                except Exception as e:
                    log.warning(f"[账号{acc_no}] 构造 InputPeerUser 失败 {uid}: {e}")
                    progress["skipped"] += 1
                    progress["done"] += 1
                    continue
            else:
                try:
                    entity = await client.get_entity(int(uid))
                except Exception as e:
                    log.warning(f"[账号{acc_no}] 跳过 {uid}: {e}")
                    progress["skipped"] += 1
                    progress["done"] += 1
                    continue
            ok, err = await safe_send_media(client, entity, text, file=file, image=image)
            if not ok and _is_bad_peer(err):
                # access_hash 无效/过期兜底：重新解析实体再试一次
                try:
                    entity = await client.get_input_entity(int(uid))
                    ok, err = await safe_send_media(client, entity, text, file=file, image=image)
                except Exception as e2:
                    err = f"{err} | 重新解析实体也失败: {e2}"
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

    pct = 100.0 if not progress["total"] else (progress["done"] / progress["total"] * 100)
    parts = []
    total_s = total_f = 0
    for acc_no, _client, _ph in accounts:
        pa = progress["per_acc"].get(acc_no, {"sent": 0, "fail": 0})
        s, f = pa["sent"], pa["fail"]
        total_s += s
        total_f += f
        parts.append(f"账号{acc_no}:成功{s}失败{f}")
    try:
        await accounts[0][1].send_message(
            owner_entity,
            f"✅ 多账号群发完成（{len(accounts)}个账号，{pct:.0f}%）\n"
            + "\n".join(parts)
            + f"\n合计：成功 {total_s}，失败 {total_f}，跳过 {progress['skipped']}",
        )
    except Exception:
        pass
    return f"✅ 多账号群发完成（{len(accounts)}个账号）\n" + "\n".join(parts) + f"\n合计：成功 {total_s}，失败 {total_f}，跳过 {progress['skipped']}"


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
