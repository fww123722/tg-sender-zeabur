# Zeabur 部署 Telegram 群发系统（PostgreSQL + Bot 传输 Session 版）

## 项目文件说明

```
tg-sender-zeabur/
├── tg_sender.py        ← 主程序（服务器运行：读环境变量 + PostgreSQL + 通过 Bot 收 session）
├── make_session.py     ← 本地 Session 生成器（只在你自己电脑上跑，自动打包 zip）
├── config.py           ← 本地配置（make_session.py 读取；服务器不用）
├── requirements.txt    ← 依赖
├── Dockerfile          ← Zeabur 容器构建
├── zeabur.json         ← Zeabur 项目配置（长驻进程模式）
└── .gitignore          ← 忽略 session 文件（防泄露）
```

## 一、架构（新方案）

```
[你自己的电脑]  运行 make_session.py（输入验证码）→ 自动打包 tg_sessions.zip
                        │
                        ▼   （把 zip 直接发给控制 Bot）
[Zeabur 容器]  tg_sender.py  ← 收到 zip 自动解压到 /data，加载 session，绝不碰验证码
     │
     ▼
[PostgreSQL]  ← 名单 / 发送记录 / 统计
```

**核心优势：**
1. ✅ **Session 本地生成 + Bot 传输** — 验证码只在本地输入，生成后打包 zip 发给 Bot 即可，**无需手动上传文件、无需重新部署**
2. ✅ **数据用 PostgreSQL** — 名单、发送记录、统计全存数据库，重启零丢失
3. ✅ **服务器零交互** — 全程通过 Telegram 管理，服务器只是常驻进程

---

## 二、本地生成 Session（只有首次需要）

### 1. 准备
- 到 [my.telegram.org](https://my.telegram.org) 获取 `API_ID` / `API_HASH`
- 在 Telegram 里创建控制 Bot，拿到 `BOT_TOKEN`
- 打开 `config.py`，填好：
  ```python
  API_ID = 你的api_id
  API_HASH = "你的api_hash"
  ACCOUNT_1_PHONE = "+8613800138000"   # 要群发的用户号，可加 ACCOUNT_2_PHONE 等
  ```

### 2. 安装依赖并运行
```bash
pip install -r requirements.txt
python make_session.py
```
按提示输入验证码（若开二步验证再输入密码），每个账号生成一个 `tg_session_N.session`，最后**自动打包成 `tg_sessions.zip`**。

### 3. 发送到服务器
把 `tg_sessions.zip` 文件直接发送给控制 Bot：
- 服务器收到后自动解压到 `/data`，并重新加载所有 session
- 加载成功的账号会收到 ✅ 通知，全部就绪后系统上线

> 注意：若服务器提示"等待 session 压缩包"，直接发 zip 给它即可，**无需重启服务**。

---

## 三、Zeabur 部署步骤

### 1. 创建 PostgreSQL 服务
- Zeabur Dashboard → 新建服务 → 选择 **PostgreSQL**
- 记下 `DATABASE_URL`（形如 `postgres://user:pass@host:port/dbname`）

### 2. 创建主服务（tg-sender）
- 新建服务 → 选 Python / 推 Git 仓库 / 手动上传
- 服务类型选 **长驻进程**
- **无需上传 session 文件**（通过 Bot 传输）

### 3. 配置环境变量

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `API_ID` | ✅ | my.telegram.org 获取 |
| `API_HASH` | ✅ | my.telegram.org 获取 |
| `BOT_TOKEN` | ✅ | 控制 Bot 的 token |
| `OWNER_ID` | ✅ | 你的 Telegram user_id（数字） |
| `DATABASE_URL` | ✅ | PostgreSQL 连接串（Zeabur 自动提供） |
| `ACCOUNT_1_PHONE` | ✅ | 账号1 手机号（与本地生成的 session 对应） |
| `ACCOUNT_2_PHONE` | 可选 | 账号2 … |
| `MIN_DELAY` | 可选 | 发送间隔下限（默认 20） |
| `MAX_DELAY` | 可选 | 发送间隔上限（默认 60） |
| `DAILY_LIMIT` | 可选 | 每日上限（默认 100） |
| `PORT` | 可选 | 健康检查端口（默认 8080） |

### 4. 挂载数据卷
- Zeabur 给服务添加 **Volume**，挂载到 `/data`（session 文件与日志放这里，重启不丢）

### 5. 部署
- 启动后 Bot 会发消息提示"等待 session 压缩包"（若本地还没生成）
- 把本地 `tg_sessions.zip` 发给 Bot → 自动解压加载 → 系统上线

---

## 四、替换账号（某个号死了怎么办？）

### 场景：账号1 被封/失效，换一个新号

**本地操作：**
1. 编辑 `config.py`，把 `ACCOUNT_1_PHONE` 改成新手机号
2. 运行 `python make_session.py --force 1`
   - `--force` 表示忽略已有的 session 文件，强制重新登录
   - `1` 表示只重建账号1，其他账号不变（不加数字则重建所有账号）
3. 按提示输入新号的验证码（如需二步验证再输密码）
4. 程序自动打包成 `tg_sessions.zip`

**服务器操作（无需重启服务）：**
5. 把 `tg_sessions.zip` 直接发给控制 Bot
6. Bot 自动解压，**热替换**运行中的账号：
   - 移除旧 session 文件（死号自然清除）
   - 断开旧客户端连接
   - 加载新客户端
   - 全部成功后通知你

> 整个过程**无需重新部署 Zeabur 服务**，数据表里的名单/发送记录/统计全部保留。

### 完整命令参考

| 命令 | 用途 |
|------|------|
| `python make_session.py` | 正常生成所有账号 session（已有有效 session 则跳过） |
| `python make_session.py --force` | 强制重新登录所有账号 |
| `python make_session.py --force 2` | 只强制重新登录账号2（替换时最常用） |
| `python make_session.py --pack` | **只打包、不登录、不验证码**（日常更新 zip 用） |
| 把 `tg_sessions.zip` 发给 Bot | 服务器热替换，无需重启 |

> 💡 **日常更新 session 包**：账号没变、只是想重新打包发给服务器时，用
> `python make_session.py --pack` —— 完全跳过登录和验证码，直接用现有 session 文件打包。
> 另外普通模式也做了保护：连接 Telegram 失败（网络/代理问题）时会**跳过该账号**而不是去触发验证码，避免误登录。

---

## 五、数据库表结构（自动创建）

程序启动时会自动建表：
- `targets` — 目标名单（uid, username, access_hash, created_at）
- `sent_log` — 发送记录（account_no, uid, sent_at）
- `stats` — 统计（stat_date, account_no, sent_today, total_sent）

无需手动建表。

---

## 五、启动后使用

在 Telegram 里找到控制 Bot，发 `/start`。

| 指令 | 作用 |
|------|------|
| `/mygroups` | 列出你加入的所有群/频道 |
| `/collect @群名` | 拉取该群成员到名单 |
| `/list` | 查看名单统计 |
| `/sendto 推广内容` | 给名单所有人私信（可多行） |
| `/broadcast @群1,@群2 内容` | 广播到多个群 |
| `/forward @源频道 @目标群` | 转发频道消息 |
| `/stats` | 查看统计 |
| `/pause` / `/resume` | 暂停 / 继续（暂停不退出任务） |
| `/speed 30` | 设置发送间隔 |
| `/quota 200` | 设置每日上限 |
| `/stop` | 停止当前任务 |

---

## 六、常见问题

**Q: 服务器上还要输验证码吗？**
A: 不需要。session 在本地生成后上传，服务器只读取。若服务器提示"未找到有效 session"，说明 session 文件没放对位置（应放入 `/data` 或项目目录）。

**Q: session 失效/换手机怎么办？**
A: 在本地重新运行 `python make_session.py` 重新生成，把新的 session 文件再部署上去即可。

**Q: 重启后数据还在吗？**
A: 在。数据存在 PostgreSQL，重启/重建都不丢。

**Q: session 文件是敏感数据吗？**
A: 是，等同于账号登录态。`.gitignore` 默认忽略它，不要公开提交。请通过 Volume 或私有仓库/手动上传方式部署。
