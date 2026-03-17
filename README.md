# Telegram 超级群管机器人 🤖

一个功能强大的 Telegram 群组管理机器人，整合广告过滤、群管、娱乐等多种功能。

## ✨ 功能特性

### 🛡️ 广告过滤系统
- 自动检测并删除广告消息
- 关键词过滤（支持正则表达式）
- 链接检测（白名单/黑名单模式）
- 新用户限制（进群 X 分钟内不能发链接）
- 重复消息检测

### 👮 群管功能
- 自动欢迎新人（自定义欢迎语）
- 自动禁言/踢人
- 设置群组规则
- 成员统计（活跃度排行）
- 消息记录查询
- 批量清理消息

### 🎮 娱乐功能
- 每日签到
- 积分系统
- 抽签/运势
- 小游戏（猜数字、掷骰子等）

### 📊 数据统计
- 群活跃度统计
- 消息数量排行
- 成员增长趋势
- 违规记录查询

### 🔔 定时任务
- 定时发送群公告
- 定时清理旧消息
- 定时提醒

## 🚀 快速开始

### 1. 创建机器人
1. 找 [@BotFather](https://t.me/BotFather) 创建机器人
2. 获取 Bot Token
3. 关闭 Privacy Mode（让机器人能看到所有消息）

### 2. 配置
```bash
git clone https://github.com/mochu8/telegram-super-bot.git
cd telegram-super-bot
cp config.example.json config.json
# 编辑 config.json 填入你的配置
```

### 3. 运行
```bash
pip install -r requirements.txt
python bot.py
```

## 📝 命令列表

### 普通用户
| 命令 | 说明 |
|------|------|
| /start | 开始使用 |
| /help | 帮助信息 |
| /sign | 每日签到 |
| /points | 查看积分 |
| /rank | 活跃度排行 |
| /luck | 今日运势 |
| /dice | 掷骰子 |

### 管理员
| 命令 | 说明 |
|------|------|
| /ban @用户 时间 原因 | 禁言用户 |
| /unban @用户 | 解除禁言 |
| /kick @用户 | 踢出用户 |
| /warn @用户 原因 | 警告用户 |
| /setwelcome 文本 | 设置欢迎语 |
| /addword 关键词 | 添加屏蔽词 |
| /delword 关键词 | 删除屏蔽词 |
| /wordlist | 查看屏蔽词列表 |
| /settings | 群设置 |
| /stats | 群统计 |
| /clean 数量 | 清理消息 |

## ⚙️ 配置文件

```json
{
  "bot_token": "你的Bot Token",
  "admin_ids": [123456789],
  "welcome_message": "欢迎 {name} 加入本群！",
  "anti_ad": {
    "enabled": true,
    "keywords": ["广告", "推广", "加群"],
    "ban_links": true,
    "whitelist_links": ["t.me/official"],
    "new_user_restrict": 30
  },
  "auto_delete": {
    "service_msg": true,
    "leave_msg": true
  },
  "points": {
    "sign_daily": 10,
    "msg_per_point": 100
  }
}
```

## 📄 许可

MIT License
