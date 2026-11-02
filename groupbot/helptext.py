"""用户能看到的命令说明。测试会核对这些名字确实注册了。"""

HELP_TEXT = """📚 <b>命令列表</b>

<b>👤 用户命令</b>
/start - 开始使用
/help - 查看帮助
/sign - 每日签到（按群计算，连续签到有奖励）
/points - 查看本群积分
/rank - 本群活跃度排行
/luck - 今日运势（同一天结果固定）
/dice - 掷骰子，消耗积分，使用 Telegram 骰子

<b>👮 管理员命令</b>
/ban - 禁言。回复消息，或 /ban @用户名、/ban 数字ID
时长默认按分钟，也可写 30s、2h、1d，0 表示永久
/unban - 解除禁言
/kick - 踢出群组（之后可以再加入）
/warn - 警告。达到上限会禁言并清零
/unwarn - 减少一次警告，/unwarn all 清零
/warns - 查看警告次数
/setwelcome - 设置欢迎语，支持 {name} {username} {id} {chat}
/addword - 添加本群屏蔽词
/delword - 删除或停用屏蔽词
/wordlist - 查看当前屏蔽词
/settings - 查看或修改本群开关
/stats - 群统计
/clean - 清理最近消息，例如 /clean 20
/violations - 查看最近的违规和管理记录

管理员包括群管理员和配置里的超级管理员。不能处罚管理员或机器人自己。
"""

USER_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "开始使用"),
    ("help", "查看命令"),
    ("sign", "每日签到"),
    ("points", "查看积分"),
    ("rank", "活跃度排行"),
    ("luck", "今日运势"),
    ("dice", "掷骰子"),
)

ADMIN_COMMANDS: tuple[tuple[str, str], ...] = (
    ("ban", "禁言用户"),
    ("unban", "解除禁言"),
    ("kick", "踢出用户"),
    ("warn", "警告用户"),
    ("unwarn", "撤销警告"),
    ("warns", "查看警告"),
    ("setwelcome", "设置欢迎语"),
    ("addword", "添加屏蔽词"),
    ("delword", "删除屏蔽词"),
    ("wordlist", "查看屏蔽词"),
    ("settings", "群设置"),
    ("stats", "群统计"),
    ("clean", "清理消息"),
    ("violations", "违规记录"),
)

SETTINGS_HELP = """⚙️ <b>群设置</b>

/settings anti_ad 开|关 — 广告过滤
/settings links 开|关 — 未允许的链接
/settings channel 开|关 — 外来频道身份发言
/settings forwards 开|关 — 转发消息
/settings verify 开|关 — 入群验证
/settings captcha 开|关 — 算术验证（关则改为按钮）
/settings service_delete 开|关 — 删除进群提示
/settings leave_delete 开|关 — 删除退群提示
/settings flood 开|关 — 刷屏限制
/settings restrict 分钟 — 新成员限制发链接
/settings mute 秒 — 自动禁言时长，0 为永久
/settings warns 次数 — 警告上限
/settings timeout 秒 — 验证超时
/settings flood_limit 条数
/settings flood_window 秒
/settings flood_mute 秒 — 至少 30
/settings whitelist add 域名
/settings whitelist del 域名
/settings log_channel 频道ID|关
"""
