"""Point d'entrée du bot Discord.

La logique se trouve dans `state` (règles du jeu), `storage` (persistance) et
`cogs/` (commandes Discord). Ce fichier se contente d'assembler le tout.
"""
import discord  # type: ignore
from discord.ext import commands  # type: ignore

import config
import storage
from keep_alive import keep_alive

EXTENSIONS = (
    "cogs.economy",
    "cogs.challenges",
    "cogs.fun",
    "cogs.aide",
)


class BotDiscord(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=discord.Intents.all())

    async def setup_hook(self):
        """Exécuté une seule fois, avant la connexion.

        C'est ici que tout se met en place, et non dans `on_ready` qui est
        rejoué à chaque reconnexion : le rejouer écraserait `user_data` sous
        les commandes en cours.
        """
        storage.load_points()
        for extension in EXTENSIONS:
            await self.load_extension(extension)

    async def on_ready(self):
        print(f'{self.user} est connecté !')


def main():
    if not config.TOKEN:
        raise SystemExit("DISCORD_TOKEN manquant : renseignez-le dans .env")

    storage.connect()
    keep_alive()
    BotDiscord().run(config.TOKEN)


if __name__ == "__main__":
    main()
