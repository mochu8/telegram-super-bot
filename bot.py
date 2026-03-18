import asyncio
import json
import logging
import sqlite3
import re
import random
from datetime import datetime, timedelta
from typing import Optional, List

from telegram import Update, User, Chat, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 加载配置
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    print("❌ 错误：找不到 config.json 文件，请复制 config.example.json 并修改")
    exit(1)
except json.JSONDecodeError as e:
    print(f"❌ 错误：config.json 格式错误: {e}")
    exit(1)

class Database:
    def __init__(self, db_name='bot_data.db'):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        # 用户表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                points INTEGER DEFAULT 0,
                sign_date TEXT,
                sign_streak INTEGER DEFAULT 0,
                total_msgs INTEGER DEFAULT 0,
                join_date TEXT,
                warnings INTEGER DEFAULT 0,
                banned_until TEXT
            )
        ''')
        
        # 群组表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS groups (
                chat_id INTEGER PRIMARY KEY,
                chat_title TEXT,
                settings TEXT,
                welcome_msg TEXT,
                created_at TEXT
            )
        ''')
        
        # 消息记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                msg_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                message_text TEXT,
                created_at TEXT
            )
        ''')
        
        # 违规记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                violation_type TEXT,
                message_text TEXT,
                action_taken TEXT,
                created_at TEXT
            )
        ''')
        
        self.conn.commit()
    
    def add_user(self, user_id: int, username: str, first_name: str):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, join_date)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, datetime.now().isoformat()))
        self.conn.commit()
    
    def update_user(self, user_id: int, **kwargs):
        """更新用户信息"""
        cursor = self.conn.cursor()
        for key, value in kwargs.items():
            cursor.execute(f'UPDATE users SET {key} = ? WHERE user_id = ?', (value, user_id))
        self.conn.commit()
    
    def get_user(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return cursor.fetchone()
    
    def add_points(self, user_id: int, points: int):
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE users SET points = points + ? WHERE user_id = ?
        ''', (points, user_id))
        self.conn.commit()
    
    def log_message(self, chat_id: int, user_id: int, text: str):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO messages (chat_id, user_id, message_text, created_at)
            VALUES (?, ?, ?, ?)
        ''', (chat_id, user_id, text, datetime.now().isoformat()))
        cursor.execute('''
            UPDATE users SET total_msgs = total_msgs + 1 WHERE user_id = ?
        ''', (user_id,))
        self.conn.commit()
    
    def log_violation(self, chat_id: int, user_id: int, vtype: str, text: str, action: str):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO violations (chat_id, user_id, violation_type, message_text, action_taken, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (chat_id, user_id, vtype, text, action, datetime.now().isoformat()))
        self.conn.commit()
    
    def get_top_users(self, limit: int = 10):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT user_id, username, first_name, points, total_msgs
            FROM users ORDER BY points DESC LIMIT ?
        ''', (limit,))
        return cursor.fetchall()

db = Database()

# ============ 辅助函数 ============

def is_admin(user_id: int) -> bool:
    return user_id in CONFIG.get('admin_ids', [])

def is_group_chat(chat: Chat) -> bool:
    return chat.type in ['group', 'supergroup']

def format_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        return f"{seconds//60}分钟"
    elif seconds < 86400:
        return f"{seconds//3600}小时"
    else:
        return f"{seconds//86400}天"

# ============ 命令处理器 ============

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始命令"""
    user = update.effective_user
    db.add_user(user.id, user.username, user.first_name)
    
    text = f"""👋 你好，{user.first_name}！

🤖 我是超级群管机器人，可以帮助：
• 🛡️ 过滤广告和垃圾信息
• 👮 自动管理群组成员
• 🎮 提供娱乐小游戏
• 📊 统计群活跃度

📖 使用 /help 查看所有命令
    """
    await update.message.reply_text(text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助命令"""
    text = """📚 <b>命令列表</b>

<b>👤 用户命令：</b>
/sign - 每日签到
/points - 查看积分
/rank - 活跃度排行
/luck - 今日运势
/dice - 掷骰子

<b>👮 管理员命令：</b>
/ban @用户 时间 原因 - 禁言用户
/unban @用户 - 解除禁言
/kick @用户 - 踢出用户
/warn @用户 原因 - 警告用户
/setwelcome 文本 - 设置欢迎语
/addword 关键词 - 添加屏蔽词
/wordlist - 查看屏蔽词
/settings - 群设置
/stats - 群统计
/clean 数量 - 清理消息
    """
    await update.message.reply_text(text, parse_mode='HTML')

async def sign_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """签到命令"""
    user = update.effective_user
    user_data = db.get_user(user.id)
    
    today = datetime.now().date().isoformat()
    
    # 检查今天是否已签到
    if user_data and user_data[4] == today:  # sign_date 是第5列 (索引4)
        await update.message.reply_text("❌ 你今天已经签到过了，明天再来吧！")
        return
    
    # 计算连续签到
    streak = user_data[5] if user_data else 0  # sign_streak 是第6列 (索引5)
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    
    if user_data and user_data[4] == yesterday:
        streak += 1
    else:
        streak = 1
    
    # 基础积分 + 连续签到奖励
    points = CONFIG.get('points', {}).get('sign_daily', 10)
    bonus_days = CONFIG.get('points', {}).get('sign_bonus', [7, 30, 100, 365])
    
    if streak in bonus_days:
        bonus = points * 2
        points += bonus
        bonus_text = f"🎁 连续签到 {streak} 天，额外奖励 {bonus} 积分！"
    else:
        bonus_text = f"🔥 连续签到 {streak} 天"
    
    # 更新用户数据
    if user_data:
        db.update_user(user.id, sign_date=today, sign_streak=streak)
        db.add_points(user.id, points)
    else:
        db.add_user(user.id, user.username, user.first_name)
        db.update_user(user.id, sign_date=today, sign_streak=streak)
        db.add_points(user.id, points)
    
    # 获取更新后的积分
    updated_user = db.get_user(user.id)
    total_points = updated_user[3] if updated_user else points
    
    text = f"""✅ 签到成功！

🎉 获得 {points} 积分
{bonus_text}
💰 总积分：{total_points}
    """
    await update.message.reply_text(text)

async def points_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看积分"""
    user = update.effective_user
    user_data = db.get_user(user.id)
    
    if not user_data:
        await update.message.reply_text("❌ 你还没有积分记录，快使用 /sign 签到吧！")
        return
    
    text = f"""💰 <b>我的积分</b>

👤 用户：{user.first_name}
⭐ 积分：{user_data[3]}
📨 消息：{user_data[6]} 条
🔥 连续签到：{user_data[5]} 天
    """
    await update.message.reply_text(text, parse_mode='HTML')

async def rank_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """排行榜"""
    top_users = db.get_top_users(10)
    
    if not top_users:
        await update.message.reply_text("📊 暂无排行数据")
        return
    
    text = "🏆 <b>活跃度排行榜</b>\n\n"
    medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
    
    for i, user in enumerate(top_users):
        name = user[2] or user[1] or f"用户{user[0]}"
        text += f"{medals[i] if i < len(medals) else '•'} {name} - {user[3]} 积分 ({user[4]} 消息)\n"
    
    await update.message.reply_text(text, parse_mode='HTML')

async def luck_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """今日运势"""
    user = update.effective_user
    fortunes = [
        ("大吉", "🌟", "今天运气爆棚，做什么都顺利！"),
        ("中吉", "✨", "运势不错，把握机会！"),
        ("小吉", "🍀", "小有收获，保持平常心。"),
        ("平", "😐", "平平淡淡才是真。"),
        ("小凶", "⚠️", "小心行事，避免冲动。"),
        ("凶", "💀", "今天不太顺利，低调为好。")
    ]
    
    # 根据用户ID和日期生成固定运势
    today = datetime.now().date().isoformat()
    seed = hash(f"{user.id}{today}") % len(fortunes)
    fortune = fortunes[seed]
    
    lucky_num = (user.id + hash(today)) % 100
    colors = ["红色", "蓝色", "绿色", "黄色", "紫色", "粉色", "橙色"]
    lucky_color = colors[seed % len(colors)]
    
    text = f"""🔮 <b>今日运势</b>

{fortune[1]} <b>{fortune[0]}</b>
📝 {fortune[2]}

💡 幸运数字：{lucky_num}
🎨 幸运颜色：{lucky_color}
    """
    await update.message.reply_text(text, parse_mode='HTML')

async def dice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """掷骰子"""
    user = update.effective_user
    user_data = db.get_user(user.id)
    
    # 检查积分
    current_points = user_data[3] if user_data else 0
    cost = CONFIG.get('games', {}).get('dice_cost', 5)
    
    if current_points < cost:
        await update.message.reply_text(f"❌ 积分不足！需要 {cost} 积分，你只有 {current_points} 积分")
        return
    
    dice_value = random.randint(1, 6)
    
    # 消耗积分
    db.add_points(user.id, -cost)
    
    # 奖励
    reward = 0
    reward_threshold = CONFIG.get('games', {}).get('dice_reward_threshold', 5)
    if dice_value >= reward_threshold:
        reward = CONFIG.get('games', {}).get('dice_reward', 10)
        db.add_points(user.id, reward)
        result_text = f"🎉 恭喜！获得 {reward} 积分！"
    else:
        result_text = "😅 再接再厉！"
    
    dice_emojis = ['⚀', '⚁', '⚂', '⚃', '⚄', '⚅']
    
    text = f"""🎲 <b>掷骰子</b>

{dice_emojis[dice_value-1]} 点数：{dice_value}
💰 消耗：{cost} 积分
{result_text}
    """
    await update.message.reply_text(text, parse_mode='HTML')

# ============ 管理员命令 ============

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """禁言用户"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 你没有权限使用此命令")
        return
    
    if not update.message.reply_to_message and not context.args:
        await update.message.reply_text("用法：/ban @用户名 时间(分钟) 原因\n或回复用户消息：/ban 时间 原因")
        return
    
    try:
        # 获取目标用户
        if update.message.reply_to_message:
            target_user = update.message.reply_to_message.from_user
            duration = int(context.args[0]) if context.args else 60
            reason = ' '.join(context.args[1:]) if len(context.args) > 1 else "违反群规"
        else:
            await update.message.reply_text("请回复用户消息来禁言")
            return
        
        # 执行禁言 - 使用 ChatPermissions
        until_date = datetime.now() + timedelta(minutes=duration)
        permissions = ChatPermissions(can_send_messages=False)
        
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target_user.id,
            permissions=permissions,
            until_date=until_date
        )
        
        text = f"""🚫 <b>用户被禁言</b>

👤 用户：{target_user.first_name}
⏱ 时长：{format_time(duration * 60)}
📝 原因：{reason}
👮 操作者：{update.effective_user.first_name}
        """
        await update.message.reply_text(text, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Ban error: {e}")
        await update.message.reply_text(f"❌ 操作失败：{str(e)}")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """解除禁言"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 你没有权限使用此命令")
        return
    
    if not update.message.reply_to_message:
        await update.message.reply_text("请回复用户消息来解除禁言")
        return
    
    target_user = update.message.reply_to_message.from_user
    
    try:
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )
        
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target_user.id,
            permissions=permissions
        )
        
        await update.message.reply_text(f"✅ {target_user.first_name} 已解除禁言")
    except Exception as e:
        logger.error(f"Unban error: {e}")
        await update.message.reply_text(f"❌ 操作失败：{str(e)}")

async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """踢出用户"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 你没有权限使用此命令")
        return
    
    if not update.message.reply_to_message:
        await update.message.reply_text("请回复用户消息来踢出")
        return
    
    target_user = update.message.reply_to_message.from_user
    
    try:
        await context.bot.ban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target_user.id
        )
        await context.bot.unban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target_user.id
        )
        
        await update.message.reply_text(f"👢 {target_user.first_name} 已被踢出群组")
    except Exception as e:
        logger.error(f"Kick error: {e}")
        await update.message.reply_text(f"❌ 操作失败：{str(e)}")

async def clean_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清理消息"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 你没有权限使用此命令")
        return
    
    try:
        count = int(context.args[0]) if context.args else 10
        count = min(count, 100)  # 最多清理100条
        
        message_id = update.message.message_id
        deleted = 0
        
        for i in range(1, count + 1):
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=message_id - i
                )
                deleted += 1
            except Exception as e:
                logger.debug(f"Delete message failed: {e}")
                continue
        
        msg = await update.message.reply_text(f"✅ 已清理 {deleted} 条消息")
        await asyncio.sleep(5)
        try:
            await msg.delete()
            await update.message.delete()
        except:
            pass
        
    except Exception as e:
        logger.error(f"Clean error: {e}")
        await update.message.reply_text(f"❌ 清理失败：{str(e)}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """群统计"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 你没有权限使用此命令")
        return
    
    chat = update.effective_chat
    
    # 获取群成员数
    try:
        member_count = await context.bot.get_chat_member_count(chat.id)
    except Exception as e:
        member_count = "未知"
        logger.error(f"Get member count error: {e}")
    
    text = f"""📊 <b>群组统计</b>

🏠 群组：{chat.title}
👥 成员数：{member_count}
🆔 ID：{chat.id}
    """
    await update.message.reply_text(text, parse_mode='HTML')

async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """警告用户"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ 你没有权限使用此命令")
        return
    
    if not update.message.reply_to_message:
        await update.message.reply_text("请回复用户消息来警告")
        return
    
    target_user = update.message.reply_to_message.from_user
    reason = ' '.join(context.args) if context.args else "违反群规"
    
    # 增加警告次数
    user_data = db.get_user(target_user.id)
    warnings = (user_data[8] if user_data else 0) + 1  # warnings 是第9列 (索引8)
    
    db.update_user(target_user.id, warnings=warnings)
    
    text = f"""⚠️ <b>用户被警告</b>

👤 用户：{target_user.first_name}
📝 原因：{reason}
⚠️ 警告次数：{warnings}/3
👮 操作者：{update.effective_user.first_name}

{"🚫 该用户已被禁言（警告达到3次）" if warnings >= 3 else "💡 再警告 {} 次将被禁言".format(3 - warnings)}
    """
    
    await update.message.reply_text(text, parse_mode='HTML')
    
    # 如果警告达到3次，自动禁言
    if warnings >= 3:
        try:
            permissions = ChatPermissions(can_send_messages=False)
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=target_user.id,
                permissions=permissions,
                until_date=datetime.now() + timedelta(hours=1)
            )
        except Exception as e:
            logger.error(f"Auto ban error: {e}")

# ============ 消息处理器 ============

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """欢迎新成员"""
    if not update.message.new_chat_members:
        return
    
    for new_member in update.message.new_chat_members:
        if new_member.id == context.bot.id:
            continue
        
        # 添加到数据库
        db.add_user(new_member.id, new_member.username, new_member.first_name)
        
        # 发送欢迎消息
        welcome_text = CONFIG.get('welcome_message', '欢迎 {name}！').format(
            name=new_member.first_name,
            username=new_member.username or "",
            id=new_member.id
        )
        
        await update.message.reply_text(welcome_text)
        
        # 删除服务消息
        if CONFIG.get('auto_delete', {}).get('service_msg', False):
            await asyncio.sleep(CONFIG.get('auto_delete', {}).get('delay_seconds', 60))
            try:
                await update.message.delete()
            except:
                pass

async def check_advertisement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """检查广告"""
    if not is_group_chat(update.effective_chat):
        return
    
    if not CONFIG.get('anti_ad', {}).get('enabled', False):
        return
    
    message = update.message
    if not message:
        return
        
    user = message.from_user
    if not user:
        return
        
    text = message.text or message.caption or ""
    
    # 检查是否是管理员
    if is_admin(user.id):
        # 记录正常消息
        if text:
            db.log_message(update.effective_chat.id, user.id, text)
        return
    
    # 检查新用户限制
    user_data = db.get_user(user.id)
    if user_data and user_data[7]:  # join_date 是第8列 (索引7)
        try:
            join_time = datetime.fromisoformat(user_data[7])
            restrict_minutes = CONFIG.get('anti_ad', {}).get('new_user_restrict', 30)
            if datetime.now() - join_time < timedelta(minutes=restrict_minutes):
                # 新用户检查链接
                if 'http' in text.lower() or 't.me/' in text.lower():
                    await message.delete()
                    warning_msg = await message.reply_text(
                        f"🚫 {user.first_name}，新用户 {restrict_minutes} 分钟内不能发送链接！"
                    )
                    await asyncio.sleep(10)
                    try:
                        await warning_msg.delete()
                    except:
                        pass
                    return
        except Exception as e:
            logger.error(f"New user check error: {e}")
    
    # 检查关键词
    for keyword in CONFIG.get('anti_ad', {}).get('keywords', []):
        if keyword in text.lower():
            try:
                await message.delete()
                warning_msg = await message.reply_text(f"🚫 {user.first_name}，检测到违规关键词！")
                db.log_violation(update.effective_chat.id, user.id, 'keyword', text, 'delete')
                await asyncio.sleep(10)
                try:
                    await warning_msg.delete()
                except:
                    pass
                return
            except Exception as e:
                logger.error(f"Delete keyword violation error: {e}")
    
    # 检查正则表达式
    for pattern in CONFIG.get('anti_ad', {}).get('regex_patterns', []):
        try:
            if re.search(pattern, text, re.IGNORECASE):
                await message.delete()
                warning_msg = await message.reply_text(f"🚫 {user.first_name}，检测到违规内容！")
                db.log_violation(update.effective_chat.id, user.id, 'regex', text, 'delete')
                await asyncio.sleep(10)
                try:
                    await warning_msg.delete()
                except:
                    pass
                return
        except re.error as e:
            logger.error(f"Regex error: {e}")
            continue
    
    # 检查链接
    if CONFIG.get('anti_ad', {}).get('ban_links', False):
        if 'http' in text.lower() or 'www.' in text.lower():
            # 检查白名单
            whitelist = CONFIG.get('anti_ad', {}).get('whitelist_links', [])
            is_whitelisted = any(wl in text for wl in whitelist)
            if not is_whitelisted:
                try:
                    await message.delete()
                    warning_msg = await message.reply_text(f"🚫 {user.first_name}，禁止发送外部链接！")
                    db.log_violation(update.effective_chat.id, user.id, 'link', text, 'delete')
                    await asyncio.sleep(10)
                    try:
                        await warning_msg.delete()
                    except:
                        pass
                    return
                except Exception as e:
                    logger.error(f"Delete link error: {e}")
    
    # 记录正常消息
    if text:
        db.log_message(update.effective_chat.id, user.id, text)

async def handle_left_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理成员离开"""
    if CONFIG.get('auto_delete', {}).get('leave_msg', False):
        await asyncio.sleep(5)
        try:
            await update.message.delete()
        except:
            pass

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """错误处理器"""
    logger.error(f"Update {update} caused error {context.error}")
    
    # 只在群组中发送错误提示
    if update and update.effective_chat and is_group_chat(update.effective_chat):
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ 操作出现错误，请检查日志"
            )
        except:
            pass

# ============ 主程序 ============

def main():
    # 检查配置
    if CONFIG.get('bot_token') == 'YOUR_BOT_TOKEN_HERE':
        print("❌ 错误：请在 config.json 中设置你的 Bot Token")
        exit(1)
    
    # 创建应用
    application = Application.builder().token(CONFIG['bot_token']).build()
    
    # 添加命令处理器
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("sign", sign_command))
    application.add_handler(CommandHandler("points", points_command))
    application.add_handler(CommandHandler("rank", rank_command))
    application.add_handler(CommandHandler("luck", luck_command))
    application.add_handler(CommandHandler("dice", dice_command))
    
    # 管理员命令
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("kick", kick_command))
    application.add_handler(CommandHandler("clean", clean_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("warn", warn_command))
    
    # 消息处理器
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    application.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, handle_left_member))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_advertisement))
    
    # 错误处理器
    application.add_error_handler(error_handler)
    
    # 启动机器人
    print("🤖 超级群管机器人启动中...")
    print("📖 使用 Ctrl+C 停止")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
