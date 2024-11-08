import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import asyncio
import pymongo
from pymongo import MongoClient
from datetime import datetime
from threading import Thread
from pymongo.server_api import ServerApi
import certifi
from keep_alive import keep_alive


load_dotenv()
token = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
CHANNEL_ID = os.getenv("CHANNEL_ID")
bot = commands.Bot(command_prefix='!', intents=discord.Intents.all())



try:
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        raise ValueError("MONGODB_URI not set")
    
    # Create a new client and connect to the server with SSL configuration
    client = MongoClient(
        mongodb_uri,
        server_api=ServerApi('1'),
        tls=True,
        tlsCAFile=certifi.where(),
        retryWrites=True,
        w='majority's
    )
        
    # Test the connection
    client.admin.command('ping')
    print("Connected successfully to MongoDB!")
    
    db = client['nathan']
    users_collection = db['users']
except Exception as e:
    print(f"Failed to connect to MongoDB: {e}")
    raise

def save_points():
    for user_id, data in user_data.items():
        # Convertir les datetime en chaînes ISO
        serializable_data = data.copy()
        if "scarlanat_timers" in serializable_data:
            serializable_data["scarlanat_timers"] = {
                k: v.isoformat() if isinstance(v, datetime) else v 
                for k, v in serializable_data["scarlanat_timers"].items()
            }
        
        users_collection.update_one(
            {'_id': user_id},
            {'$set': serializable_data},
            upsert=True
        )

def load_points():
    global user_data
    try:
        user_data = {doc['_id']: doc for doc in users_collection.find()}
        
        # Reconvertir les timers en datetime
        for user_id in user_data:
            if "scarlanat_timers" in user_data[user_id]:
                user_data[user_id]["scarlanat_timers"] = {
                    k: datetime.fromisoformat(v) if isinstance(v, str) else v
                    for k, v in user_data[user_id]["scarlanat_timers"].items()
                }
    
    except pymongo.errors.ServerSelectionTimeoutError as e:
        print(f"Failed to connect to MongoDB: {e}")
        user_data = {}
    except Exception as e:
        print(f"An error occurred while loading points: {e}")
        user_data = {}


@bot.event
async def on_ready():
    print(f'{bot.user} est connecté !')
    load_points()
    start_timer.start()
    keep_alive()

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
    keep_alive()

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
async def check(ctx, *usernames: str):
    # Vérification de l'heure
    current_time = datetime.now().time()
    if current_time.hour >= 13:
        await ctx.send("❌ Vous ne pouvez plus gagner de points aujourd'hui, la limite de 13h est passée.")
        return

    user_id = str(ctx.author.id)
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Initialisation des données utilisateur si nécessaire
    if user_id not in user_data:
        user_data[user_id] = {
            "points": 0, 
            "last_claim": None, 
            "scarlanat_timers": {}
        }

    # Vérifier et ajouter les timers pour chaque utilisateur mentionné
    checked_users = []
    check_details = {}

    for username in usernames:
        user_to_check = discord.utils.get(ctx.guild.members, name=username)
        
        if user_to_check is None:
            await ctx.send(f"Utilisateur @{username} non trouvé dans le serveur.")
            continue

        user_to_check_id = str(user_to_check.id)
        
        # Initialiser les données de l'utilisateur vérifié
        if user_to_check_id not in user_data:
            user_data[user_to_check_id] = {
                "points": 0, 
                "last_claim": None, 
                "scarlanat_timers": {}
            }

        # Ajouter un timer spécifique pour cet utilisateur
        timer = datetime.now() + timedelta(minutes=30)
        user_data[user_id]["scarlanat_timers"][user_to_check_id] = timer
        
        # Stocker les détails du check
        check_details[user_to_check_id] = {
            "username": username,
            "timer": timer
        }
        
        checked_users.append(user_to_check)

    # Message récapitulatif
    if checked_users:
        users_mention = " ".join([f"<@{user.id}>" for user in checked_users])
        await ctx.send(f"Un compteur de 30 minutes a été lancé pour {users_mention}. S'ils répondent, ils gagneront 150 points. Sinon, <@{ctx.author.id}> gagnera 150 points!")
        save_points()

    # Fonction de vérification des réponses
    def check_response(response):
        return (
            response.author.id in [int(user_id) for user_id in check_details.keys()] and 
            response.channel == ctx.channel
        )

    # Attente des réponses
    try:
        response = await bot.wait_for("message", timeout=1800, check=check_response)
        
        # Identifier l'utilisateur qui a répondu
        responder_id = str(response.author.id)
        
        # Donner les points à l'utilisateur qui a répondu
        user_data[responder_id]["points"] += 100
        user_data[responder_id]["last_claim"] = today
        
        # Supprimer les timers
        if user_id in user_data:
            user_data[user_id]["scarlanat_timers"].clear()
        
        save_points()
        
        await ctx.send(f"🎉 <@{response.author.id}> a répondu ! Il gagne 100 points ! Total: {user_data[responder_id]['points']} points")

    except asyncio.TimeoutError:
        # Si personne ne répond, donner les points à l'initiateur
        user_data[user_id]["points"] += 70
        user_data[user_id]["last_claim"] = today
        
        # Supprimer les timers
        user_data[user_id]["scarlanat_timers"].clear()
        
        save_points()
        
        await ctx.send(f"🎉 <@{ctx.author.id}> a gagné 70 points car personne n'a répondu ! Total: {user_data[user_id]['points']} points")


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

bot.run(token)