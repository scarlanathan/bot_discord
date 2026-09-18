"""Configuration du bot : variables d'environnement et valeurs d'équilibrage.

Tout ce qui se règle sans toucher à la logique se trouve ici.
"""
import os

import pytz
from dotenv import load_dotenv  # type: ignore

load_dotenv()


def _env_int(name, default=None):
    """Les IDs Discord doivent être des entiers : `get_guild("123")` renvoie None."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        print(f"⚠️ {name} n'est pas un entier valide, valeur ignorée.")
        return default


# --------------------------------------------------------------- environnement
TOKEN = os.getenv("DISCORD_TOKEN")
MONGODB_URI = os.getenv("MONGODB_URI")
GUILD_ID = _env_int("GUILD_ID")
CHANNEL_ID = _env_int("CHANNEL_ID")

# Seul ce compte peut utiliser !reset. Surchargeable par l'environnement.
OWNER_ID = _env_int("OWNER_ID", 660241424746545168)

DB_NAME = "nathan"
COLLECTION_NAME = "users"

# Fuseau de référence : toutes les dates du bot y sont exprimées
FRANCE_TZ = pytz.timezone('Europe/Paris')

# ------------------------------------------------------- équilibrage du jeu
# Pour ajuster le jeu, il suffit de modifier ces constantes.

# !wassimchien — récompense quotidienne
DAILY_BASE_POINTS = 60          # points de base pour la réclamation du jour
DAILY_STREAK_BONUS = 10         # bonus par jour consécutif de streak
DAILY_STREAK_BONUS_MAX = 90     # plafond du bonus de streak (atteint au 9e jour)

# !check — défi lancé à d'autres membres
CHECK_WINDOW = 1800             # 30 min : délai laissé à la cible pour répondre
CHECK_RESPONSE_REWARD = 15      # gagné par la cible qui répond à temps
CHECK_TIMEOUT_REWARD = 25       # gagné par l'initiateur, par cible silencieuse
CHECK_TIMEOUT_PENALTY = 20      # perdu par une cible qui n'a pas répondu
CHECK_MAX_TARGETS = 5           # nombre max de cibles par commande (anti-farm)
CHECK_TARGET_COOLDOWN = 3600    # 1 h avant de pouvoir re-checker la même cible
CHECK_START_MINUTES = 5 * 60 + 30   # plage horaire autorisée : 5h30…
CHECK_END_MINUTES = 14 * 60         # …jusqu'à 14h00

# !countercheck — contre-défi de la cible vers l'initiateur
COUNTERCHECK_WINDOW = 600           # 10 min pour que l'initiateur réagisse
COUNTERCHECK_PENALTY_RATE = 0.25    # part des points perdus s'il ne répond pas
COUNTERCHECK_PENALTY_MIN = 50       # perte plancher, même avec peu de points

# Plancher global : les points ne descendent jamais en dessous
POINTS_FLOOR = -100

# Surveillance des timers en arrière-plan
TIMER_CHECK_INTERVAL = 30       # fréquence de la boucle de surveillance
TIMER_REMINDER_BEFORE = 300     # rappel en MP 5 min avant la fin du délai
TIMER_CLEANUP_GRACE = 60        # marge avant de purger un timer clos

# !pompecheck — paliers (seuil de points, nombre de pompes)
POMPES_TIERS = [(600, 10), (1500, 20), (3000, 30)]
POMPES_MAX = 40                 # au-delà du dernier palier
