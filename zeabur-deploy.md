# Zeabur 部署 Telegram 群发系统（PostgreSQL + Bot 对话登录版）

## 项目文件说明

```
tg-sender-zeabur/
├── main.py            ← 程序入口（健康检查 + 连接 Bot + 加载账号 + 热替换监听）
├── config.py          ← 配置（全部读环境变量）/ 全局状态 / 日志
├── bot.py             ← Bot 指令与菜单事件处理
├── bot_menu.py        ← 菜单键盘体系（按钮文字 / 键盘布局 / 菜单文本）
├── accounts.py        ← 账号登录（Bot 对话验证码）与 session zip 接收
├── sender.py          ← 群发 / 广播 / 转发执行器
├── collector.py       ← 群成员采集 / 频道历史消息采集
├── db.py              ← PostgreSQL（名单 / 记录 / 统计 / session 持久化）
├── filter.py          ← 目标过滤
├── profile.py         ← 批量改资料
├── reporter.py        ← 举报执行器
├── reasons.py         ← 举报理由定义
├── ai.py              ← AI 文案生成（可选，OpenAI 兼容接口）
├── law.py / law.json  ← 法条引用数据
├── ops_state.py       ← 任务运行状态
├── health.py          ← /:8080 健康检查 HTTP 服务
├── tg_sender.py       ← 旧版单文件程序（已由模块化版本取代，仅留档）
├── requirements.txt   ← 依赖
├── Dockerfile         ← Zeabur 容器构建
├── zeabur.json        ← Zeabur 服务配置（长驻进程 + 健康检查）
└── .gitignore         ← 忽略 session 文件等敏感内容
```

## 一、架构

```
[Zeabur 容器]  main.py  ← 常驻进程，通过 Bot Token 连接 Telegram
     │
     ├── 控制 Bot (@your_bot)   ← 你唯一的管理入口：所有操作在 Telegram 对话里完成
     │       · 添加账号：点「账号管理」→「添加账号」，发手机号 + 验证码即可
     │       · session 自动存入 PostgreSQL，重启不丢，无需手动上传
     │       · 也可随时发送 tg_sessions.zip 热替换（兼容旧流程）
     │
     ├── 用户号 (session)        ← 实际执行群发 / 采集 / 举报的账号
     │
     └── PostgreSQL             ← 名单 / 发送记录 / 统计 / 群信息 / session / 文案池
```

**核心优势：**
1. ✅ **全程 Bot 对话操作** — 添加账号、验证码、群发、设置全在 Telegram 里完成，服务器零交互、零验证码文件
2. ✅ **Session 存 PostgreSQL** — 容器重建 / 重启后自动恢复登录态，不依赖本地文件
3. ✅ **模块化** — 群发、采集、举报、AI 文案各自独立模块，便于维护

> 旧版 `make_session.py` 本地生成流程已废弃（保留在 .gitignore 中防止误提交），现在**不需要在本地跑任何脚本**。

---

## 二、Zeabur 部署步骤

### 1. 创建 PostgreSQL 服务
- Zeabur Dashboard → 新建服务 → **PostgreSQL**
- Zeabur 会自动把 `DATABASE_URL` 注入同项目内的其他服务，无需手动复制

### 2. 创建主服务（tg-sender）
- 新建服务 → Git 仓库（本仓库）或手动上传
- 服务类型：**长驻进程**（zeabur.json 已配置好，端口 8080，健康检查 `/`）

### 3. 配置环境变量

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `API_ID` | ✅ | [my.telegram.org](https://my.telegram.org) → API development tools |
| `API_HASH` | ✅ | 同上 |
| `BOT_TOKEN` | ✅ | 控制 Bot 的 token（@BotFather 创建） |
| `OWNER_ID` | ✅ | 你的 Telegram user_id（数字，可用 @userinfobot 查询） |
| `OPERATOR_IDS` | 可选 | 多人使用：操作员 user_id 白名单，逗号分隔（如 `222,333`）。仅作首次引导，日常用 `/addop` 增删更省事 |
| `DATABASE_URL` | ✅ | PostgreSQL 连接串（Zeabur 自动注入） |
| `MIN_DELAY` | 可选 | 发送间隔下限秒数（默认 20） |
| `MAX_DELAY` | 可选 | 发送间隔上限秒数（默认 60） |
| `DAILY_LIMIT` | 可选 | 每账号每日上限（默认 100） |
| `BATCH_SIZE` | 可选 | 每批数量（默认 30） |
| `BATCH_SLEEP` | 可选 | 批间休息秒数（默认 300） |
| `MAX_FLOOD_WAIT` | 可选 | FLOOD_WAIT 等待上限秒数（默认 3600） |
| `AI_BASE_URL` | 可选 | OpenAI 兼容接口地址（默认官方，配了才启用 AI 功能） |
| `AI_API_KEY` | 可选 | 对应 API Key（不配则 AI 相关功能自动禁用） |
| `AI_MODEL` | 可选 | 模型名（默认 gpt-4o-mini） |
| `PORT` | 可选 | 健康检查端口（默认 8080） |
| `DATA_DIR` | 可选 | 本地数据目录（默认 /data，仅存日志；session 在数据库） |

> ⚠️ 旧文档中的 `ACCOUNT_1_PHONE` 等变量**已废弃**——账号全部通过 Bot 对话登录，不再需要环境变量预设手机号。

### 4. 挂数据卷（可选但推荐）
- 给服务添加 **Volume** 挂载到 `/data`（存放运行日志 `tg_sender.log`，重启不丢）
- 即使不挂 Volume，程序也能运行（日志会退回写到应用目录）

### 5. 部署
- push 代码后 Zeabur 自动构建 Dockerfile 并启动
- 启动成功后你的控制 Bot 会发来：`🟢 群发系统已上线，N 个账号可用`

---

## 三、添加账号（首次必做）

部署完成后 Bot 会提示「服务器上还没有已登录的账号」，两种方式添加：

### 方式一：Bot 对话登录（推荐）
1. Telegram 里打开控制 Bot → `/menu` → 「👥 账号管理」→「添加账号」
2. 直接发送要群发的**用户号的手机号**（国际格式，如 `+8613800138000`）
3. Telegram 官方发的**验证码**会出现在该用户号上，把数字回复到 Bot 对话里
4. 若开了两步验证，再回复密码
5. 登录成功自动上线，session 存入 PostgreSQL，**无需重启**

### 方式二：/login 指令
- `/login` 给所有已在线账号重新触发登录；`/login 2` 只操作账号 2

### 替换死号
1. 「账号管理」→「添加账号」登录新号即可；旧 session 可在数据库 `tg_sessions` 表中删除
2. 或随时把新的 `tg_sessions.zip` 直接发给 Bot → 自动**热替换**（断开旧连接 → 加载新账号），全程无需重启服务，数据不丢

---

## 四、启动后使用

在 Telegram 里找到控制 Bot，发 `/start` 或 `/menu` 打开主面板。所有功能通过底部按钮操作：

| 主菜单 | 功能 |
|--------|------|
| 🚀 群发运营 | 五步向导：① 选群 → ② 拉名单 → ③ 写文案 → ④ 账号准备 → ⑤ 开始群发；支持暂停/继续/停止/查进度 |
| 📥 群管理 | 查看我的群 / 加群 / 批量导入目标 |
| 👥 账号管理 | 账号列表 / 添加账号 / 批量改资料 / 账号过滤 |
| 📊 数据看板 | 查看发送统计 |
| ⚙️ 系统设置 | 发送间隔 / 每日上限 / 并行账号数 / 文本模式 |
| 🚨 举报 | 举报用户/频道 / AI 批量举报 / 指定理由举报 / 账号冷却状态（举报后账号进入 30 分钟冷却） |

### 兼容旧指令

| 指令 | 作用 |
|------|------|
| `/mygroups` | 列出你加入的所有群/频道 |
| `/collect @群名` | 拉取该群成员到名单 |
| `/collect_history @频道` | 采集频道历史消息进文案池 |
| `/sendto 推广内容` | 给名单所有人私信（可多行） |
| `/broadcast @群1,@群2 内容` | 广播到多个群 |
| `/forward @源频道 @目标群` | 转发频道消息 |
| `/stats` | 查看统计 |
| `/login [n]` | 触发账号 n（或全部）登录 |

---

## 四·五、多人使用（操作员白名单）

一套系统只有一个 `OWNER_ID`（admin），可以拉若干**操作员**共用号池干活。现在操作员与admin同权（仅增删操作员除外）。

### 开通 / 踢人（全在 Telegram 完成，不需重启）

| 指令 | 谁能用 | 作用 |
|------|--------|------|
| `/whoami` | 任何人 | 查自己的 user_id（给对方发截图用） |
| `/addop <user_id> [备注名]` | 仅admin | 授权，立即生效 |
| `/dropop <user_id>` | 仅admin | 停用（审计记录保留） |
| `/oplist` | 仅admin | 看当前白名单 |
| `/oplog [条数]` | 全员可看全量 | 操作审计（谁干了什么、停了谁的任务） |

典型流程：
1. 让对方先私聊你的控制 Bot 发一条 `/start`（没说过话的 Bot 主动发消息会被 Telegram 拒），
   他收到的「⛔ 无权限」里带自己的 user_id（也可发 `/whoami`）
2. 你执行 `/addop 123456789 小王`
3. 通知对方重新发 `/start` 即可开工

> 也可以先在 Zeabur 环境变量里填 `OPERATOR_IDS=222,333` 做首次引导，但日常用 `/addop` 更方便（以数据库 `operators` 表为准）。

### 权限矩阵（已按老板要求：操作员全开）

| 能力 | admin | 操作员 |
|---|---|---|
| 群发运营、选群、写文案、暂停/继续/停止 | ✅ | ✅ |
| 群管理：我的群 / 加群 / 批量导入 / 删除群 | ✅ | ✅ |
| 账号管理：登录、添加账号、改资料、重置冷却 | ✅ | ✅ |
| 发 session 压缩包热替换 | ✅ | ✅ |
| 系统设置：速度 / 配额 / 文本模式 / 近7天 / 重复推广 | ✅ | ✅ |
| 举报中心全套 | ✅ | ✅ |
| 数据看板、操作审计 /oplog（看全量） | ✅ | ✅ |
| **增删操作员** `/addop` `/dropop` `/oplist` | ✅ | ❌ 仅admin |

> 唯一保留给admin的是「增删操作员」——否则操作员能自己拉人扩权、
> 甚至把admin踢出白名单。这一条我建议永远不要开。

> **登录验证码的归属**：谁点「登录 / 添加账号」，验证码和提示就发给谁，
> 系统只收这个发起人的回复，避免两个人同时登录时验证码串号。

> **停止任务已开放给所有人**：任何人都能停别人正在跑的任务，
> 但 `/oplog` 会记成「张三 停掉了 Alice 的任务」，责任可追。

### 共用号池的两道排队锁

1. **任务锁（busy）**：号池共用，同一时刻只跑一个重任务；被挡住的人会看到「占用者：某人（已 N 分钟）」，而不是干等。
2. **名单锁（list_claim）**：`targets` 名单全库只有一份，而「① 选群」会**清空旧名单再拉人**——所以 A 占用名单时，B 选群会被拒绝并看到 A 的名字，防止把同事的名单洗掉。租期 30 分钟自动失效（人挂了下一个人自动能接）。
3. 群发草稿（选了哪个群/写了什么文案）**按人分键**存储，两个人同时编辑互不覆盖。

### 落地数据（启动自动建表，无需手动 SQL）

| 表 | 用途 |
|----|------|
| `operators` | 操作员白名单（uid, name, enabled, added_by） |
| `op_log` | 审计流水：选群/存文案/开跑/加群/导入/采集/加账号/停任务/增删操作员 |
| `campaigns` | 每次群发的归属（谁发的、哪个群、目标/实发人数），看板「近 7 天按人」统计读它 |

---

## 五、数据库表结构（启动时自动创建）

| 表 | 用途 |
|----|------|
| `targets` | 目标名单（uid, username, access_hash） |
| `sent_log` | 发送记录（account_no + uid 唯一，防重发） |
| `stats` | 每日统计（stat_date, account_no） |
| `groups_info` | 已加入群组信息 |
| `tg_sessions` | **所有 Telegram session**（bot + 用户号，StringSession 字符串） |
| `messages_pool` | 采集的频道历史消息（文案池） |
| `account_cooldown` | 撞 430/限流后的账号冷却记账 |
| `operators` | 多人使用：操作员白名单 |
| `op_log` | 多人使用：操作审计流水 |
| `campaigns` | 多人使用：群发任务归属与成果统计 |

无需手动建表；旧库会自动补列（如 `targets.access_hash`）。

---

## 六、常见问题

**Q: 服务器上要输验证码吗？**
A: 验证码还是 Telegram 官方发给用户号的，但你只需把数字**回复到 Bot 对话里**，全程在 Telegram 完成，无需登录服务器。

**Q: 重启 / 重建容器后账号还在吗？**
A: 在。session 存在 PostgreSQL `tg_sessions` 表，重启后自动恢复；仅当 Telegram 服务端注销该 session（AUTH_KEY_UNREGISTERED）时才需重新登录，Bot 会自动检测并重新登录控制 Bot 本身。

**Q: Zeabur 部署时报缺环境变量退出？**
A: 启动日志会明确列出缺哪些（`API_ID / API_HASH / BOT_TOKEN / OWNER_ID / DATABASE_URL`），在 Variables 里补齐后重新部署即可。

**Q: AI 功能没反应？**
A: AI 是可选功能，必须配置 `AI_BASE_URL` + `AI_API_KEY`（OpenAI 兼容接口均可，如中转站）。未配置时相关按钮会走默认兜底文案。

**Q: session 文件是敏感数据吗？**
A: 是，等同于账号登录态。session 字符串存于你的私有 PostgreSQL；本地 `*.session` 文件已被 `.gitignore` 忽略，切勿公开提交。
