#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""举报核心模块：复用 tg-sender 现有账号池（ACTIVE_ACCOUNTS）发起 Telegram 举报。

设计原则：
- 直接复用 config.ACTIVE_ACCOUNTS（元素是 (acc_no, client, _ph) 三元组，
  其中 client 是已连接、已登录的 Telethon 客户端），不重建 session 体系。
- 账号冷却：每个账号举报后进入冷却，避免单次任务把同一账号用爆触发风控。
- 三种模式：
  * report_user    单用户举报（ReportPeer + 头像 + spam 三连）
  * report_custom  指定理由举报（自选理由 + 可选文案）
  * report_super   批量多账号举报（拉取目标消息 → AI 生成理由 → 每账号独立文案）

所有耗时操作通过 status_cb 向前端回传进度文本，与 tg-sender 的 _reply 解耦。
"""
import asyncio
import contextlib
import os
import random
import re
import time

from telethon.errors import FloodWaitError
from telethon.tl.functions.account import ReportPeerRequest, ReportProfilePhotoRequest
from telethon.tl.functions.messages import (
    ReportRequest as MsgReportRequest,
    ReportSpamRequest as MsgReportSpamRequest,
)
from telethon.tl.types import InputPhoto

from config import ACTIVE_ACCOUNTS, log, API_ID, API_HASH
from reasons import REPORT_REASONS, REASON_CN, HEAVY_REASONS
from law import get_law_context


# ============================================================
# 目标链接清洗（搬运自 y/bot/helpers.parse_link）
# ============================================================
def _parse_target(link: str) -> str:
    """把 @username / t.me/xxx / 群链接 统一清洗为可 get_entity 的字符串。"""
    link = (link or '').strip()
    if link.startswith('@'):
        return link[1:]
    for prefix in ('https://t.me/', 'http://t.me/', 't.me/'):
        if link.lower().startswith(prefix):
            link = link[len(prefix):]
            break
    link = link.rstrip('/')
    if '/' in link:
        link = link.rsplit('/', 1)[-1]
    return link

# ============================================================
# 账号冷却
# ============================================================
COOLDOWN_SECONDS = 30 * 60  # 举报后休息 30 分钟
_cooldowns = {}  # acc_no -> 时间戳


def set_cooldown(acc_no):
    _cooldowns[acc_no] = time.time()


def cooldown_remaining(acc_no) -> int:
    ts = _cooldowns.get(acc_no)
    if not ts:
        return 0
    return max(0, int(COOLDOWN_SECONDS - (time.time() - ts)))


def available_accounts(n=None):
    """返回未处于冷却的账号列表，按请求数量截断。"""
    accs = [a for a in ACTIVE_ACCOUNTS if cooldown_remaining(a[0]) == 0]
    if n is not None:
        accs = accs[:n]
    return accs


def cooldown_summary() -> str:
    avail = len(available_accounts())
    resting = len(ACTIVE_ACCOUNTS) - avail
    return f'{avail}可用 / {resting}休息中'


# ============================================================
# 进度条工具
# ============================================================
# ============================================================
# 举报防「静默睡死」
# ============================================================
# Telethon 默认 flood_sleep_threshold=60：撞限流不报错而是自己睡最多 60 秒，
# 多个请求叠加起来就是几十分钟无响应。举报链路改成：立刻抛错 + 超阈值换号。
REPORT_FLOOD_MAX = int(os.environ.get("REPORT_FLOOD_MAX", "120"))
REPORT_API_TIMEOUT = int(os.environ.get("REPORT_API_TIMEOUT", "25"))
# 举报正文长度上限（与 AI prompt 的字数要求对齐，不白跑也不截半句）
REPORT_TEXT_MAX = int(os.environ.get("REPORT_TEXT_MAX", "900"))
# 进度刷新频率（个）：1=每个账号都刷，避免界面长时间不动被当成卡死
REPORT_REFRESH_EVERY = max(1, int(os.environ.get("REPORT_REFRESH_EVERY", "1")))


@contextlib.contextmanager
def _no_silent_sleep(client):
    """临时关掉 Telethon 的自动 flood 睡眠（1.44.0 的 __call__ 不转发该参数，
    只能改实例属性）。退出时恢复，不影响群发。"""
    old = getattr(client, "flood_sleep_threshold", 60)
    try:
        client.flood_sleep_threshold = 0
    except Exception:
        pass
    try:
        yield
    finally:
        try:
            client.flood_sleep_threshold = old
        except Exception:
            pass


async def _api(client, request):
    """带硬超时的单次调用；返回 (ok, err)。限流/超时都如实报，不再静默吞。"""
    with _no_silent_sleep(client):
        try:
            await asyncio.wait_for(client(request), timeout=REPORT_API_TIMEOUT)
            return True, ""
        except asyncio.TimeoutError:
            return False, "超时"
        except FloodWaitError as e:
            return False, "限流等%s秒" % e.seconds
        except Exception as e:
            return False, type(e).__name__ + ":" + str(e)[:28]


def _bar(done, total, width=12):
    pct = done * width // total if total else 0
    return '█' * pct + '░' * (width - pct)


def _truncate(text, limit=3800):
    if len(text) <= limit:
        return text
    return text[:limit - 10] + '\n\n...'


# ============================================================
# 内部：对一个 client 执行「举报三连」
# ============================================================
async def _resolve(client, target):
    """带硬超时的实体解析，避免 get_input_entity 无限挂住。"""
    try:
        return await asyncio.wait_for(
            client.get_input_entity(target), timeout=REPORT_API_TIMEOUT)
    except asyncio.TimeoutError:
        raise
    except Exception:
        return await asyncio.wait_for(
            client.get_entity(target), timeout=REPORT_API_TIMEOUT)


async def _do_report_once(client, input_peer, reason_obj, report_text,
                          msg_ids=None):
    """对单个账号执行举报 API 组合。返回 (ok_api, total_api, err)。

    与旧版区别：不再 except: pass 静默吞错，也不再让 Telethon 偷偷睡 60 秒。
    撞长限流（>REPORT_FLOOD_MAX 秒）立刻收手，原因回带给调用方换号。
    """
    api_ok, api_total = 0, 2  # ReportPeer + MsgSpam 至少2个
    errs = []

    ok, err = await _api(client, ReportPeerRequest(
        peer=input_peer, reason=reason_obj, message=report_text or ""))
    if ok:
        api_ok += 1
    else:
        errs.append(err)
        if _give_up_early(err):
            return api_ok, api_total, err

    # 2) MsgReportRequest（带具体消息 ID）
    if msg_ids:
        api_total = 3
        ok, err = await _api(client, MsgReportRequest(
            peer=input_peer, id=msg_ids, option=b'', message=report_text or ""))
        if ok:
            api_ok += 1
        else:
            errs.append(err)
            if _give_up_early(err):
                return api_ok, api_total, err

    # 3) MsgReportSpamRequest
    ok, err = await _api(client, MsgReportSpamRequest(peer=input_peer))
    if ok:
        api_ok += 1
    else:
        errs.append(err)

    return api_ok, api_total, ("; ".join(errs)[:70] or "")


def _give_up_early(err):
    """这个号本轮别再硬试：
      - 限流等 N 秒且 N 超阈值（等下去就是几十分钟）
      - 请求超时（链路/DC 有问题，再发第二个请求只会再白等一个超时）
    """
    if "超时" in (err or ""):
        return True
    m = re.match(r"限流等(\d+)秒", err or "")
    return bool(m) and int(m.group(1)) > REPORT_FLOOD_MAX


# ============================================================
# 模式一：单用户举报（含头像举报）
# ============================================================
async def report_user(username, reason_key='spam', report_text='',
                      num_accounts=None, status_cb=None):
    """举报一个用户/频道。reason_key 为 reasons.REPORT_REASONS 的 key。"""
    username = _parse_target(username)
    reason_obj, reason_name = _resolve_reason(reason_key)
    accs = available_accounts(num_accounts)
    if not accs:
        if status_cb:
            await status_cb('⚠️ 没有可用账号（可能都在冷却中）。')
        return _empty_result()

    results = []
    # 取头像（用于 ReportProfilePhotoRequest）
    photo_id = None
    try:
        c0 = accs[0][1]
        entity = await asyncio.wait_for(c0.get_entity(username), timeout=REPORT_API_TIMEOUT)
        if getattr(entity, 'photo', None):
            photos = await asyncio.wait_for(c0.get_profile_photos(entity, limit=1), timeout=REPORT_API_TIMEOUT)
            if photos:
                p = photos[0]
                photo_id = InputPhoto(id=p.id, access_hash=p.access_hash,
                                      file_reference=p.file_reference)
    except Exception:
        pass

    total = len(accs)
    last_update = 0
    for i, (acc_no, client, _ph) in enumerate(accs):
        try:
            input_peer = await _resolve(client, username)
        except Exception as e:
            results.append((acc_no, f'❌ 解析失败:{str(e)[:24]}'))
            set_cooldown(acc_no)
            continue
        api_ok, api_total, err = await _do_report_once(
            client, input_peer, reason_obj, report_text)
        if photo_id and api_ok < api_total:
            # 尝试额外加头像举报
            try:
                await client(ReportProfilePhotoRequest(
                    peer=input_peer, photo_id=photo_id,
                    reason=reason_obj, message=report_text or ""))
                api_ok += 1
            except Exception:
                pass
        _score(results, acc_no, api_ok, api_total, err)
        set_cooldown(acc_no)
        await _maybe_sleep(i, total, status_cb, results, "用户")

    text = "\n".join(f"  [{acc_no}] → {res}" for acc_no, res in results)
    return _finalize(results, total, target=username, reason_name=reason_name, text=text,
                    strategy='ReportPeer + ProfilePhoto + MsgSpam')


# ============================================================
# 模式二：指定理由举报（channel/user 通用）
# ============================================================
async def report_custom(username, reason_key, num_accounts=None,
                        report_text='', status_cb=None):
    reason_obj, reason_name = _resolve_reason(reason_key)
    username = _parse_target(username)
    accs = available_accounts(num_accounts)
    if not accs:
        if status_cb:
            await status_cb('⚠️ 没有可用账号（可能都在冷却中）。')
        return _empty_result()

    # 拉取目标最近消息 ID，供 MsgReportRequest 使用
    msg_ids = []
    try:
        entity = await asyncio.wait_for(accs[0][1].get_entity(username), timeout=REPORT_API_TIMEOUT)
        msgs = await asyncio.wait_for(accs[0][1].get_messages(entity, limit=50), timeout=REPORT_API_TIMEOUT * 2)
        msg_ids = [m.id for m in msgs if m.id]
    except Exception:
        pass

    results = []
    total = len(accs)
    for i, (acc_no, client, _ph) in enumerate(accs):
        try:
            input_peer = await _resolve(client, username)
        except Exception as e:
            results.append((acc_no, f'❌ 解析失败:{str(e)[:24]}'))
            set_cooldown(acc_no)
            continue
        n_pick = min(random.randint(1, 3), len(msg_ids)) if msg_ids else 0
        picked = random.sample(msg_ids, n_pick) if n_pick else None
        api_ok, api_total, err = await _do_report_once(
            client, input_peer, reason_obj, report_text, msg_ids=picked)
        _score(results, acc_no, api_ok, api_total, err)
        set_cooldown(acc_no)
        await _maybe_sleep(i, total, status_cb, results, "指定理由")

    text = "\n".join(f"  [{acc_no}] → {res}" for acc_no, res in results)
    return _finalize(results, total, target=username, reason_name=reason_name, text=text,
                    strategy='ReportPeer + MsgReport + MsgSpam')


# ============================================================
# 模式三：批量超级举报（多账号 + AI 自动理由）
# ============================================================
async def report_super(username, num_accounts=None, ai_cfg=None, status_cb=None):
    """拉取目标消息 → AI 生成每账号独立理由 → 多账号举报。"""
    username = _parse_target(username)
    accs = available_accounts(num_accounts)
    if not accs:
        if status_cb:
            await status_cb('⚠️ 没有可用账号（可能都在冷却中）。')
        return _empty_result()

    # 拉消息
    if status_cb:
        await status_cb('📥 正在获取目标消息...')
    try:
        client0 = accs[0][1]
        entity = await asyncio.wait_for(client0.get_entity(username), timeout=REPORT_API_TIMEOUT)
        name = getattr(entity, 'title', None) or getattr(entity, 'first_name', '') or username
        messages = await asyncio.wait_for(client0.get_messages(entity, limit=100), timeout=REPORT_API_TIMEOUT * 2)
    except Exception as e:
        if status_cb:
            await status_cb(f'❌ 获取目标失败: {str(e)[:60]}')
        return _empty_result()

    if not messages:
        if status_cb:
            await status_cb('⚠️ 目标没有任何消息。')
        return _empty_result()

    msg_count = len(messages)
    msg_ids = [m.id for m in messages if m.id]
    msgs_combined = '\n'.join(
        f'[MsgID:{m.id}] {m.message}' for m in reversed(messages) if m.message)

    # AI 生成（无 cfg 则兜底）
    ai_results = None
    if ai_cfg:
        try:
            from ai import generate_super_reports
            ai_results = await generate_super_reports(
                ai_cfg, msgs_combined, name, msg_count, len(accs), status_cb=status_cb)
        except Exception as e:
            log.warning(f'[report_super] AI 生成失败，用兜底: {e}')
            ai_results = None
    if not ai_results:
        ai_results = [{'reason': 'child_abuse',
                       'report_text': f'This channel "{name}" distributes harmful illegal content. '
                                      f'Request removal under DSA Article 16.'}
                      for _ in range(len(accs))]
    while len(ai_results) < len(accs):
        ai_results.append(ai_results[-1].copy())

    if status_cb:
        await status_cb(f'🚀 执行举报 ({len(accs)} 账号)...')

    reason_map = {k: v[1] for k, v in REPORT_REASONS.items()}
    results = []
    stats = {}
    total = len(accs)
    last_update = 0
    for i, (acc_no, client, _ph) in enumerate(accs):
        rinfo = ai_results[i]
        rk = rinfo.get('reason', 'child_abuse')
        txt = (rinfo.get('report_text') or '')[:REPORT_TEXT_MAX]
        robj = reason_map.get(rk, reason_map['child_abuse'])
        try:
            input_peer = await _resolve(client, username)
        except Exception as e:
            results.append((acc_no, rk, f'❌ 解析失败:{str(e)[:24]}'))
            set_cooldown(acc_no)
            continue
        n_pick = min(random.randint(1, 3), len(msg_ids)) if msg_ids else 0
        picked = random.sample(msg_ids, n_pick) if n_pick else None
        api_ok, api_total, err = await _do_report_once(client, input_peer, robj, txt, msg_ids=picked)
        tail = f' {err}' if (api_ok < api_total and err) else ''
        if api_ok == api_total:
            res = f'✅ {api_ok}/{api_total}'
        elif api_ok > 0:
            res = f'⚠️ {api_ok}/{api_total}{tail}'
        else:
            res = f'❌ 0/{api_total}{tail}'
        results.append((acc_no, rk, res))
        stats[rk] = stats.get(rk, 0) + 1
        set_cooldown(acc_no)
        await _maybe_sleep(i, total, status_cb, results, "超级", keyed=True)

    stats_text = '\n'.join(f"  {REASON_CN.get(k, k)}: {v} 账号"
                           for k, v in sorted(stats.items(), key=lambda x: -x[1]))
    text = "\n".join(f"  [{acc_no}] [{REASON_CN.get(rk, rk)}] {res}"
                     for acc_no, rk, res in results)
    return _finalize(results, total, target=name, text=text, strategy='ReportPeer+MsgReport+MsgSpam',
                    stats_text=stats_text)


# ============================================================
# 辅助
# ============================================================
def _resolve_reason(key):
    item = REPORT_REASONS.get(key)
    if not item:
        item = REPORT_REASONS['other']
    return item[1], item[0]


def _score(results, acc_no, api_ok, api_total, err=""):
    tail = f' {err}' if (api_ok < api_total and err) else ''
    if api_ok == api_total:
        results.append((acc_no, f'✅ {api_ok}/{api_total}'))
    elif api_ok > 0:
        results.append((acc_no, f'⚠️ {api_ok}/{api_total}{tail}'))
    else:
        results.append((acc_no, f'❌ 0/{api_total}{tail}'))


async def _maybe_sleep(i, total, status_cb, results, label, keyed=False):
    await asyncio.sleep(random.uniform(1.5, 5.0))
    # 每 10 个账号批次间歇，防检测
    if i > 0 and i % 10 == 0:
        await asyncio.sleep(random.uniform(10, 30))
    done = i + 1
    if done % REPORT_REFRESH_EVERY == 0 or done == total:
        live = [f'[{a}] → {r}' for a, r in results[-10:]]
        if keyed:
            live = [f'[{a}] [{REASON_CN.get(k, k)}] → {r}' for a, k, r in results[-10:]]
        if len(results) > 10:
            live.insert(0, f'... ({len(results) - 10} 已完成)')
        if status_cb:
            await status_cb(
                f'🚀 [{_bar(done, total)}] {done}/{total}\n\n' + '\n'.join(live))


def _empty_result():
    return {
        'success': 0, 'partial': 0, 'fail': 0, 'total': 0,
        'summary': cooldown_summary(), 'text': '', 'stats_text': '',
        'strategy': '',
    }


def _finalize(results, total, target='', reason_name='', text='', strategy='', stats_text=''):
    success = sum(1 for r in results if r[-1].startswith('✅'))
    partial = sum(1 for r in results if r[-1].startswith('⚠️'))
    fail = total - success - partial
    return {
        'success': success, 'partial': partial, 'fail': fail, 'total': total,
        'target': target, 'reason_name': reason_name,
        'summary': cooldown_summary(), 'text': _truncate(text), 'strategy': strategy,
        'stats_text': stats_text,
    }
