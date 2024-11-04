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

    user_id = str(message.author.id)
    
    if message.content.lower() == "wassimchien":
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

    elif message.content == "!points":
        if user_id in user_data:
            points = user_data[user_id]["points"]
            await message.channel.send(f"💰 Vous avez {points} points !")
        else:
            await message.channel.send("Vous n'avez pas encore de points !")

    elif message.content == "!classement":
        # Trie les joueurs par points
        classement = []
        for id_joueur, data in user_data.items():
            try:
                # Récupère le membre du serveur
                membre = await message.guild.fetch_member(int(id_joueur))
                if membre:
                    classement.append({
                        "nom": membre.display_name,
                        "points": data["points"]
                    })
            except discord.NotFound:
                continue  # Ignore les utilisateurs qui ne sont plus sur le serveur

        # Trie le classement par points (du plus grand au plus petit)
        classement_trie = sorted(classement, key=lambda x: x["points"], reverse=True)

        # Crée un joli message avec le classement
        embed = discord.Embed(
            title="🏆 Classement des Points",
            description="Voici les meilleurs joueurs !",
            color=0xFFD700  # Couleur or
        )

        # Ajoute les 10 premiers joueurs au classement
        for i, joueur in enumerate(classement_trie[:10], 1):
            # Emoji pour les 3 premiers, numéro pour les autres
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

        await message.channel.send(embed=embed)

    await bot.process_commands(message)


keep_alive()
bot.run(token)