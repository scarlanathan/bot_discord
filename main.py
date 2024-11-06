import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from keep_alive import keep_alive
import asyncio
import pymongo
from pymongo import MongoClient
from datetime import datetime
import ssl

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("1299012900165255210") 
CHANNEL_ID = os.getenv("C1299496069985599498")
bot = commands.Bot(command_prefix='!', intents=discord.Intents.all())


user_data = {}

mongodb_uri = "mongodb+srv://nathan:WHzqnSuWw5kI0QyH@botdiscord.ew8se.mongodb.net/"
client = MongoClient(mongodb_uri)
db = client['nathan']  # Remplacez par le nom de votre base de données
users_collection = db['users']  # Collection pour stocker les données des utilisateurs
client = MongoClient(mongodb_uri, ssl=True, ssl_cert_reqs=ssl.CERT_NONE)

def save_points():
    for user_id, data in user_data.items():
        users_collection.update_one(
            {'_id': user_id},
            {'$set': data},
            upsert=True
        )

def load_points():
    global user_data
    user_data = {doc['_id']: doc for doc in users_collection.find()}

# Assurez-vous d'appeler load_points() au démarrage du bot
@bot.event
async def on_ready():
    print(f'{bot.user} est connecté !')
    load_points()
    start_timer.start()

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

@bot.event
async def on_ready():
    print(f'{bot.user} est connecté !')
    load_points()
    start_timer.start()

@tasks.loop(seconds=10)
async def start_timer():
    for user_id, data in user_data.items():
        if "scarlanat_timer" in data and data["scarlanat_timer"] is not None:
            time_left = data["scarlanat_timer"] - datetime.now()
            if time_left.total_seconds() > 0:
                guild = bot.get_guild(GUILD_ID)
                if guild:
                    member = await guild.get_member(int(user_id))
                    if member:
                        await member.send(f"⏱️ Vous avez encore {round(time_left.total_seconds())} secondes avant de pouvoir utiliser à nouveau la commande 'scarlanat check'.")
                else:
                    channel = bot.get_channel(CHANNEL_ID)
                    if channel:
                        await channel.send(f"⚠️ Impossible de contacter l'utilisateur {user_id} pour le timer 'scarlanat check'.")

@bot.command(name="wassimchien")
async def wassimchien(ctx):
    user_id = str(ctx.author.id)
    if user_id not in user_data:
        user_data[user_id] = {
            "points": 0,
            "last_claim": None
        }
    
    aujourd_hui = datetime.now().strftime("%Y-%m-%d")
    derniere_fois = user_data[user_id]["last_claim"]
    
    if derniere_fois != aujourd_hui:
        user_data[user_id]["points"] += 100
        user_data[user_id]["last_claim"] = aujourd_hui
        save_points()
        await ctx.send(f"🎉 Vous avez gagné 100 points ! Total: {user_data[user_id]['points']} points")
    else:
        await ctx.send("❌ Vous avez déjà gagné vos points aujourd'hui !")

@bot.command(name="check")
async def check(ctx, username: str):
    user_to_check = discord.utils.get(ctx.guild.members, name=username)
    
    if user_to_check is None:
        await ctx.send(f"Utilisateur @{username} non trouvé dans le serveur.")
        return

    current_time = datetime.now().time()
    
    if current_time.hour >= 13:
        await ctx.send("❌ Vous ne pouvez plus gagner de points aujourd'hui, la limite de 13h est passée.")
        return

    user_id = str(ctx.author.id)
    today = datetime.now().strftime("%Y-%m-%d")
    
    if user_id not in user_data:
        user_data[user_id] = {
            "points": 0,
            "last_claim": None,
            "scarlanat_timer": None
        }

    await ctx.send(f"Un compteur de 30 minutes a été lancé. Si <@{user_to_check.id}> ne répond pas, vous gagnerez 150 points!")

    user_data[user_id]["scarlanat_timer"] = datetime.now() + timedelta(minutes=30)
    save_points()

    def check_response(response):
        return response.author.id == user_to_check.id and response.channel == ctx.channel
    
    try:
        await bot.wait_for("message", timeout=1800, check=check_response)
        user_data[user_id]["points"] += 70
        save_points()
        await ctx.send(f"🎉 @{username} a répondu! Vous gagnez 70 points! Total: {user_data[user_id]['points']} points")
    except asyncio.TimeoutError:
        user_data[user_id]["points"] += 150
        user_data[user_id]["last_claim"] = today
        save_points()
        await ctx.send(f"🎉 Vous avez gagné 150 points car @{username} n'a pas répondu! Total: {user_data[user_id]['points']} points")


@bot.command(name="points")
async def points(ctx):
    """Affiche vos points actuels"""
    user_id = str(ctx.author.id)
    if user_id in user_data:
        points = user_data[user_id]["points"]
        await ctx.send(f"💰 Vous avez {points} points !")
    else:
        await ctx.send("Vous n'avez pas encore de points !")

@bot.command(name="classement")
async def classement(ctx):
    """Affiche le classement des 10 meilleurs joueurs"""
    classement = []
    for id_joueur, data in user_data.items():
        try:
            membre = await ctx.guild.fetch_member(int(id_joueur))
            if membre:
                classement.append({
                    "nom": membre.display_name,
                    "points": data["points"]
                })
        except discord.NotFound:
            continue

    classement_trie = sorted(classement, key=lambda x: x["points"], reverse=True)

    embed = discord.Embed(
        title="🏆 Classement des Points",
        description="Voici les meilleurs joueurs !",
        color=0xFFD700
    )

    for i, joueur in enumerate(classement_trie[:10], 1):
        if i == 1:
            position = "🥇"
        elif i == 2:
            position = "🥈"
        elif i == 3:
            position = "🥉"
        else:
            position = f"{i}."

        embed.add_field(
            name=f"{position} {joueur['nom']}",
            value=f"{joueur['points']} points",
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command(name="bot-help")
async def bot_help(ctx):
    """Affiche l'aide du bot"""
    embed = discord.Embed(
        title="📖 Aide du Bot",
        description="Voici les commandes disponibles :",
        color=0x3498db
    )
    
    embed.add_field(
        name="scarlanat check",
        value="Lancez un compteur de 30 minutes. Si @scarlanat répond, vous gagnez 70 points. Sinon, vous gagnez 150 points.",
        inline=False
    )
    
    embed.add_field(
        name="!points",
        value="Voir votre nombre de points actuel",
        inline=False
    )
    
    embed.add_field(
        name="!classement",
        value="Voir le top 10 des joueurs",
        inline=False
    )
    
    embed.add_field(
        name="!bot-help",
        value="Afficher ce message d'aide",
        inline=False
    )

    await ctx.send(embed=embed)

keep_alive()
bot.run(token)