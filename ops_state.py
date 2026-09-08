#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运营状态持久化：记录「群发运营」流程做到哪一步、当前选中的群、待发文案。

qfbot 桌面板是本地状态；TG 端是服务端无状态，所以必须把进行中的操作存到 DB，
这样随时回来点开都能接着上次的进度，不用重新走。

多人共管：campaign 按发起人 uid 分键存放（campaign_by_actor），
主人和每个操作员各有自己一份「选群/文案」草稿，互不覆盖。
旧数据（单一 campaign）会自动归到 owner 名下，不丢进度。
"""
import json
import time

from db import DB


# ---- 键名 ----
K_CAMPAIGN = "campaign"          # 旧版单一任务（仅作迁移来源）
K_CAMPAIGN_BY_ACTOR = "campaign_by_actor"  # 新版：{str(uid): campaign}
K_CURRENT_GROUP = "current_group"
K_SETTINGS = "settings"          # 运行时设置（全局共享，仅 owner 可改）
K_LIST_CLAIM = "list_claim"      # 名单占用：{uid, name, group, at}（targets 表全局共用，必须串行）

LIST_CLAIM_TTL = 1800            # 名单占用租期（秒），超时自动可被他人接管

DEFAULTS = {
    K_CAMPAIGN: None,
    K_CAMPAIGN_BY_ACTOR: {},
    K_CURRENT_GROUP: None,
    K_LIST_CLAIM: None,
}


def _actor_key(uid) -> str:
    try:
        return str(int(uid))
    except (TypeError, ValueError):
        return "0"


def _load() -> dict:
    raw = DB.load_session("ops_state")
    if not raw:
        return dict(DEFAULTS)
    try:
        data = json.loads(raw)
    except Exception:
        return dict(DEFAULTS)
    for k, v in DEFAULTS.items():
        data.setdefault(k, v)
    return data


def _save(data: dict):
    try:
        DB.save_session("ops_state", json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def get(key: str):
    return _load().get(key)


def set(key: str, value):
    data = _load()
    data[key] = value
    _save(data)


def clear(key: str):
    set(key, None)


# ---- 群发任务（campaign）便捷方法：按发起人隔离 ----
def _all_campaigns(data: dict) -> dict:
    """取出按人分键的任务表；顺手把旧版单一 campaign 迁移到 owner 名下。"""
    table = data.get(K_CAMPAIGN_BY_ACTOR)
    if not isinstance(table, dict):
        table = {}
    legacy = data.get(K_CAMPAIGN)
    if isinstance(legacy, dict) and legacy:
        from config import OWNER_ID
        key = _actor_key(OWNER_ID)
        if key not in table:
            table[key] = legacy
        data[K_CAMPAIGN] = None
        data[K_CAMPAIGN_BY_ACTOR] = table
        _save(data)
    return table


def get_campaign(uid=None) -> dict:
    data = _load()
    table = _all_campaigns(data)
    c = table.get(_actor_key(uid))
    return c if isinstance(c, dict) else {}


def set_campaign(uid=None, **kwargs):
    data = _load()
    table = _all_campaigns(data)
    key = _actor_key(uid)
    c = table.get(key) or {}
    c.update(kwargs)
    c["owner_uid"] = int(uid) if uid else 0
    c["updated_at"] = int(time.time())
    table[key] = c
    data[K_CAMPAIGN_BY_ACTOR] = table
    data[K_CAMPAIGN] = None
    _save(data)


def clear_campaign(uid=None):
    data = _load()
    table = _all_campaigns(data)
    table.pop(_actor_key(uid), None)
    data[K_CAMPAIGN_BY_ACTOR] = table
    data[K_CAMPAIGN] = None
    _save(data)


def campaign_busy_by() -> list:
    """列出所有「已有草稿/正在跑」的任务，用于告诉后来的人谁在占用。"""
    table = _all_campaigns(_load())
    out = []
    for uid, c in table.items():
        if not isinstance(c, dict) or not c:
            continue
        out.append((uid, c))
    return out


# ---- 名单占用（targets 表是全库共用的，多人同时选群会互相覆盖）----
def get_list_claim() -> dict:
    c = get(K_LIST_CLAIM)
    if not isinstance(c, dict) or not c:
        return {}
    try:
        if int(time.time()) - int(c.get("at") or 0) > LIST_CLAIM_TTL:
            return {}
    except (TypeError, ValueError):
        return {}
    return c


def claim_list(uid, name="", group="") -> tuple:
    """尝试占用名单。返回 (True, None) 或 (False, 占用者 dict)。"""
    cur = get_list_claim()
    try:
        uid_i = int(uid)
    except (TypeError, ValueError):
        uid_i = 0
    if cur and int(cur.get("uid") or 0) != uid_i:
        return False, cur
    set(K_LIST_CLAIM, {"uid": uid_i, "name": name or "", "group": str(group or ""),
                       "at": int(time.time())})
    return True, None


def touch_list(uid):
    """续期（自己还在用）。"""
    cur = get_list_claim()
    if cur and str(cur.get("uid")) == str(uid):
        cur["at"] = int(time.time())
        set(K_LIST_CLAIM, cur)


def release_list(uid=None):
    """释放名单；uid 为 None 时无条件释放（主人可用「停止任务」解锁）。"""
    cur = get_list_claim()
    if uid is None or not cur or str(cur.get("uid")) == str(uid):
        set(K_LIST_CLAIM, None)
        return True
    return False


def campaign_text(uid=None) -> str:
    """生成群发运营的当前进度文本（新版 3 步：选群→文案→开跑）。"""
    c = get_campaign(uid)
    if not c:
        return "尚未开始。点「① 选群」选群（自动拉成员）→ 发文案 → 「③ 确认开跑」。"
    lines = ["【群发运营】当前进度："]
    checks = {
        "group": bool(c.get("group")),
        "target": bool(c.get("target_count")),
        "text": bool(c.get("text")),
        "accounts": bool(c.get("accounts_ready")),
    }
    steps = [
        ("① 选群（自动拉成员）", "group"),
        ("② 写文案", "text"),
        ("③ 确认开跑", None),
    ]
    done = 0
    for label, key in steps:
        if key is None:
            lines.append("  ⬜ " + label)
            continue
        ok = checks.get(key, False)
        if ok:
            done += 1
        lines.append(f"  {'✅' if ok else '⬜'} {label}")
    lines.append(f"  当前: 群={c.get('group_title') or c.get('group')} | "
                 f"名单={c.get('target_count', 0)}人 | "
                 f"文案={'已填' if c.get('text') else '未填'}")
    if checks.get("group") and checks.get("text"):
        lines.append("→ 就绪，点「③ ✅ 确认开跑」开始群发")
    else:
        lines.append(f"→ 已完成 {done}/2 步")
    return "\n".join(lines)
