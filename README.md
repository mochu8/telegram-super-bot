# Telegram 超级群管机器人 🤖

一个功能强大的 Telegram 群组管理机器人，整合广告过滤、群管、娱乐等多种功能。

## ✨ 功能特性

### 🛡️ 广告过滤系统
- ✅ 自动检测并删除广告消息
- ✅ 关键词过滤（支持正则表达式）
- ✅ 链接检测（白名单/黑名单模式）
- ✅ 新用户限制（进群 X 分钟内不能发链接）
- ✅ 违规记录自动保存

### 👮 群管功能
- ✅ 自动欢迎新人（自定义欢迎语）
- ✅ 禁言/解除禁言
- ✅ 踢出用户
- ✅ 警告系统（3次警告自动禁言）
- ✅ 批量清理消息
- ✅ 群统计

### 🎮 娱乐功能
- ✅ 每日签到（连续签到有奖励）
- ✅ 积分系统
- ✅ 活跃度排行
- ✅ 今日运势
- ✅ 掷骰子游戏

### 💾 数据系统
- ✅ SQLite 数据库
- ✅ 用户数据持久化
- ✅ 消息记录
- ✅ 违规记录查询

## 🚀 快速开始

### 1. 创建机器人
1. 找 [@BotFather](https://t.me/BotFather) 创建机器人
2. 获取 Bot Token
3. **重要**：发送 `/setprivacy` 给 BotFather，选择 **Disabled**（让机器人能看到所有消息）
4. 将机器人添加到群组，并设为管理员

### 2. 安装依赖
```bash
git clone https://github.com/mochu8/telegram-super-bot.git
cd telegram-super-bot
pip install -r requirements.txt
```

### 3. 配置
```bash
cp config.example.json config.json
# 编辑 config.json 填入你的配置
```

**必填项：**
- `bot_token` - 从 BotFather 获取的 Token
- `admin_ids` - 你的 Telegram 用户 ID（数字）

**获取用户 ID：**
- 找 [@userinfobot](https://t.me/userinfobot)，发送任意消息即可获取

### 4. 运行
```bash
python bot.py
```

## 📝 命令列表

### 👤 用户命令
| 命令 | 说明 | 示例 |
|------|------|------|
| `/start` | 开始使用 | `/start` |
| `/help` | 帮助信息 | `/help` |
| `/sign` | 每日签到 | `/sign` |
| `/points` | 查看积分 | `/points` |
| `/rank` | 活跃度排行 | `/rank` |
| `/luck` | 今日运势 | `/luck` |
| `/dice` | 掷骰子（消耗5积分） | `/dice` |

### 👮 管理员命令
| 命令 | 说明 | 示例 |
|------|------|------|
| `/ban` | 禁言用户（回复消息） | 回复用户消息：`/ban 60 刷屏` |
| `/unban` | 解除禁言（回复消息） | 回复用户消息：`/unban` |
| `/kick` | 踢出用户（回复消息） | 回复用户消息：`/kick` |
| `/warn` | 警告用户（回复消息） | 回复用户消息：`/warn 请勿广告` |
| `/clean` | 清理消息 | `/clean 20` |
| `/stats` | 群统计 | `/stats` |

## ⚙️ 配置文件说明

```json
{
  "bot_token": "你的Bot Token",
  "admin_ids": [123456789, 987654321],
  "welcome_message": "欢迎语，支持{name}变量",
  "anti_ad": {
    "enabled": true,           // 是否启用广告过滤
    "keywords": ["广告"],      // 屏蔽关键词列表
    "regex_patterns": [],      // 正则表达式规则
    "ban_links": true,         // 是否禁止链接
    "whitelist_links": [],     // 链接白名单
    "new_user_restrict": 30    // 新用户限制（分钟）
  },
  "points": {
    "sign_daily": 10,          // 每日签到积分
    "sign_bonus": [7, 30]      // 连续签到奖励天数
  },
  "games": {
    "dice_cost": 5,            // 掷骰子消耗
    "dice_reward": 10,         // 掷骰子奖励
    "dice_reward_threshold": 5 // 获奖门槛（>=5点）
  }
}
```

## 🔧 常见问题

### Q: 机器人不响应命令
- 检查 Token 是否正确
- 确认机器人已加入群组
- 确认机器人是群组管理员
- 检查 Privacy Mode 是否已关闭

### Q: 广告过滤不生效
- 确认 `anti_ad.enabled` 为 `true`
- 检查机器人是否有删除消息权限
- 确认用户不是管理员（管理员消息不检查）

### Q: 如何获取用户 ID？
- 私聊 [@userinfobot](https://t.me/userinfobot)
- 或在群组中回复用户消息发送 `/info`（如果机器人支持）

## 📄 更新日志

### v1.0.1 (2026-03-18)
- ✅ 修复数据库字段索引错误
- ✅ 修复 ChatPermissions 导入问题
- ✅ 添加错误处理和日志
- ✅ 优化广告检测逻辑
- ✅ 添加警告系统（3次警告自动禁言）

### v1.0.0 (2026-03-18)
- 🎉 初始版本发布

## 📄 许可

MIT License
