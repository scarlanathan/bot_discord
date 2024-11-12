import discord # type: ignore
from discord.ext import commands, tasks # type: ignore
import json
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv # type: ignore
import asyncio
import pymongo # type: ignore
from pymongo import MongoClient # type: ignore
from datetime import datetime
import pytz
from threading import Thread
from pymongo.server_api import ServerApi # type: ignore
import certifi
from keep_alive import keep_alive


load_dotenv()
token = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
CHANNEL_ID = os.getenv("CHANNEL_ID")
bot = commands.Bot(command_prefix='!', intents=discord.Intents.all())

# Définir le fuseau horaire français
france_tz = pytz.timezone('Europe/Paris')

# Obtenir l'heure actuelle dans le fuseau horaire français
current_time = datetime.now(france_tz)

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
    
    # Initialisation complète des données utilisateur
    if user_id not in user_data:
        user_data[user_id] = {
            "points": 0,
            "last_claim": None,
            "streak": 0,  # Assurez-vous que cette clé existe
            "last_streak_date": None
        }
    
    # Vérifiez que toutes les clés nécessaires existent
    if "streak" not in user_data[user_id]:
        user_data[user_id]["streak"] = 0
    
    aujourd_hui = datetime.now().strftime("%Y-%m-%d")
    hier = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Logique de mise à jour du streak
    if user_data[user_id]["last_claim"] is None:
        # Première utilisation
        user_data[user_id]["streak"] = 1
    elif user_data[user_id]["last_claim"] == hier:
        # Streak continue
        user_data[user_id]["streak"] += 1
    elif user_data[user_id]["last_claim"] == aujourd_hui:
        # Déjà réclamé aujourd'hui
        await ctx.send("❌ Vous avez déjà gagné vos points aujourd'hui !")
        return
    else:
        # Streak interrompu
        user_data[user_id]["streak"] = 1
    
    # Calcul des points avec bonus de streak
    points_base = 100
    bonus_streak = min(user_data[user_id]["streak"] * 10, 100)  # Bonus max de 100 points
    points_totaux = points_base + bonus_streak
    
    # Mise à jour des données
    user_data[user_id]["points"] += points_totaux
    user_data[user_id]["last_claim"] = aujourd_hui
    
    save_points()
    
    # Message avec information de streak
    await ctx.send(f"🎉 Vous avez gagné {points_totaux} points (100 points de base + {bonus_streak} points de streak) !" 
                   f"\nVotre streak actuel est de {user_data[user_id]['streak']} jours !" 
                   f"\nTotal de points : {user_data[user_id]['points']} points")


@bot.command(name="check")
async def check(ctx, *mentions: discord.Member):
    # Utiliser le fuseau horaire français
    current_time = datetime.now(france_tz)
    
    # Convertir l'heure actuelle en minutes depuis minuit
    current_minutes = current_time.hour * 60 + current_time.minute
    
    # Convertir les bornes horaires en minutes
    start_time_minutes = 5 * 60 + 30  # 5h30 = 5 * 60 + 30
    end_time_minutes = 14 * 60  # 14h00 = 14 * 60
    
    # Vérifier si l'heure actuelle est en dehors de la plage autorisée
    if current_minutes < start_time_minutes or current_minutes >= end_time_minutes:
        await ctx.send(f"❌ Vous ne pouvez utiliser cette commande qu'entre 5h30 et 14h. Heure actuelle : {current_time.strftime('%H:%M')}")
        return

    # Le reste de votre code de commande reste identique
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
    checked_users = list(mentions)
    check_details = {}

    # Calcul des points pour celui qui tag
    points_to_add = len(checked_users) * 50
    user_data[user_id]["points"] += points_to_add

    # Retirer des points aux utilisateurs taggés
    for user_to_check in checked_users:
        user_to_check_id = str(user_to_check.id)
        
        # Initialiser les données de l'utilisateur vérifié
        if user_to_check_id not in user_data:
            user_data[user_to_check_id] = {
                "points": 0, 
                "last_claim": None, 
                "scarlanat_timers": {}
            }
        
        user_data[user_to_check_id]["points"] -= 50
        
        # Ajouter un timer spécifique pour cet utilisateur
        timer = datetime.now() + timedelta(minutes=30)
        user_data[user_id]["scarlanat_timers"][user_to_check_id] = timer
        
        # Stocker les détails du check
        check_details[user_to_check_id] = {
            "username": user_to_check.name,
            "timer": timer
        }

    # Message récapitulatif
    if checked_users:
        users_mention = " ".join([user.mention for user in checked_users])
        await ctx.send(f"Un compteur de 30 minutes a été lancé pour {users_mention}. " 
                       f"<@{ctx.author.id}> gagne {points_to_add} points et les utilisateurs taggés perdent 50 points chacun!")
        save_points()
        
    # Attente des réponses
    responded_users = set()
    try:
        while len(responded_users) < len(checked_users):
            response = await bot.wait_for(
                "message", 
                timeout=1800,  # 30 minutes
                check=lambda m: (
                    m.author.id in [user.id for user in checked_users] and 
                    m.channel == ctx.channel and
                    m.author.id not in [user.id for user in responded_users]
                )
            )
            
            # Identifier l'utilisateur qui a répondu
            responder_id = str(response.author.id)
            
            # Donner les points à l'utilisateur qui a répondu
            user_data[responder_id]["points"] += 100
            user_data[responder_id]["last_claim"] = today
            
            # Ajouter l'utilisateur aux répondants
            responded_users.add(response.author.id)
            
            # Message de confirmation
            await ctx.send(f"🎉 <@{response.author.id}> a répondu ! Il gagne 100 points ! Total: {user_data[responder_id]['points']} points")
        
        # Si tous les utilisateurs ont répondu
        await ctx.send("Tous les utilisateurs mentionnés ont répondu !")

    except asyncio.TimeoutError:
        # Liste des utilisateurs qui n'ont pas répondu
        non_responded = [user for user in checked_users if user.id not in responded_users]
        
        if non_responded:
            # Donner des points à l'initiateur
            user_data[user_id]["points"] += 70
            user_data[user_id]["last_claim"] = today
            
            # Message indiquant qui n'a pas répondu
            non_responded_mentions = " ".join([user.mention for user in non_responded])
            await ctx.send(f"🎉 <@{ctx.author.id}> a gagné 70 points car {non_responded_mentions} n'ont pas répondu ! Total: {user_data[user_id]['points']} points")
            
    save_points()
    
    
@bot.command(name="pompecheck")
async def pompecheck(ctx):
    # Récupérer tous les membres connectés du serveur (hors bots)
    connected_members = [member for member in ctx.guild.members if member.status != discord.Status.offline and not member.bot]
    
    # Vérifier s'il y a des membres connectés
    if not connected_members:
        await ctx.send("Aucun membre connecté à taguer !")
        return

    # Préparer la liste des membres à taguer
    users_mention = " ".join([member.mention for member in connected_members])
    
    # Message de défi
    await ctx.send(f"{users_mention} Faites-moi 10 pompes, gros porc de gros sac")


@bot.command(name="countercheck")
async def countercheck(ctx):
    user_id = str(ctx.author.id)
    
    # Vérifier si l'utilisateur a été précédemment check
    if user_id not in user_data or "scarlanat_timers" not in user_data[user_id]:
        await ctx.send("❌ Vous n'avez pas été check récemment !")
        return
    
    # Vérifier s'il y a des timers actifs pour cet utilisateur
    active_timers = user_data[user_id]["scarlanat_timers"]
    if not active_timers:
        await ctx.send("❌ Vous n'avez pas de check actif à contrer !")
        return
    
    # Trouver l'utilisateur qui a initié le check
    checkers = list(active_timers.keys())
    if not checkers:
        await ctx.send("❌ Impossible de trouver l'initiateur du check !")
        return
    
    # Prendre le premier checker (dans le cas où il y aurait plusieurs checks)
    checker_id = checkers[0]
    
    # Créer un nouveau timer de 10 minutes pour le checker
    counter_timer = datetime.now() + timedelta(minutes=10)
    
    # Mettre à jour les timers
    user_data[checker_id]["scarlanat_timers"][user_id] = counter_timer
    
    # Supprimer les anciens timers pour cet utilisateur
    user_data[user_id]["scarlanat_timers"].clear()
    
    # Sauvegarder les modifications
    save_points()
    
    # Mentionner le checker
    await ctx.send(f"🚨 <@{checker_id}> Contre-check ! Vous avez 10 minutes pour répondre !")
    
    
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