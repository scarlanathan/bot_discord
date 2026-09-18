"""Récompense quotidienne, consultation des points, classement, reset."""
import discord  # type: ignore
from discord.ext import commands  # type: ignore

import config
import state
import storage


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="wassimchien", aliases=["wc"])
    async def wassimchien(self, ctx):
        user_id = str(ctx.author.id)
        resultat = state.claim_daily(user_id, state.now())

        if resultat is None:
            await ctx.send("❌ Vous avez déjà gagné vos points aujourd'hui !")
            return

        total, bonus, streak = resultat
        storage.save_points()

        await ctx.send(f"🎉 Vous avez gagné {total} points "
                       f"({config.DAILY_BASE_POINTS} points de base + {bonus} points "
                       f"de streak) !\nVotre streak actuel est de {streak} jours !"
                       f"\nTotal de points : {state.get_points(user_id)} points")

    @commands.command(name="points")
    async def points(self, ctx):
        """Affiche vos points actuels"""
        user_id = str(ctx.author.id)
        if user_id not in state.user_data:
            await ctx.send("Vous n'avez pas encore de points !")
            return

        entry = state.ensure_user(user_id)
        message = f"💰 Vous avez {entry['points']} points !"
        if entry["streak"]:
            message += f"\n🔥 Streak : {entry['streak']} jour(s)"
        if entry["points"] <= config.POINTS_FLOOR:
            message += (f"\n⚠️ Vous êtes au plancher ({config.POINTS_FLOOR}) : "
                        "faites !wc pour remonter.")
        elif entry["points"] < 0:
            message += "\n⚠️ Points négatifs : vous ne pouvez pas lancer de !check."
        await ctx.send(message)

    @commands.command(name="classement")
    async def classement(self, ctx):
        """Affiche le classement des 10 meilleurs joueurs"""
        joueurs = []
        for id_joueur, data in state.user_data.items():
            try:
                membre = ctx.guild.get_member(int(id_joueur))
                if membre is None:
                    membre = await ctx.guild.fetch_member(int(id_joueur))
            except (discord.HTTPException, ValueError):
                continue
            if membre:
                joueurs.append({"nom": membre.display_name,
                                "points": data.get("points", 0)})

        joueurs.sort(key=lambda j: j["points"], reverse=True)

        embed = discord.Embed(
            title="🏆 Classement des Points",
            description="Voici les meilleurs joueurs !",
            color=0xFFD700
        )

        medailles = {1: "🥇", 2: "🥈", 3: "🥉"}
        for rang, joueur in enumerate(joueurs[:10], 1):
            embed.add_field(
                name=f"{medailles.get(rang, f'{rang}.')} {joueur['nom']}",
                value=f"{joueur['points']} points",
                inline=False
            )

        await ctx.send(embed=embed)

    @commands.command(name="reset")
    async def reset_points(self, ctx):
        if ctx.author.id != config.OWNER_ID:
            await ctx.send("❌ Vous n'avez pas la permission d'utiliser cette commande !")
            return

        state.reset_all()
        storage.save_points()
        await ctx.send("✅ Tous les points ont été réinitialisés à zéro !")


async def setup(bot):
    await bot.add_cog(Economy(bot))
