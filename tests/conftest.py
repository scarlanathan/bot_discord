"""Outillage commun aux tests : état remis à zéro, doublures de Discord et Mongo."""
import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import state  # noqa: E402
import storage  # noqa: E402

SALON = 111


@pytest.fixture(autouse=True)
def etat_vierge():
    """Chaque test part d'un état vide, sans persistance réelle.

    La configuration est neutralisée pour que les tests ne dépendent pas du
    `.env` de la machine : le salon de repli est le salon factice.
    """
    guild_id, channel_id = config.GUILD_ID, config.CHANNEL_ID
    config.GUILD_ID = None
    config.CHANNEL_ID = SALON

    state.user_data.clear()
    storage._last_saved.clear()
    storage.users_collection = None
    yield
    state.user_data.clear()
    storage._last_saved.clear()
    storage.users_collection = None

    config.GUILD_ID, config.CHANNEL_ID = guild_id, channel_id


class FakeCollection:
    """Collection MongoDB minimale : mémorise ce qui aurait été écrit."""

    def __init__(self, documents=None):
        self.documents = documents or []
        self.appels = []          # une entrée par bulk_write, avec les _id écrits
        self.echouer = False

    def find(self):
        return [dict(doc) for doc in self.documents]

    def bulk_write(self, operations, ordered=True):
        if self.echouer:
            import pymongo
            raise pymongo.errors.AutoReconnect("réseau coupé")
        self.appels.append([op._filter['_id'] for op in operations])


class FakeChannel:
    def __init__(self, channel_id=SALON):
        self.id = channel_id
        self.envoyes = []

    async def send(self, text=None, embed=None):
        self.envoyes.append(text if text is not None else embed)


class FakeAuthor:
    def __init__(self, user_id, bot=False):
        self.id = user_id
        self.bot = bot


class FakeMessage:
    def __init__(self, user_id, channel=None, est_commande=False, bot=False):
        self.author = FakeAuthor(user_id, bot)
        self.channel = channel or FakeChannel()
        self.est_commande = est_commande


class FakeContext:
    def __init__(self, valid):
        self.valid = valid


class FakeBot:
    """Juste ce dont les cogs se servent : salons, guilde et get_context."""

    def __init__(self, channels=None):
        self.channels = channels or {}
        self.user = object()

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_guild(self, guild_id):
        return None

    async def get_context(self, message):
        return FakeContext(message.est_commande)


@pytest.fixture
def salon():
    return FakeChannel()


@pytest.fixture
def challenges(salon):
    """Le vrai cog, branché sur un bot factice."""
    from cogs.challenges import Challenges
    return Challenges(FakeBot({SALON: salon}))


@pytest.fixture
def poser_check():
    """Crée un check déjà en cours, éventuellement lancé il y a un moment."""
    def _poser(initiateur, cible, minutes_ecoulees=0, **extra):
        moment = state.now() - timedelta(minutes=minutes_ecoulees)
        timer = state.register_check(initiateur, cible, moment, SALON)
        timer.update(extra)
        return timer
    return _poser


@pytest.fixture
def poser_countercheck():
    def _poser(initiateur, contre_attaquant, minutes_ecoulees=0):
        moment = state.now() - timedelta(minutes=minutes_ecoulees)
        return state.register_countercheck(initiateur, contre_attaquant, moment, SALON)
    return _poser
