#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入群验证「人在环」中继（B 方案）：系统只搬题、只跑腿，答题的是人。

为什么不等于 verify.py：
  verify.py 自己读题、自己算 1+2、自己找带 verify 字样的按钮点掉——决定是程序做的。
  本模块一个都不自己决定：
    · 不猜答案（没有 solve_math，没有关键字挑按钮）
    · 不主动发消息（不替账号 /start 验证 bot）
    · 每次提交都必须有一次「老板在控制 Bot 里点/打字」的动作，一次动作只提交一次
  所以流程是：把验证 bot 的原话 + 它的按钮原样列给老板 → 老板点第几个 / 打什么字
             → 系统把「老板选的那一个」提交一次。

边界（写死的护栏，别改宽）：
  1. 只在「该账号刚撞到入群验证并被 arm」的时间窗内监听，不做全局监听。
  2. 每个待办条目一次性消费（taken 后立刻失效），防止重复点击 = 给验证 bot 刷屏。
  3. 需要密码(requires_password)的按钮一律拒绝，绝不代填。
  4. 密码/URL 类按钮只给老板链接让老板自己开，程序不打开。
  5. 条目 15 分钟过期，过期即丢；每个 arm 窗最多转 6 题，防刷。
"""
import time

from telethon import events

from config import log, get_notify
from bot_menu import verify_relay_kb

VERIFY_TTL = 15 * 60      # 一道题从转出去到作废
ARM_TTL = 30 * 60         # 撞验证后，盯多久的消息
MAX_PUSH_PER_ARM = 6      # 每个 arm 窗最多转几道题
PUSH_COOLDOWN = 45        # 同一个会话两条之间至少隔这么久

# 只用来判断「这条像不像验证题」，仅用于决定要不要搊给老板看，绝不用于猜答案。
# 带按钮 / 不带按钮 分开：前者很宽（验证题大多就是几个字的按钮），后者必须更明确，
# 否则会把该号在其他地方的普通聊天捣给老板。
_BTN_HINTS = ("verif", "human", "captcha", "confirm", "prove", "are you",
              "click", "press", "solve", "answer", "start", "join", "rules",
              "验证", "确认", "人机", "机器人", "加入", "规则", "题", "答")
_TEXT_HINTS = ("verif", "captcha", "human", "prove you", "are you a",
               "click the button", "answer", "solve", "access", "to join",
               "验证", "人机", "机器人", "答案", "回答", "算一下", "等于几",
               "点下面", "点击下方", "输入", "答题")

_b = {"bot": None}                 # 控制 bot（发消息给老板用）
_armed = {}                        # acc_tag -> {link,title,at,pushes,last_push,group_id}
_items = {}                        # item_id -> 待办（题目+可选项），一次性消费
_seq = {"n": 0}
_hooked = set()                    # 已挂监听的 client id


def set_bot(bot):
    _b["bot"] = bot


def _now():
    return int(time.time())


def _new_id():
    _seq["n"] += 1
    return f"{_now():x}{_seq['n']:02x}"


# ---------------------------------------------------------------- 监听装配
def attach(client, acc_tag):
    """给某个账号 client 挂消息监听（同一个 client 只挂一次）。"""
    key = id(client)
    if key in _hooked:
        return
    _hooked.add(key)

    async def _on_new(ev):
        await _capture(client, acc_tag, ev)

    try:
        client.add_event_handler(_on_new, events.NewMessage(incoming=True))
        client.add_event_handler(_on_new, events.MessageEdited(incoming=True))
    except Exception as e:
        log.info(f"[验证中继] 挂监听失败(忽略) {acc_tag}: {type(e).__name__}")


def arm(client, acc_tag, link, title="", group_id=None):
    """撞入群验证后调用：接下来 ARM_TTL 秒内，这个账号收到的验证题转给老板。"""
    _armed[acc_tag] = {"link": link or "", "title": title or "", "at": _now(),
                       "pushes": 0, "last_push": 0, "group_id": group_id}
    attach(client, acc_tag)
    log.info(f"[验证中继] 开始盯 {acc_tag} 的验证消息（群「{title or '?'}」）")


def disarm(acc_tag):
    _armed.pop(acc_tag, None)


def armed_summary():
    """给「在途申请」面板用的文字。"""
    now = _now()
    rows = [(t, v) for t, v in _armed.items() if now - v.get("at", 0) < ARM_TTL]
    if not rows:
        return ""
    rows.sort(key=lambda kv: kv[1].get("at") or 0)
    lines = ["📡 正在盯验证消息的账号："]
    for t, v in rows:
        mins = (now - int(v.get("at") or 0)) // 60
        lines.append(f"  · 账号{t} ← 「{v.get('title') or '?'}」（已盯 {mins} 分钟，"
                     f"转了 {v.get('pushes', 0)} 题）")
    lines.append("💡 题目一出现就自动送到这个会话，你点按钮或打答案，系统只提交你选的那一次。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 抓题
def _looks_like_verification(text, has_buttons):
    low = (text or "").lower()
    return any(h in low for h in (_BTN_HINTS if has_buttons else _TEXT_HINTS))


async def _capture(client, acc_tag, ev):
    """把疑似验证题原样转给老板；不做任何自动应答。"""
    try:
        slot = _armed.get(acc_tag)
        if not slot or _now() - slot.get("at", 0) >= ARM_TTL:
            return
        msg = getattr(ev, "message", None)
        if msg is None or getattr(msg, "out", False):
            return  # 只看进来的消息，自己发的绝不回环
        if getattr(msg, "action", None) is not None:
            return
        text = (getattr(msg, "text", None) or "").strip()
        buttons = getattr(msg, "buttons", None)
        flat = _flatten(buttons)
        if not text and not flat:
            return
        # 只看私聊：入群验证都是 Telegram 把用户引到验证 bot 的私聊里出题。
        # 不限的话，这个号在其他群/频道的普通消息会被当验证捣给老板。
        if not getattr(msg, "is_private", False):
            return
        if not _looks_like_verification(text, bool(flat)):
            return
        if _now() - slot.get("last_push", 0) < PUSH_COOLDOWN:
            return
        if slot.get("pushes", 0) >= MAX_PUSH_PER_ARM:
            log.info(f"[验证中继] {acc_tag} 超过 {MAX_PUSH_PER_ARM} 题上限，本窗不再转")
            return
        slot["pushes"] = slot.get("pushes", 0) + 1
        slot["last_push"] = _now()
        iid = _new_id()
        _items[iid] = {
            "at": _now(),
            "acc": acc_tag,
            "client": client,
            "chat_id": msg.chat_id,
            "msg_id": msg.id,
            "text": text[:900],
            "opts": flat,
            "link": slot.get("link") or "",
            "title": slot.get("title") or "",
            "taken": False,
        }
        await _push(iid)
    except Exception as e:
        log.info(f"[验证中继] 抓题异常(忽略): {type(e).__name__}: {e}")


def _flatten(buttons):
    """把 Telethon 的 buttons 二维结构摊平，记 (行,列)。只留文字，不改写。"""
    out = []
    if not buttons:
        return out
    for i, row in enumerate(buttons):
        for j, b in enumerate(row or []):
            txt = (getattr(b, "text", None) or "").strip()
            if not txt:
                continue
            kind = "callback"
            try:
                if getattr(b, "url", None):
                    kind = "url"
                elif getattr(b, "data", None) is not None:
                    kind = "callback"
            except Exception:
                pass
            req = False
            try:
                req = bool(getattr(b, "requires_password", False))
            except Exception:
                req = False
            out.append({"i": i, "j": j, "text": txt[:40], "kind": kind, "pwd": req})
    return out


async def _push(iid):
    it = _items.get(iid)
    bot = _b.get("bot")
    if not it or bot is None:
        return
    uid = get_notify()
    try:
        await bot.send_message(uid, _render(iid, it), buttons=verify_relay_kb(iid, it))
        log.info(f"[验证中继] 已把 1 道验证题转给 uid={uid}（账号{it['acc']}）")
    except Exception as e:
        log.info(f"[验证中继] 转发失败: {type(e).__name__}: {e}")


def _render(iid, it):
    lines = [
        "🔒 入群验证 · 等人来答（系统不代答）",
        "",
        f"群：「{it['title'] or '?'}」  账号：{it['acc']}",
    ]
    if it.get("link"):
        lines.append(f"群链接：{it['link']}")
    lines += ["", "— 验证机器人原话 —", it["text"] or "(无文字，只有按钮)", ""]
    opts = it["opts"]
    if opts:
        lines.append(f"它给的按钮（共 {len(opts)} 个，你点哪个我就只提交哪个）：")
        for n, o in enumerate(opts, 1):
            mark = ""
            if o["kind"] == "url":
                mark = " 🔗外部链接"
            if o["pwd"]:
                mark += " ⛔需密码(不代填)"
            lines.append(f"  {n}. {o['text']}{mark}")
    else:
        lines.append("这条没有按钮 → 大概是「打字回答」型：直接回我 `ans <编号> 你的答案`。")
    lines += ["", "⏱ 15 分钟内有效；你提交一次，系统就只发一次，不会重复。"]
    lines.append(f"（编号 {iid}）")
    return "\n".join(lines)


def open_items():
    now = _now()
    dead = [k for k, v in _items.items() if v.get("taken") or now - v.get("at", 0) >= VERIFY_TTL]
    for k in dead:
        _items.pop(k, None)
    return [(k, v) for k, v in sorted(_items.items(), key=lambda kv: kv[1].get("at") or 0)]


# ---------------------------------------------------------------- 人工动作
async def click_option(iid, no, actor_uid):
    """老板点了第 no 个按钮 → 只提交这一个。"""
    it = _items.get(iid)
    if not it:
        return "⚠️ 这道题已经过期或已提交过了。要重试请回「📨 在途申请」再点一次。"
    if it.get("taken"):
        return "⚠️ 这条已经提交过一次了（防重复刷屏）。没通过就等它出下一题，或去官方客户端处理。"
    idx = _now()
    if idx - it.get("at", 0) >= VERIFY_TTL:
        _items.pop(iid, None)
        return "⚠️ 超过 15 分钟，作废了。重新加一次群会重新盯。"
    try:
        no = int(no)
    except (TypeError, ValueError):
        return "⚠️ 按钮编号无效。"
    if no < 1 or no > len(it["opts"]):
        return f"⚠️ 编号超出范围（1~{len(it['opts'])}）。"
    opt = it["opts"][no - 1]
    if opt.get("pwd"):
        return "⛔ 这个按钮要密码，系统不代填。请在官方客户端自己点。"
    if opt.get("kind") == "url":
        return "🔗 这是外部链接，我不代开。请用上面「打开群/链接」按钮自己点开看。"
    it["taken"] = True
    _items.pop(iid, None)
    res = await _do_click(it, opt)
    _audit(actor_uid, "verify_click",
           f"acc={it['acc']} 「{it['title']}」 第{no}个={opt['text'][:30]} -> {res[:60]}")
    return res


async def _do_click(it, opt):
    client = it["client"]
    try:
        msg = await client.get_messages(it["chat_id"], ids=it["msg_id"])
    except Exception as e:
        return f"❌ 取不到那条验证消息（{type(e).__name__}），可能已被验证 bot 删除。"
    if msg is None:
        return "❌ 那条消息已经没了（验证 bot 撤回了）。重新加一次群会重新盯。"
    fresh = _flatten(getattr(msg, "buttons", None))
    hit = next((o for o in fresh if o["i"] == opt["i"] and o["j"] == opt["j"]), None)
    if hit is None:
        return "⚠️ 消息里的按钮变了（验证 bot 改了题目）。下一条我会重新转给你。"
    if hit["text"] != opt["text"]:
        return "⚠️ 你点的那个位置的文字已经换了（防误点，这次没提交）。请看新转发的题目重新点。"
    try:
        await msg.click(opt["i"], opt["j"])
    except Exception as e:
        return f"❌ 提交失败：{type(e).__name__}: {str(e)[:80]}"
    return (f"✅ 已按你选的提交一次：{opt['text']}\n"
            "   通过了的话，再点「加群」发同一链接就会自动入表+拉名单；\n"
            "   它若再出题，我会继续转给你。")


async def submit_answer(iid, answer, actor_uid):
    """老板打的字，原样发一次到那条题所在的会话。"""
    it = _items.get(iid)
    if not it:
        return "⚠️ 没有这道题（已过期/已提交）。格式：ans <编号> 你的答案"
    if it.get("taken"):
        return "⚠️ 这条已提交过一次，不重复发。"
    if _now() - it.get("at", 0) >= VERIFY_TTL:
        _items.pop(iid, None)
        return "⚠️ 超过 15 分钟，作废了。"
    txt = (answer or "").strip()
    if not txt:
        return "⚠️ 答案是空的。"
    if len(txt) > 120:
        return "⚠️ 太长了（>120 字），确认一下是不是打错地方。"
    it["taken"] = True
    _items.pop(iid, None)
    client = it["client"]
    try:
        await client.send_message(it["chat_id"], txt)
    except Exception as e:
        return f"❌ 发送失败：{type(e).__name__}: {str(e)[:80]}"
    _audit(actor_uid, "verify_answer",
           f"acc={it['acc']} 「{it['title']}」 答={txt[:30]}")
    return (f"✅ 已原样发一次给该会话（账号{it['acc']}）：{txt}\n"
            "   通过了就回「加群」发同一链接，自动入表+拉名单；要再答下一题我继续盯。")


def parse_answer(text):
    """解析 `ans <编号> 答案`；不是这个格式返回 None。"""
    t = (text or "").strip()
    if not t.lower().startswith("ans"):
        return None
    rest = t[3:].strip()
    parts = rest.split(None, 1)
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def drop(iid, actor_uid=None):
    it = _items.pop(iid, None)
    if not it:
        return "⚠️ 这道题已经不在了。"
    _audit(actor_uid, "verify_drop", f"acc={it['acc']} 「{it['title']}」")
    return "✖ 已忽略这道题。要重开就回「📨 在途申请」点「继续盯」。"


def forget_arm(acc_tag, actor_uid=None):
    had = _armed.pop(acc_tag, None)
    for k in [k for k, v in _items.items() if v.get("acc") == acc_tag]:
        _items.pop(k, None)
    return "✅ 已停止盯该账号的验证消息。" if had else "ℹ️ 该账号本来就没在盯。"


def _audit(uid, action, detail):
    try:
        from db import db_log_op
        from config import actor_name
        db_log_op(uid, actor_name(uid) if uid else "", action, detail)
    except Exception:
        pass
