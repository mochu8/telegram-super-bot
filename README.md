# Telegram 超级群管机器人

按群管理的 Telegram 机器人：广告和刷屏过滤、入群验证、警告、禁言，以及签到、积分和运势。界面是中文。

运行环境是 Python 3.11 及以上。数据放在 SQLite 里，积分、签到、警告和排行都按 `(群 ID, 用户 ID)` 分开，不会串到别的群。

## 功能

- 广告过滤会看正文、图片说明、隐藏链接、转发和频道身份发言，编辑后的消息也会再查一遍。白名单按域名判断，关键词不区分大小写。
- 默认规则收紧了，不再把「加新」「免费」「优惠」这类日常聊天当成广告。各群可以用命令再加词。
- 刷屏限制、新成员限时禁发链接。
- 新人进群先禁言，点按钮或做算术题后解除；超时移出。进群、退群提示可以延迟删除，删除由任务队列执行，不会卡住别的消息。
- 禁言、踢人、警告支持回复、`@用户名` 和数字 ID。群管理员和配置里的超级管理员都能操作，但不能处罚管理员或机器人自己。
- 警告达到上限后按 `mute_duration` 禁言并清零。有 `/unwarn` 和 `/warns`。
- 管理操作写入数据库，也可以转发到日志频道。`/violations` 查询最近记录。
- 签到、发言积分、排行、当日固定运势，骰子使用 Telegram 原生骰子。
- 按配置定时备份数据库，并清理过期的消息记录。

## 准备机器人

1. 在 [@BotFather](https://t.me/BotFather) 创建机器人，拿到 token。
2. 对这个机器人发送 `/setprivacy`，选择 **Disable**，否则它看不到群里的普通消息。
3. 把机器人拉进群，设为管理员，打开删除消息和封禁用户的权限。
4. 自己的数字 ID 可以问 [@userinfobot](https://t.me/userinfobot)。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
```

在 `.env` 里填写 `BOT_TOKEN`。`BOT_TOKEN` 优先于 `config.json` 里的 `bot_token`。`config.json`、数据库和 `.env` 都不会被提交。

只想用环境变量时，可以不创建 `config.json`，其余项使用内置默认值。

## 运行

```bash
python -m groupbot
```

`python bot.py` 同样可以。用 Ctrl+C 停止。

## Docker

```bash
cp .env.example .env
# 编辑 .env，至少填 BOT_TOKEN
docker compose up -d --build
```

数据库在卷 `bot-data`，备份在 `bot-backups`。容器里的路径是 `/app/data/bot.db` 和 `/app/backups`。进程用户是 uid `10001`。如果改成绑定宿主目录，请把目录所有者改成这个 uid。compose 会把容器的 `8080` 映射出来，方便隧道接到本机；只有设置了 `PANEL_BASE_URL` 时，这个端口才有面板服务。

## Debian 上的 systemd

```bash
sudo useradd --system --create-home --home-dir /opt/telegram-super-bot bot
sudo mkdir -p /opt/telegram-super-bot
sudo rsync -a ./ /opt/telegram-super-bot/ --exclude .git
sudo python3 -m venv /opt/telegram-super-bot/.venv
sudo /opt/telegram-super-bot/.venv/bin/pip install -r /opt/telegram-super-bot/requirements.txt
sudo cp .env.example /opt/telegram-super-bot/.env
sudo chown -R bot:bot /opt/telegram-super-bot
# 编辑 /opt/telegram-super-bot/.env
sudo cp deploy/groupbot.service /etc/systemd/system/groupbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now groupbot
```

日志：`journalctl -u groupbot -f`。

## 命令

### 用户

| 命令 | 说明 |
|------|------|
| `/start` | 开始使用 |
| `/help` | 命令列表 |
| `/sign` | 本群每日签到。连续签到到 7、30、100、365 天时，额外再给两份当日积分 |
| `/points` | 本群积分、消息数、连续签到 |
| `/rank` | 本群积分排行 |
| `/luck` | 今日运势。同一个人同一天的结果固定，重启也不会变 |
| `/dice` | 消耗积分掷 Telegram 骰子。默认 5 分，点数达到 5 奖励 10 分 |
| `/rules` | 查看本群群规。群规没写或功能关闭时会说明 |
| `/panel` | 打开管理面板。群里只有管理员能看到按钮 |

私聊里签到、积分、运势和骰子也能用，但和任何一个群的数据都是分开的。积分功能被关掉时，`/sign`、`/points`、`/rank`、`/dice` 会直接说明。

### 管理员

群管理员，以及 `ADMIN_IDS` / `admin_ids` 里的超级管理员，可以使用：

| 命令 | 说明 |
|------|------|
| `/ban` | 禁言。回复消息后 `/ban 60 原因`，或 `/ban @用户 2h 原因`、`/ban 数字ID 1d 原因`。裸数字是分钟，`0` 是永久 |
| `/unban` | 解除禁言 |
| `/kick` | 踢出群，之后仍可再加入 |
| `/warn` | 警告。达到上限后按 `mute_duration` 禁言，并把警告清零 |
| `/unwarn` | 减少一次警告。`/unwarn all` 清零，也可以写成一个数字直接设定 |
| `/warns` | 查看警告次数 |
| `/setwelcome` | 设置欢迎语。`/setwelcome 默认` 恢复全局文案 |
| `/addword` | 给本群加屏蔽词 |
| `/delword` | 删除或停用屏蔽词，包括默认词 |
| `/wordlist` | 查看当前生效的屏蔽词 |
| `/settings` | 查看或修改本群开关，见下文 |
| `/stats` | 成员数和记录数量 |
| `/clean` | 清理最近的消息，例如 `/clean 20`，最多 100 条 |
| `/violations` | 最近的违规和管理记录，例如 `/violations 20` |
| `/setrules` | 设置群规。`/setrules 清空` 会清掉正文 |
| `/reply` | 添加自动回复：`/reply 关键词 \| 回复内容` |
| `/delreply` | 删除自动回复 |
| `/replies` | 查看自动回复 |

`/ban` 表示禁言（限制发言），不是永久封禁。踢人用 `/kick`。

对象可以是回复、`@用户名` 或数字 ID。用户名只有在这个机器人见过对方，或者 Telegram 能解析这个公开用户名时才查得到。

### 本群设置

`/settings` 不带参数时会列出当前值和用法。常用的有：

- `/settings anti_ad 开`、`/settings links 关`
- `/settings verify 开`、`/settings captcha 开`（改成算术题；关掉则回到按钮）
- `/settings flood 开`、`/settings channel 开`、`/settings forwards 关`
- `/settings service_delete 开`、`/settings leave_delete 开`
- `/settings restrict 30`（新成员多少分钟内不能发未允许的链接）
- `/settings mute 3600`（自动禁言秒数，`0` 为永久）
- `/settings warns 3`
- `/settings whitelist add example.com`、`/settings whitelist del example.com`
- `/settings log_channel -1001234567890` 或 `/settings log_channel 关`
- `/settings welcome 开`、`/settings reply 开`、`/settings announce 开`、`/settings rules 开`、`/settings points 关`
- `/settings timeout 120`、`/settings flood_limit 5`、`/settings flood_window 8`、`/settings flood_mute 60`

欢迎语支持 `{name}`、`{username}`、`{id}`、`{chat}`。用户名里的特殊字符会转义，不会被当成 HTML。自动回复、定时公告和群规正文按纯文本发送。

## 环境变量

写在 `.env` 里，或由 systemd 的 `EnvironmentFile`、Docker 的 `env_file` 注入。同名项优先于 `config.json`。

| 变量 | 作用 |
|------|------|
| `BOT_TOKEN` | 必填。BotFather 的 token |
| `ADMIN_IDS` | 超级管理员数字 ID，逗号分隔 |
| `LOG_CHANNEL_ID` | 可选。全局管理日志频道或群 |
| `TZ` | 签到和运势用的时区，默认 `Asia/Shanghai` |
| `DB_PATH` | SQLite 路径，默认 `data/bot.db` |
| `BACKUP_DIR` | 备份目录，默认 `backups` |
| `CONFIG_PATH` | 配置文件路径，默认 `config.json`。文件可以不存在 |
| `PANEL_BASE_URL` | 面板的对外 https 地址。留空则不启动网页 |
| `PANEL_HOST` | 面板监听地址，默认 `0.0.0.0` |
| `PANEL_PORT` | 面板监听端口，默认 `8080` |
| `PANEL_AUTH_MAX_AGE` | `initData` 有效秒数，默认 `86400` |
| `PANEL_RATE_LIMIT` | 每个用户在窗口内的请求上限，默认 `60` |
| `PANEL_RATE_WINDOW` | 限流窗口秒数，默认 `60` |
| `PANEL_DOMAIN` | 只给 Caddy 用，见下面的域名部署 |

## 配置说明

`config.json` 可以不存在。下面这些原来就有、但以前没有生效的项，现在会用到：

| 配置 | 作用 |
|------|------|
| `anti_ad.mute_duration` | 警告达上限时的禁言秒数，默认 3600。`/settings mute` 可按群覆盖 |
| `points.msg_per_point` | 每累计这么多条正常消息得 1 分，默认 50 |
| `points.max_points_per_day` | 每天通过发言得到的积分上限，默认 100。签到和骰子另计 |
| `backup.enabled` / `backup.interval_hours` | 是否定时备份，以及间隔小时数。备份在 `backups/`，默认保留 7 份 |

消息正文只存截断后的一小段，默认 7 天后删除，全库最多保留 5 万条。内部错误只写日志；如果配置了日志频道，频道里也只会收到错误类型，不会把群消息或 token 再发出去。

时区默认 `Asia/Shanghai`。禁言的 `until_date` 使用带时区的 UTC 时间。

从 v1 升级时，旧的 `bot_data.db` 不能直接沿用（积分以前是全局的）。请换一个 `DB_PATH`，或把旧文件移走。程序发现旧表结构会拒绝启动。

如果本地还留着旧的 `config.json`，里面的「免费」「优惠」和 `加[我]?…[信新]` 会继续误伤聊天。请对照 `config.example.json` 更新规则。

## 管理面板

面板是一个 Telegram Mini App，由同一个 Python 进程提供页面和接口。它改的是命令使用的那一套群设置：屏蔽词、正则、白名单、防刷、警告、入群验证、欢迎语和按钮、自动回复、定时公告、群规、积分、日志频道。不设置 `PANEL_BASE_URL` 时，网页服务不会启动，机器人其他功能不受影响。

私聊里的 `/panel` 会给出「进入管理面板」按钮。群里的按钮会尝试直接打开 Mini App；如果只是跳进了私聊，机器人会再发一个按钮。面板用 Telegram 的 `initData` 登录，没有另外的密码。每次请求都会校验签名和 `auth_date`，并且只允许该群的管理员或配置里的超级管理员修改。

### 本地测试

Mini App 必须使用 https。本机没有域名时，可以用临时隧道把本机 `8080` 端口暴露出去：

```bash
# .env 里先写 PANEL_HOST=0.0.0.0 和 PANEL_PORT=8080
# 隧道起来之后，把给出的 https 地址写进 PANEL_BASE_URL，再启动机器人
cloudflared tunnel --url http://127.0.0.1:8080
# 或者
ngrok http 8080
python -m groupbot
```

然后在 [@BotFather](https://t.me/BotFather) 里：

1. 打开 Bot Settings → Configure Mini App，把主 Mini App 的地址设成 `PANEL_BASE_URL`。群里的「打开管理面板」是 `https://t.me/机器人用户名?startapp=...`，必须配这个主入口。`/newapp` 用来做带短名的入口，不是这条链接所必需的。
2. 用 `/setdomain` 绑定同一个域名。隧道地址变了就要改这两处。
3. 把机器人设为群管理员。私聊发送 `/panel`，或在群里发送 `/panel`。主 Mini App 还没配对时，用群里的「改到私聊打开」，私聊里的按钮不依赖主 Mini App。

### 有域名时

把域名解析到服务器，`.env` 里设置 `PANEL_DOMAIN` 和 `PANEL_BASE_URL=https://你的域名`。然后：

```bash
docker compose --profile https up -d --build
```

Caddy 会为 `PANEL_DOMAIN` 申请证书，并把 https 转到机器人的 `8080`。不需要面板时，照旧 `docker compose up -d --build`，不要设置 `PANEL_BASE_URL` 即可。

## 开发

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check .
pytest
```

GitHub Actions 会在 Python 3.11 和 3.12 上跑同样的检查。测试覆盖广告判断、签到积分、按群存储和命令注册，不会连接 Telegram。

## 许可

MIT License，见 [LICENSE](LICENSE)。
