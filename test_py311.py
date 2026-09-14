# -*- coding: utf-8 -*-
"""防回归：全仓必须能被线上 Python 3.11 编译。

09-15 线上崩了半个钟头：压缩菜单文案时写了 f"{'\\u23f3 任务中' if busy else ...}"，
f-string 表达式里带反斜杠是 3.12+ 才允许的语法，线上 3.11 直接 SyntaxError，
容器起来就崩、反复重启。本地 venv 是 3.14，py_compile 全绿也照样上线炸。

两道检查：
  T1 静态扫（不依赖本机解释器版本）：f-string 表达式里不许反斜杠、不许嵌套同款引号
  T3 真实 Python 3.11 编译全仓（本机有 uv 就顺手起一个 3.11，权威判定）
"""
import ast
import glob
import io
import os
import shutil
import subprocess
import sys
import tempfile

D = os.path.dirname(os.path.abspath(__file__))
os.chdir(D)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FAILS = []


def ck(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  << " + str(extra)[:200]))
    if not cond:
        FAILS.append(name)


def fstring_problems(path, src):
    """返回 [(行号, 说明)]：f-string 表达式里的 3.11 非法写法。"""
    out = []
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        return [(e.lineno or 0, "SyntaxError: %s" % e.msg)]
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):        # 一个 f"..." 片段
            continue
        seg = ast.get_source_segment(src, node) or ""
        head = seg[:3].lower()
        q = "'" if ("'" in head) else '"'
        for sub in ast.walk(node):
            if not isinstance(sub, ast.FormattedValue):   # {...} 里的表达式
                continue
            body = (ast.get_source_segment(src, sub) or "")[1:-1]
            if "\\" in body:
                out.append((sub.lineno, "表达式含反斜杠: %s" % body.strip()[:60]))
            for c in ast.walk(sub):
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    cf = ast.get_source_segment(src, c) or ""
                    if cf[:1] == q:
                        out.append((c.lineno, "嵌套同款引号 %s: %s" % (q, cf[:50])))
    return out


files = sorted(glob.glob("*.py"))
ck("T0 扫到了文件", len(files) >= 25, len(files))

bad_fs, bad_bom = [], []
for f in files:
    # utf-8-sig：文件头的 BOM 由解码器吃掉（Python 编译源文件时也是这样处理的），
    # 否则 ast.parse 会假报 "invalid non-printable character U+FEFF"
    raw = io.open(f, encoding="utf-8-sig", errors="strict").read()
    for ln, why in fstring_problems(f, raw):
        bad_fs.append("%s:%d %s" % (f, ln, why))
    if "\ufeff" in raw[1:]:
        bad_bom.append("%s@%d" % (f, raw[1:].index("\ufeff") + 2))

ck("T1 没有 f-string 表达式含反斜杠/同引号（线上 3.11 会 SyntaxError）",
   not bad_fs, bad_fs[:6])
ck("T2 没有半路 BOM", not bad_bom, bad_bom[:6])

# ---- T3 真 3.11 编译（写个临时脚本跑，避开 -c 的引号地狱） ----
if shutil.which("uv"):
    script = os.path.join(tempfile.gettempdir(), "py311_check.py")
    io.open(script, "w", encoding="utf-8").write(
        "import py_compile,glob,os,tempfile\n"
        "out=tempfile.mkdtemp()\nbad=[]\n"
        "for f in sorted(glob.glob('*.py')):\n"
        "    try: py_compile.compile(f, doraise=True, cfile=os.path.join(out,f[:-3]+'.pyc'))\n"
        "    except Exception as e: bad.append(f+': '+str(e)[-90:])\n"
        "print('BADN=%d'%len(bad))\n"
        "[print('  '+b) for b in bad[:8]]\n")
    p = subprocess.run(["uv", "run", "--python", "3.11", "--no-project", "python", script],
                       cwd=D, capture_output=True, text=True, timeout=420,
                       shell=os.name == "nt")
    txt = ((p.stdout or "") + (p.stderr or "")).strip()
    hit = [l for l in txt.splitlines() if "BADN=" in l]
    ck("T3 真 Python 3.11 全仓编译通过", bool(hit) and hit[0].strip() == "BADN=0",
       (hit[0] if hit else txt[-260:]).strip())
else:
    print("SKIP T3（本机没有 uv，起不了 3.11）")

# ---- T4~T6 文案没被改坏 ----
sys.path.insert(0, D)
os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "abc")
os.environ.setdefault("BOT_TOKEN", "***")
os.environ.setdefault("OWNER_ID", "111")
os.environ.setdefault("DATABASE_URL", "postgresql://u:***@127.0.0.1:5432/none")
import bot_menu as M  # noqa: E402

owner = M.main_menu_text([1, 2], 9, 30, 4, False, role="owner", name="admin")
oper = M.main_menu_text([1], 9, 30, 4, True, role="operator", name="Bob", busy_tip="Bob 在跑")
texts = [owner, oper, M.campaign_menu_text(), M.groups_menu_text(), M.watch_menu_text(),
         M.accounts_menu_text(), M.profile_menu_text(), M.settings_menu_text(),
         M.pool_menu_text(3)]
ck("T4 菜单文本全部 <=4 行", all(len(t.split("\n")) <= 4 for t in texts),
   [len(t.split("\n")) for t in texts])
ck("T5 主菜单带身份与数据", "控制面板" in owner and "👤2" in owner and "📁9" in owner, owner)
ck("T6 操作员才提示、老板不提示",
   "需找admin" in oper and "需找admin" not in owner, (owner, oper))
ck("T7 运行中状态写在文本里", "任务中" in oper and "占用：Bob 在跑" in oper, oper)

print("\n" + ("PY311_OK" if not FAILS else "FAILED: " + "; ".join(FAILS)))
sys.exit(1 if FAILS else 0)
