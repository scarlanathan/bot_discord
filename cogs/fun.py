"""Défis collectifs sans enjeu de points : !pompecheck et !watercheck."""
import discord  # type: ignore
from discord.ext import commands  # type: ignore

import state


def membres_en_ligne(guild):
    return [membre for membre in guild.members
            if membre.status != discord.Status.offline and not membre.bot]


class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="pompecheck")
    async def pompecheck(self, ctx):
        connectes = membres_en_ligne(ctx.guild)
        if not connectes:
            await ctx.send("Aucun membre connecté à taguer !")
            return

        defis = [
            f"{membre.mention} Faites-moi {state.pompes_for(state.get_points(membre.id))} "
            "pompes, gros porc de gros sac !"
            for membre in connectes
        ]
        await ctx.send(" ".join(defis))

    @commands.command(name="watercheck", aliases=["eau", "water"])
    async def watercheck(self, ctx):
        connectes = membres_en_ligne(ctx.guild)
        if not connectes:
            await ctx.send("Aucun membre connecté à taguer !")
            return

        mentions = " ".join(membre.mention for membre in connectes)
        await ctx.send(f"{mentions} Allez boire de l'eau bandes de merde")


async def setup(bot):
    await bot.add_cog(Fun(bot))
