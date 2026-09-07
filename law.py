#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EU 法条库（完整搬运自 y 项目 law.json）。

原本提供：
- get_law_context(reason_key): 给 AI 生成举报文案时引用，返回该违规类型相关的法条原文。
- LAW_DATA: 完整结构化法条（dsa / child_protection / gdpr / terrorist_content /
  fraud / harassment_violence / drugs 共 7 大节、几十个法条），与 y 完全对齐。
- flatten_laws(): 返回 [(section, key, article, text)] 平面列表，便于调试/展示。

所有内容从同目录 law.json 加载；加载失败时用内置最小兜底，保证模块可用。
"""
import json
import os

_BASE = os.path.dirname(os.path.abspath(__file__))
_LAW_PATH = os.path.join(_BASE, "law.json")

# 违规类型 -> 关联法条节（用于 get_law_context 取相关法条）
_REASON_TO_SECTIONS = {
    'child_abuse':       ['child_protection', 'dsa'],
    'illegal_drugs':     ['drugs', 'dsa'],
    'violence':          ['harassment_violence', 'dsa'],
    'pornography':       ['child_protection', 'dsa'],
    'personal_details':  ['gdpr', 'dsa'],
    'fake':              ['fraud', 'dsa'],
    'spam':              ['dsa'],
    'copyright':         ['dsa'],
    'geo_irrelevant':    ['dsa'],
    'other':             ['dsa'],
}


def _load():
    try:
        with open(_LAW_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


LAW_DATA = _load()


def _section_articles(section: str) -> list:
    """返回某节下所有 (key, article, text) 列表。"""
    out = []
    v = LAW_DATA.get(section)
    if not isinstance(v, dict):
        return out
    arts = v.get("articles", {})
    if not isinstance(arts, dict):
        return out
    for group, grp in arts.items():
        if isinstance(grp, str):
            out.append((group, group, grp))
        elif isinstance(grp, dict):
            for a, desc in grp.items():
                if isinstance(desc, str):
                    out.append((group, a, desc))
    return out


def flatten_laws():
    """平面化所有法条，便于展示/校验。返回 [(section, key, article, text)]。"""
    rows = []
    for sec in LAW_DATA:
        if sec.startswith("_"):
            continue
        for key, art, text in _section_articles(sec):
            rows.append((sec, key, art, text))
    return rows


def get_law_context(reason_key: str) -> str:
    """返回某违规类型对应的 EU 法条引用文本（多节拼接）。"""
    sections = _REASON_TO_SECTIONS.get(reason_key, ['dsa'])
    parts = []
    for sec in sections:
        for _group, _art, text in _section_articles(sec):
            parts.append(f"{text}")
    if not parts:
        return "DSA Regulation (EU) 2022/2065 Article 16: platforms must process notices of illegal content diligently and objectively."
    # 取前若干条，避免 prompt 过长
    joined = "\n".join(f"- {p}" for p in parts[:12])
    return joined
