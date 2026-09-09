# -*- coding: utf-8 -*-
"""Regression for reporter._maybe_sleep unpack crash (live crash 00:42:11).

Super mode stores 3-tuples (acc_no, reason_key, res); user/custom store
2-tuples. The old code unconditionally unpacked 2 -> ValueError every time
super mode reported progress, killing the whole loop at account #1.
"""
import asyncio
import io
import os
import sys

os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "abc")
os.environ.setdefault("BOT_TOKEN", "***")
os.environ.setdefault("OWNER_ID", "111")
os.environ.setdefault("DATABASE_URL", "postgresql://u:***@127.0.0.1:5432/none")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import reporter  # noqa: E402

PASS = []
FAIL = []


def chk(name, cond):
    (PASS if cond else FAIL).append(name)
    print(('  ok  ' if cond else '  FAIL') + ' ' + name)


async def main():
    sent = []

    async def cb(text):
        sent.append(text)

    # --- super mode: 3-tuples, keyed=True (the crash path) ---
    keyed_results = [(f'A{i}', 'child_abuse', '✅ 3/3') for i in range(7)]
    try:
        reporter.REPORT_REFRESH_EVERY = 1
        await reporter._maybe_sleep(0, 7, cb, keyed_results, '超级', keyed=True)
        chk('keyed 3-tuple does not raise', True)
    except Exception as e:
        chk('keyed 3-tuple does not raise: %s:%s' % (type(e).__name__, str(e)[:60]), False)
    chk('keyed progress emitted', len(sent) == 1)
    if sent:
        chk('keyed shows reason label', '儿童色情' in sent[0] or '儿童' in sent[0])
        chk('keyed shows bar+count', '1/7' in sent[0])
        chk('keyed lists accounts', '[A0]' in sent[0])

    # --- user/custom mode: 2-tuples, keyed=False ---
    sent.clear()
    plain_results = [(f'B{i}', '✅ 2/2') for i in range(3)]
    try:
        await reporter._maybe_sleep(2, 3, cb, plain_results, '用户')
        chk('plain 2-tuple does not raise', True)
    except Exception as e:
        chk('plain 2-tuple does not raise: %s:%s' % (type(e).__name__, str(e)[:60]), False)
    chk('plain progress emitted', len(sent) == 1)
    if sent:
        chk('plain shows 3/3', '3/3' in sent[0])
        chk('plain lists accounts', '[B2]' in sent[0])
        chk('plain has no reason column', '儿童' not in sent[0])

    # --- >10 results: overflow header + window of last 10 ---
    sent.clear()
    big = [(f'C{i}', 'child_abuse', '✅ 3/3') for i in range(15)]
    await reporter._maybe_sleep(14, 15, cb, big, '超级', keyed=True)
    chk('big keyed does not raise', len(sent) == 1)
    if sent:
        chk('big shows overflow hint', '(5 已完成)' in sent[0])
        chk('big shows 15/15', '15/15' in sent[0])

    # --- refresh throttle still respected ---
    sent.clear()
    reporter.REPORT_REFRESH_EVERY = 5
    await reporter._maybe_sleep(1, 7, cb, keyed_results[:2], '超级', keyed=True)
    chk('throttle skips non-multiple', len(sent) == 0)
    reporter.REPORT_REFRESH_EVERY = 1

    # --- source-level guard: no unconditional 2-tuple unpack left ---
    src = open('reporter.py', encoding='utf-8').read()
    lines = src.split('\n')
    bad = [n + 1 for n, l in enumerate(lines)
           if 'for a, r in results' in l and 'else' not in lines[max(0, n - 1)]]
    chk('no stray unconditional unpack (lines %s)' % bad, len(bad) == 0)
    chk('CR free', '\r' not in src)

    print('\nRESULT pass=%d fail=%d' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED: ' + '; '.join(FAIL))
        sys.exit(1)
    print('ALL PASS')


asyncio.run(main())
