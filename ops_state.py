#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运营状态持久化：记录「群发运营」流程做到哪一步、当前选中的群、待发文案。

qfbot 桌面板是本地状态；TG 端是服务端无状态，所以必须把进行中的操作存到 DB，
这样老板随时回来点开都能接着上次的进度，不用重新走。
"""
import json
import time

from db import DB


# ---- 键名 ----
K_CAMPAIGN = "campaign"      # 群发任务：{group, group_title, target_count, text, started, status}
K_CURRENT_GROUP = "current_group"  # 当前选中的群 id
K_SETTINGS = "settings"      # 运行时设置（间隔/上限/并行数）也可走 state，但落库更稳

DEFAULTS = {
    K_CAMPAIGN: None,
    K_CURRENT_GROUP: None,
}


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


# ---- 群发任务（campaign）便捷方法 ----
def get_campaign() -> dict:
    c = get(K_CAMPAIGN)
    if not c:
        return {}
    return c


def set_campaign(**kwargs):
    c = get_campaign()
    c.update(kwargs)
    c["updated_at"] = int(time.time())
    set(K_CAMPAIGN, c)


def clear_campaign():
    set(K_CAMPAIGN, None)


def campaign_text() -> str:
    """生成群发运营向导的当前进度文本"""
    c = get_campaign()
    if not c:
        return "尚未开始群发运营，点「🚀 开始群发」从第①步走起。"
    lines = ["【群发运营】当前进度："]
    checks = {
        "group": bool(c.get("group")),
        "target": bool(c.get("target_count")),
        "text": bool(c.get("text")),
        "accounts": bool(c.get("accounts_ready")),
    }
    steps = [
        ("① 选择群", "group"),
        ("② 拉取名单", "target"),
        ("③ 写文案", "text"),
        ("④ 账号就绪", "accounts"),
    ]
    done = 0
    for label, key in steps:
        ok = checks.get(key, False)
        if ok:
            done += 1
        lines.append(f"  {'✅' if ok else '⬜'} {label}")
    lines.append(f"  当前: 群={c.get('group_title') or c.get('group')} | "
                 f"名单={c.get('target_count', 0)}人 | "
                 f"文案={'已填' if c.get('text') else '未填'}")
    lines.append(f"→ 已完成 {done}/4 步")
    return "\n".join(lines)
