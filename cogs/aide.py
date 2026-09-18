"""Message d'aide. Les valeurs affichées sont lues depuis `config`, pour ne
jamais diverger de l'équilibrage réel."""
import discord  # type: ignore
from discord.ext import commands  # type: ignore

import config


def build_embed():
    """Construit l'embed d'aide. Séparé de la commande pour être testable."""
    embed = discord.Embed(
        title="📖 Aide du Bot",
        description="Voici les commandes disponibles :",
        color=0x3498db
    )

    embed.add_field(
        name="🗓️ !wassimchien (!wc)",
        value=f"""
- Récompense quotidienne : {config.DAILY_BASE_POINTS} points de base
- + {config.DAILY_STREAK_BONUS} points par jour de streak (plafond {config.DAILY_STREAK_BONUS_MAX})
- Un jour manqué remet le streak à 1""",
        inline=False
    )

    embed.add_field(
        name="🎲 !check",
        value=f"""
        **!check @utilisateur**
- Lance un défi à un utilisateur (entre 5h30 et 14h)
- L'utilisateur ciblé a {config.CHECK_WINDOW // 60} minutes pour écrire dans le salon
- Si réponse : +{config.CHECK_RESPONSE_REWARD} points pour le répondant
- Si pas de réponse : +{config.CHECK_TIMEOUT_REWARD} points pour l'initiateur, -{config.CHECK_TIMEOUT_PENALTY} points pour le ciblé
- Une commande ne compte pas comme une réponse, il faut un vrai message
- Max {config.CHECK_MAX_TARGETS} cibles, et {config.CHECK_TARGET_COOLDOWN // 60} min de cooldown avant de re-checker la même personne""",
        inline=False
    )

    embed.add_field(
        name="🎲 !countercheck (!cc)",
        value=f"""
        **!countercheck**
- Déclenché uniquement si vous êtes ciblé par un !check en cours
- Renvoie la pression sur l'initiateur : le check initial se referme sans point
- L'initiateur a maintenant {config.COUNTERCHECK_WINDOW // 60} minutes pour répondre
- S'il répond : personne ne gagne ni ne perd rien
- S'il se tait : il perd {int(config.COUNTERCHECK_PENALTY_RATE * 100)} % de ses points (au moins {config.COUNTERCHECK_PENALTY_MIN})""",
        inline=False
    )

    embed.add_field(
        name="💪 !pompecheck",
        value=f"""- Demande a tout les utilisateurs en ligne de faire des pompes, toutes les personnes dans ce discord doivent le faire
- Le nombre dépend de vos points : {config.POMPES_TIERS[0][1]} pompes sous {config.POMPES_TIERS[0][0]} points, jusqu'à {config.POMPES_MAX} au-delà de {config.POMPES_TIERS[-1][0]}
- Seules les personnes indiquées comme en ligne peuvent exiger à une autre personne de faire les pompes.
- Si une personne hors-ligne fait cette commande, elle doit faire 2 fois plus de pompes.""",
        inline=False
    )

    embed.add_field(
        name="💧 !watercheck (!eau, !water)",
        value="""- Demande a tout les utilisateurs en ligne de boire une gorgée d'eau.
- C'est bien de s'hydrater toute les demi-heure ahah.
- Devient un monstre de puissance tel RiadhOFG""",
        inline=False
    )

    embed.add_field(
        name="💰 !points",
        value="Voir votre nombre de points, votre streak et vos éventuels malus",
        inline=False
    )

    embed.add_field(
        name="🏆 !classement",
        value="Voir le top 10 des joueurs",
        inline=False
    )

    embed.add_field(
        name="📖 !help",
        value="Afficher ce message d'aide",
        inline=False
    )

    return embed


class Aide(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help")
    async def help(self, ctx):
        """Affiche l'aide du bot"""
        await ctx.send(embed=build_embed())


async def setup(bot):
    bot.remove_command('help')
    await bot.add_cog(Aide(bot))
