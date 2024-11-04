import discord
from discord.ext import commands
import json
from datetime import datetime
import os
from dotenv import load_dotenv
from keep_alive import keep_alive

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix='!', intents=discord.Intents.all())

user_data = {}

def save_points():
    with open('user_data.json', 'w') as fichier:
        json.dump(user_data, fichier)

def load_points():
    global user_data
    try:
        with open('user_data.json', 'r') as fichier:
            user_data = json.load(fichier)
    except FileNotFoundError:
        user_data = {}

@bot.event
async def on_ready():
    print(f'{bot.user} est connecté !')
    load_points()

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content.lower() == "wassimchien":
        user_id = str(message.author.id)
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
            await message.channel.send(f"🎉 Vous avez gagné 100 points ! Total: {user_data[user_id]['points']} points")
        else:
            await message.channel.send("❌ Vous avez déjà gagné vos points aujourd'hui !")

    await bot.process_commands(message)

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
    # Trie les joueurs par points
    classement = []
    for id_joueur, data in user_data.items():
        try:
            # Récupère le membre du serveur
            membre = await ctx.guild.fetch_member(int(id_joueur))
            if membre:
                classement.append({
                    "nom": membre.display_name,
                    "points": data["points"]
                })
        except discord.NotFound:
            continue

    # Trie le classement par points
    classement_trie = sorted(classement, key=lambda x: x["points"], reverse=True)

    # Crée l'embed
    embed = discord.Embed(
        title="🏆 Classement des Points",
        description="Voici les meilleurs joueurs !",
        color=0xFFD700
    )

    # Ajoute les 10 premiers
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

@bot.command(name="help")
async def help(ctx):
    """Affiche l'aide du bot"""
    embed = discord.Embed(
        title="📖 Aide du Bot",
        description="Voici les commandes disponibles :",
        color=0x3498db
    )
    
    embed.add_field(
        name="wassimchien",
        value="Gagnez 100 points (une fois par jour)",
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

    await ctx.send(embed=embed)

keep_alive()
bot.run(token)