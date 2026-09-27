"""Professional server systems for Ader. Economy is intentionally untouched."""
from __future__ import annotations
import json
import random
import re
import time
import unicodedata
from collections import defaultdict, deque
from datetime import timedelta
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import EmbedFactory, EmbedColor
from utils.permissions import is_admin, is_moderator

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", s)
    s = s.translate(str.maketrans({"0":"o","1":"i","3":"e","4":"a","5":"s","7":"t","@":"a","$":"s"}))
    return re.sub(r"[._|/\\\-+=~*^\x60]+", "", s)

def bool_value(v: str):
    return {"on":True,"off":False,"enable":True,"disable":False,"enabled":True,"disabled":False}.get(v.lower())

class ProfessionalCore(commands.Cog):
    def __init__(self, bot, db, config):
        self.bot, self.db, self.config = bot, db, config
        self.cache = {}
        self.spam = defaultdict(deque)
        self.xp_cd = {}
        self.nuke = defaultdict(deque)

    async def cog_load(self):
        await self.db.execute("CREATE TABLE IF NOT EXISTS ader_core_settings(guild_id INTEGER PRIMARY KEY,data TEXT NOT NULL DEFAULT '{}',updated_at REAL NOT NULL DEFAULT 0)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS ader_mod_cases(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,moderator_id INTEGER NOT NULL,action TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL)")

    async def settings(self, gid):
        if gid in self.cache: return self.cache[gid]
        row = await self.db.fetchone("SELECT data FROM ader_core_settings WHERE guild_id=?", (gid,))
        data = {}
        if row:
            try: data = json.loads(row["data"] or "{}")
            except Exception: data = {}
        d = {"logs":{"channel":None},"automod":{"enabled":True,"spam":True,"links":False,"mentions":5,"messages":6,"window":5,"timeout":5,"words":[]},"autorole":{"enabled":False,"role":None},"levels":{"enabled":True,"min":8,"max":14,"cooldown":45,"base":100},"antinuke":{"enabled":True,"threshold":4,"window":10,"action":"timeout"}}
        def merge(a,b):
            for k,v in b.items():
                if isinstance(v,dict) and isinstance(a.get(k),dict): merge(a[k],v)
                else: a[k]=v
        merge(d,data); self.cache[gid]=d; return d

    async def save(self,gid,data):
        self.cache[gid]=data
        await self.db.execute("INSERT INTO ader_core_settings(guild_id,data,updated_at) VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",(gid,json.dumps(data,ensure_ascii=False),time.time()))

    async def log(self,guild,title,text,color=discord.Color.blurple()):
        c=(await self.settings(guild.id))["logs"]["channel"]
        channel=guild.get_channel(int(c)) if c else None
        if not channel: return
        try:
            await channel.send(embed=discord.Embed(title="Ader • "+title,description=text[:4000],color=color,timestamp=discord.utils.utcnow()),allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden,discord.HTTPException): pass

    async def case(self,guild,user,mod,action,reason):
        await self.db.execute("INSERT INTO ader_mod_cases(guild_id,user_id,moderator_id,action,reason,created_at) VALUES(?,?,?,?,?,?)",(guild.id,user,mod,action,reason[:1000],time.time()))

    async def automod(self,m):
        s=(await self.settings(m.guild.id))["automod"]
        if not s["enabled"] or m.author.guild_permissions.manage_messages: return False
        text=norm(m.content)
        if any(norm(w) and norm(w) in text for w in s["words"]):
            try: await m.delete()
            except discord.HTTPException: pass
            await self.case(m.guild,m.author.id,self.bot.user.id,"automod-word","كلمة محظورة")
            try: await m.author.timeout(timedelta(minutes=max(1,int(s["timeout"]))),reason="Ader AutoMod")
            except discord.HTTPException: pass
            await self.log(m.guild,"AutoMod",f"تم حذف رسالة من {m.author.mention} بسبب كلمة محظورة.",discord.Color.orange())
            return True
        if s["links"] and re.search(r"(?:https?://|www\.)\S+",m.content,re.I):
            try: await m.delete()
            except discord.HTTPException: pass
            return True
        return False

    async def add_xp(self,m):
        s=(await self.settings(m.guild.id))["levels"]
        if not s["enabled"]: return
        key=(m.guild.id,m.author.id); now=time.time()
        if now-self.xp_cd.get(key,0)<max(5,int(s["cooldown"])): return
        self.xp_cd[key]=now
        row=await self.db.fetchone("SELECT xp,level FROM users WHERE user_id=? AND guild_id=?",(m.author.id,m.guild.id))
        if not row:
            await self.db.create_user(m.author.id,m.guild.id)
            row=await self.db.fetchone("SELECT xp,level FROM users WHERE user_id=? AND guild_id=?",(m.author.id,m.guild.id))
        xp=int(row["xp"])+random.randint(int(s["min"]),int(s["max"])); old=int(row["level"]); base=max(10,int(s["base"])); level=old
        while xp>=base*(level+1): level+=1
        await self.db.execute("UPDATE users SET xp=?,level=? WHERE user_id=? AND guild_id=?",(xp,level,m.author.id,m.guild.id))
        if level>old:
            await m.channel.send(f"🎉 {m.author.mention} وصل للمستوى {level}!",delete_after=8,allowed_mentions=discord.AllowedMentions(users=True))

    @commands.Cog.listener()
    async def on_message(self,m):
        if m.author.bot or not m.guild: return
        if await self.automod(m): return
        await self.add_xp(m)

    async def nuke_guard(self,guild,actor,action):
        s=(await self.settings(guild.id))["antinuke"]
        if not s["enabled"] or actor.bot or actor.id==guild.owner_id or actor.guild_permissions.administrator: return
        q=self.nuke[(guild.id,actor.id,action)]; now=time.time(); q.append(now)
        while q and now-q[0]>max(3,int(s["window"])): q.popleft()
        if len(q)<max(2,int(s["threshold"])): return
        q.clear()
        try:
            if s["action"]=="ban": await guild.ban(actor,reason="Ader Anti-Nuke",delete_message_seconds=0)
            else: await actor.timeout(timedelta(minutes=60),reason="Ader Anti-Nuke")
        except discord.HTTPException: pass
        await self.log(guild,"Anti-Nuke",f"تم تفعيل Anti-Nuke ضد {actor.mention} بسبب تكرار {action}.",discord.Color.red())

    @commands.Cog.listener()
    async def on_guild_channel_delete(self,ch):
        await self.audit_guard(ch.guild,discord.AuditLogAction.channel_delete,"channel_delete")
    @commands.Cog.listener()
    async def on_guild_role_delete(self,role):
        await self.audit_guard(role.guild,discord.AuditLogAction.role_delete,"role_delete")

    async def audit_guard(self,guild,action,name):
        try:
            entries=[e async for e in guild.audit_logs(limit=1,action=action)]
            if entries and isinstance(entries[0].user,discord.Member): await self.nuke_guard(guild,entries[0].user,name)
        except (discord.Forbidden,discord.HTTPException): pass

    @app_commands.command(name="core-settings",description="عرض حالة أنظمة Ader")
    @is_admin()
    async def core_settings(self,i):
        s=await self.settings(i.guild.id)
        await i.response.send_message(embed=EmbedFactory.create(title="Ader Core Systems",color=EmbedColor.INFO,fields=[{"name":"AutoMod","value":"مفعّل" if s["automod"]["enabled"] else "متوقف","inline":True},{"name":"Anti-Nuke","value":"مفعّل" if s["antinuke"]["enabled"] else "متوقف","inline":True},{"name":"Levels","value":"مفعّل" if s["levels"]["enabled"] else "متوقف","inline":True},{"name":"AutoRole","value":"مفعّل" if s["autorole"]["enabled"] else "متوقف","inline":True}]),ephemeral=True)

    @app_commands.command(name="automod-word",description="إضافة أو حذف كلمة محظورة")
    @app_commands.describe(action="add أو remove",word="الكلمة")
    @app_commands.choices(action=[app_commands.Choice(name="إضافة",value="add"),app_commands.Choice(name="حذف",value="remove")])
    @is_admin()
    async def automod_word(self,i,action:app_commands.Choice[str],word:str):
        s=await self.settings(i.guild.id); words=s["automod"]["words"]
        if action.value=="add" and not any(norm(x)==norm(word) for x in words): words.append(word[:100])
        elif action.value=="remove": words[:]=[x for x in words if norm(x)!=norm(word)]
        await self.save(i.guild.id,s); await i.response.send_message(f"تم تحديث الفلتر. الكلمات: {len(words)}.",ephemeral=True)

    @app_commands.command(name="automod-toggle",description="تشغيل أو إيقاف AutoMod")
    @is_admin()
    async def automod_toggle(self,i,enabled:str):
        v=bool_value(enabled)
        if v is None: return await i.response.send_message("استعمل on أو off.",ephemeral=True)
        s=await self.settings(i.guild.id); s["automod"]["enabled"]=v; await self.save(i.guild.id,s); await i.response.send_message("تم تحديث AutoMod.",ephemeral=True)

    @app_commands.command(name="set-log-channel",description="تعيين قناة السجلات")
    @is_admin()
    async def set_log_channel(self,i,channel:discord.TextChannel):
        s=await self.settings(i.guild.id); s["logs"]["channel"]=channel.id; await self.save(i.guild.id,s); await i.response.send_message(f"تم تعيين {channel.mention}.",ephemeral=True)

    @app_commands.command(name="set-autorole",description="تعيين الرتبة التلقائية")
    @is_admin()
    async def set_autorole(self,i,role:discord.Role,enabled:str="on"):
        v=bool_value(enabled)
        if v is None or (i.guild.me and role>=i.guild.me.top_role): return await i.response.send_message("إعداد غير صالح أو الرتبة أعلى من البوت.",ephemeral=True)
        s=await self.settings(i.guild.id); s["autorole"]={"enabled":v,"role":role.id}; await self.save(i.guild.id,s); await i.response.send_message("تم حفظ AutoRole.",ephemeral=True)

    @app_commands.command(name="level",description="عرض المستوى والXP")
    async def level(self,i,user:Optional[discord.Member]=None):
        m=user or i.user; r=await self.db.fetchone("SELECT xp,level FROM users WHERE user_id=? AND guild_id=?",(m.id,i.guild.id))
        xp=int(r["xp"]) if r else 0; lv=int(r["level"]) if r else 0; base=int((await self.settings(i.guild.id))["levels"]["base"])
        await i.response.send_message(f"{m.display_name} — المستوى {lv} — XP {xp:,}/{base*(lv+1):,}",ephemeral=True)

    @app_commands.command(name="mod-history",description="عرض سجل إجراءات عضو")
    @is_moderator()
    async def mod_history(self,i,user:discord.Member,limit:int=10):
        rows=await self.db.fetchall("SELECT id,moderator_id,action,reason,created_at FROM ader_mod_cases WHERE guild_id=? AND user_id=? ORDER BY created_at DESC LIMIT ?",(i.guild.id,user.id,max(1,min(25,limit))))
        if not rows: return await i.response.send_message("لا توجد سجلات.",ephemeral=True)
        lines=[f"#{r['id']} {r['action']} — <@{r['moderator_id']}> — {r['reason'] or 'بدون سبب'}" for r in rows]
        await i.response.send_message(embed=EmbedFactory.create(title=f"سجل {user.display_name}",description="\n".join(lines),color=EmbedColor.INFO),ephemeral=True)

    @app_commands.command(name="clear-mod-history",description="حذف سجل إجراءات عضو")
    @is_admin()
    async def clear_mod_history(self,i,user:discord.Member):
        await self.db.execute("DELETE FROM ader_mod_cases WHERE guild_id=? AND user_id=?",(i.guild.id,user.id)); await i.response.send_message("تم حذف السجل.",ephemeral=True)


async def setup(bot):
    await bot.add_cog(ProfessionalCore(bot,bot.db,bot.config))
