#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""举报理由表（10 种 Telegram 官方举报理由，搬运自 y 项目 bot/reasons.py，含原始 emoji 文案）。"""
from telethon.tl.types import (
    InputReportReasonSpam,
    InputReportReasonViolence,
    InputReportReasonPornography,
    InputReportReasonChildAbuse,
    InputReportReasonOther,
    InputReportReasonCopyright,
    InputReportReasonGeoIrrelevant,
    InputReportReasonFake,
    InputReportReasonIllegalDrugs,
    InputReportReasonPersonalDetails,
)

# (按钮文案, 理由对象) — 文案与 y 完全一致
REPORT_REASONS = {
    'spam':              ('🚫 Spam 垃圾信息',                InputReportReasonSpam()),
    'violence':          ('💀 Violence 暴力威胁',             InputReportReasonViolence()),
    'pornography':       ('🔞 Pornography 色情内容',          InputReportReasonPornography()),
    'child_abuse':       ('👶 Child Abuse 儿童虐待',          InputReportReasonChildAbuse()),
    'copyright':         ('©️ Copyright 版权侵犯',             InputReportReasonCopyright()),
    'fake':              ('🎭 Fake 虚假诈骗',                 InputReportReasonFake()),
    'illegal_drugs':     ('💊 Illegal Drugs 非法毒品',        InputReportReasonIllegalDrugs()),
    'personal_details':  ('🔓 Personal Details 个人信息泄露',  InputReportReasonPersonalDetails()),
    'geo_irrelevant':    ('🌍 Geo Irrelevant 地理无关',       InputReportReasonGeoIrrelevant()),
    'other':             ('📋 Other 其他违规',                InputReportReasonOther()),
}

# 中文名（AI 文案/结果展示用）
REASON_CN = {
    'child_abuse': '儿童虐待', 'illegal_drugs': '非法毒品',
    'violence': '暴力', 'pornography': '色情',
    'spam': '垃圾信息', 'fake': '虚假/诈骗',
    'copyright': '版权侵犯', 'personal_details': '个人信息泄露',
    'geo_irrelevant': '地理位置不相关', 'other': '其他',
}

# 严重违规类型（AI 分析时锁定在这几类，绝不选 other）
HEAVY_REASONS = {
    'child_abuse', 'illegal_drugs', 'violence',
    'pornography', 'personal_details', 'fake',
}
