"""
Games Cog for Logiq
Button-based interactive mini-games for users
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import logging
import random

from utils.embeds import EmbedFactory, EmbedColor
from utils.permissions import is_admin
from database.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class DiceGameView(discord.ui.View):
    """Button-based dice game"""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="🎲 رمي النرد", style=discord.ButtonStyle.primary, custom_id="dice_roll")
    async def roll_dice(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Roll a dice"""
        result = random.randint(1, 6)

        embed = EmbedFactory.create(
            title="🎲 رمية النرد",
            description=f"قام {interaction.user.mention} برمي النرد وكانت النتيجة:\n\n# {result}",
            color=EmbedColor.INFO
        )

        await interaction.response.send_message(embed=embed)


class CoinFlipView(discord.ui.View):
    """Button-based coinflip game"""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="🪙 صورة", style=discord.ButtonStyle.success, custom_id="coin_heads")
    async def flip_heads(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Bet on heads"""
        await self._flip_coin(interaction, "heads")

    @discord.ui.button(label="🪙 كتابة", style=discord.ButtonStyle.danger, custom_id="coin_tails")
    async def flip_tails(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Bet on tails"""
        await self._flip_coin(interaction, "tails")

    async def _flip_coin(self, interaction: discord.Interaction, choice: str):
        """Flip coin logic"""
        result = random.choice(["heads", "tails"])
        won = result == choice

        if won:
            embed = EmbedFactory.success(
                "🎉 لقد فزت!",
                f"اختار {interaction.user.mention} **{choice}**، وكانت نتيجة رمي العملة **{result}**."
            )
        else:
            embed = EmbedFactory.error(
                "لم تفز هذه المرة!",
                f"اختار {interaction.user.mention} **{choice}**، لكن نتيجة رمي العملة كانت **{result}**."
            )

        await interaction.response.send_message(embed=embed)


class TriviaView(discord.ui.View):
    """Button-based trivia game"""

    def __init__(self, cog, question_data):
        super().__init__(timeout=30)
        self.cog = cog
        self.question_data = question_data
        self.answered = False

        # Create buttons for each option
        for i, option in enumerate(question_data['options']):
            button = discord.ui.Button(
                label=option,
                style=discord.ButtonStyle.primary,
                custom_id=f"trivia_{i}"
            )
            button.callback = self._make_callback(i)
            self.add_item(button)

    def _make_callback(self, option_index):
        async def callback(interaction: discord.Interaction):
            if self.answered:
                await interaction.response.send_message("تمت الإجابة عن هذا السؤال بالفعل.", ephemeral=True)
                return

            self.answered = True
            correct = option_index == self.question_data['answer']

            if correct:
                await self.cog.db.add_balance(interaction.user.id, interaction.guild.id, 50)
                embed = EmbedFactory.success(
                    "إجابة صحيحة! 🎉",
                    f"أجاب {interaction.user.mention} إجابة صحيحة!\nحصلت على **💎 50**!"
                )
            else:
                correct_answer = self.question_data['options'][self.question_data['answer']]
                embed = EmbedFactory.error(
                    "إجابة غير صحيحة! ❌",
                    f"الإجابة الصحيحة هي: **{correct_answer}**"
                )

            for child in self.children:
                child.disabled = True

            await interaction.response.edit_message(view=self)
            await interaction.followup.send(embed=embed)

        return callback


class EightBallView(discord.ui.View):
    """Button-based 8ball game"""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="🔮 اسأل كرة الحظ السحرية", style=discord.ButtonStyle.primary, custom_id="8ball_ask")
    async def ask_8ball(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Ask 8ball"""
        responses = [
            "نعم، بالتأكيد!", "الأمر مؤكد.", "بلا شك.",
            "على الأرجح.", "المؤشرات إيجابية.", "تشير الدلائل إلى نعم.",
            "الإجابة غير واضحة، حاول مجددًا.", "أعد السؤال لاحقًا.",
            "لا يمكن التنبؤ الآن.", "لا تعتمد على ذلك.",
            "إجابتي هي لا.", "المؤشرات ليست جيدة.", "هذا مستبعد جدًا."
        ]

        response = random.choice(responses)

        embed = EmbedFactory.create(
            title="🔮 كرة الحظ السحرية",
            description=f"طرح {interaction.user.mention} سؤالًا على كرة الحظ السحرية...\n\n**الإجابة:** {response}",
            color=EmbedColor.INFO
        )

        await interaction.response.send_message(embed=embed)


class TriviaStartView(discord.ui.View):
    """Button to start trivia"""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="🧠 ابدأ الأسئلة العامة", style=discord.ButtonStyle.success, custom_id="trivia_start")
    async def start_trivia(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Start a trivia game"""
        question_data = random.choice(self.cog.trivia_questions)

        embed = EmbedFactory.create(
            title="🎯 وقت الأسئلة العامة!",
            description=f"**{question_data['question']}**",
            color=EmbedColor.INFO
        )
        embed.set_footer(text="لديك 30 ثانية للإجابة! اربح 💎 50 عند الإجابة الصحيحة.")

        view = TriviaView(self.cog, question_data)
        await interaction.response.send_message(embed=embed, view=view)


class Games(commands.Cog):
    """Games and entertainment cog"""

    def __init__(self, bot: commands.Bot, db: DatabaseManager, config: dict):
        self.bot = bot
        self.db = db
        self.config = config
        self.module_config = config.get('modules', {}).get('games', {})
        self.trivia_questions = self._load_trivia()

    def _load_trivia(self):
        """Load trivia questions"""
        return [
            {
                "question": "في أي عام صدرت لغة Python لأول مرة؟",
                "options": ["1989", "1991", "1995", "2000"],
                "answer": 1
            },
            {
                "question": "What does CPU stand for?",
                "options": ["وحدة المعالجة المركزية", "وحدة الحاسوب الشخصية", "اتحاد المعالجة المركزي", "وحدة معالجة الحاسوب"],
                "answer": 0
            },
            {
                "question": "من أنشأ نظام Linux؟",
                "options": ["Bill Gates", "Linus Torvalds", "Steve Jobs", "Dennis Ritchie"],
                "answer": 1
            },
            {
                "question": "ما أكبر قيمة لعدد صحيح موقّع من 32 بت؟",
                "options": ["2,147,483,647", "4,294,967,295", "65,535", "2,147,483,648"],
                "answer": 0
            },
            {
                "question": "أي لغة برمجة تُعرف باسم أمّ اللغات؟",
                "options": ["C", "Assembly", "Fortran", "COBOL"],
                "answer": 0
            },
            {
                "question": "إلى ماذا يشير اختصار HTML؟",
                "options": ["لغة ترميز النص التشعبي", "لغة التقنية الحديثة المتقدمة", "لغة ترميز أدوات المنزل", "لغة ترميز الروابط التشعبية والنصوص"],
                "answer": 0
            },
            {
                "question": "من يُعرف بأبي الحاسوب؟",
                "options": ["Charles Babbage", "Alan Turing", "Bill Gates", "Steve Jobs"],
                "answer": 0
            },
            {
                "question": "في أي عام صدر أول هاتف iPhone؟",
                "options": ["2005", "2006", "2007", "2008"],
                "answer": 2
            },
            {
                "question": "إلى ماذا يشير اختصار RAM؟",
                "options": ["ذاكرة الوصول العشوائي", "ذاكرة الوصول للقراءة", "ذاكرة الوصول السريع", "ذاكرة الوصول أثناء التشغيل"],
                "answer": 0
            },
            {
                "question": "ما الشركة التي أنشأت JavaScript؟",
                "options": ["Microsoft", "Netscape", "Google", "Apple"],
                "answer": 1
            },
            {
                "question": "ما عاصمة فرنسا؟",
                "options": ["London", "Berlin", "Paris", "Madrid"],
                "answer": 2
            },
            {
                "question": "كم عدد القارات؟",
                "options": ["5", "6", "7", "8"],
                "answer": 2
            },
            {
                "question": "ما أكبر كوكب في مجموعتنا الشمسية؟",
                "options": ["Earth", "Mars", "Jupiter", "Saturn"],
                "answer": 2
            },
            {
                "question": "من رسم لوحة الموناليزا؟",
                "options": ["Vincent van Gogh", "Pablo Picasso", "Leonardo da Vinci", "Michelangelo"],
                "answer": 2
            },
            {
                "question": "ما سرعة الضوء؟",
                "options": ["299,792 km/s", "150,000 km/s", "500,000 km/s", "1,000,000 km/s"],
                "answer": 0
            }
        ]

    @app_commands.command(name="setup-game-panel", description="إعداد لوحة الألعاب بأزرار للمستخدمين (للمسؤولين)")
    @is_admin()
    async def setup_game_panel(self, interaction: discord.Interaction):
        """Setup game panel"""
        embed = EmbedFactory.create(
            title="🎮 مركز الألعاب",
            description="اضغط على الأزرار أدناه للعب!\n\n"
                       "🎲 **النرد** - ارمِ النرد\n"
                       "🪙 **رمي العملة** - اختر صورة أو كتابة\n"
                       "🧠 **الأسئلة العامة** - اختبر معلوماتك واربح 💎 50\n"
                       "🔮 **كرة الحظ السحرية** - اطرح سؤالًا",
            color=EmbedColor.INFO
        )

        # Create dice panel
        await interaction.channel.send(embed=embed)

        # Dice game
        dice_embed = EmbedFactory.create(title="🎲 لعبة النرد", description="اضغط لرمي النرد!", color=EmbedColor.INFO)
        await interaction.channel.send(embed=dice_embed, view=DiceGameView(self))

        # Coinflip game
        coin_embed = EmbedFactory.create(title="🪙 رمي العملة", description="اختر صورة أو كتابة!", color=EmbedColor.INFO)
        await interaction.channel.send(embed=coin_embed, view=CoinFlipView(self))

        # Trivia game
        trivia_embed = EmbedFactory.create(title="🧠 الأسئلة العامة", description="اختبر معلوماتك واربح 💎 50!", color=EmbedColor.SUCCESS)
        await interaction.channel.send(embed=trivia_embed, view=TriviaStartView(self))

        # 8-Ball
        ball_embed = EmbedFactory.create(title="🔮 كرة الحظ السحرية", description="اطرح سؤالًا على كرة الحظ السحرية!", color=EmbedColor.INFO)
        await interaction.channel.send(embed=ball_embed, view=EightBallView(self))

        await interaction.response.send_message(
            embed=EmbedFactory.success("تم إنشاء لوحة الألعاب", "يمكن للمستخدمين اللعب الآن بالضغط على الأزرار."),
            ephemeral=True
        )

        logger.info(f"Game panel created in {interaction.guild}")

    # Public commands for viewing stats
    @app_commands.command(name="rank", description="عرض بطاقة مستواك")
    @app_commands.describe(user="العضو المراد عرض مستواه (اختياري)")
    async def rank(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """View rank card - PUBLIC"""
        target = user or interaction.user

        user_data = await self.db.get_user(target.id, interaction.guild.id)
        if not user_data:
            user_data = await self.db.create_user(target.id, interaction.guild.id)

        leaderboard = await self.db.get_leaderboard(interaction.guild.id, limit=1000)
        rank = next((i + 1 for i, u in enumerate(leaderboard) if u['user_id'] == target.id), 0)

        from utils.constants import calculate_level_xp
        level = user_data.get('level', 0)
        xp = user_data.get('xp', 0)
        next_level_xp = calculate_level_xp(level + 1)

        embed = EmbedFactory.rank_card(target, level, xp, rank, next_level_xp)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="balance", description="عرض رصيدك")
    @app_commands.describe(user="العضو المراد عرض مستواه (اختياري)")
    async def balance(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """Check balance - PUBLIC"""
        target = user or interaction.user

        user_data = await self.db.get_user(target.id, interaction.guild.id)
        if not user_data:
            user_data = await self.db.create_user(target.id, interaction.guild.id)

        balance = user_data.get('balance', 0)
        embed = EmbedFactory.economy_balance(target, balance, "💎")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="عرض لوحة المتصدرين في الخادم")
    async def leaderboard(self, interaction: discord.Interaction):
        """View leaderboard - PUBLIC"""
        leaderboard = await self.db.get_leaderboard(interaction.guild.id, limit=10)

        if not leaderboard:
            await interaction.response.send_message(
                embed=EmbedFactory.info("لا توجد بيانات", "لا تتوفر بيانات للمتصدرين حاليًا."),
                ephemeral=True
            )
            return

        embed = EmbedFactory.leaderboard("لوحة متصدري النقاط", leaderboard, field_name="xp", color=EmbedColor.LEVELING)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    """Setup function for cog loading"""
    await bot.add_cog(Games(bot, bot.db, bot.config))
