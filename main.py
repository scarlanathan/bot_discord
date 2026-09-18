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


def _env_int(name):
    """Les IDs Discord doivent être des entiers : `get_guild("123")` renvoie None."""
    value = os.getenv(name)
    try:
        return int(value)
    except (TypeError, ValueError):
        print(f"⚠️ {name} absent ou invalide : les rappels de timer seront désactivés.")
        return None


token = os.getenv("DISCORD_TOKEN")
GUILD_ID = _env_int("GUILD_ID")
CHANNEL_ID = _env_int("CHANNEL_ID")
bot = commands.Bot(command_prefix='!', intents=discord.Intents.all())

# Définir le fuseau horaire français
france_tz = pytz.timezone('Europe/Paris')

# ---------------------------------------------------------------------------
# Configuration du système de points
# Toutes les valeurs d'équilibrage sont regroupées ici : pour ajuster le jeu,
# il suffit de modifier ces constantes, rien d'autre dans le fichier.
# ---------------------------------------------------------------------------

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
TIMER_CLEANUP_GRACE = 60        # marge avant de purger un timer expiré

# !pompecheck — paliers (seuil de points, nombre de pompes)
POMPES_TIERS = [(600, 10), (1500, 20), (3000, 30)]
POMPES_MAX = 40                 # au-delà du dernier palier

# Données en mémoire, rechargées depuis MongoDB au démarrage
user_data = {}


def ensure_user(user_id):
    """Garantit qu'un utilisateur existe avec toutes ses clés, et le renvoie."""
    user_id = str(user_id)
    entry = user_data.setdefault(user_id, {})
    entry.setdefault("points", 0)
    entry.setdefault("streak", 0)
    entry.setdefault("last_streak_date", None)
    entry.setdefault("last_claim", None)
    entry.setdefault("scarlanat_timers", {})
    entry.setdefault("check_cooldowns", {})
    return entry


def add_points(user_id, delta):
    """Ajoute (ou retire) des points en respectant le plancher global.

    Renvoie la variation réellement appliquée, qui peut être plus petite que
    `delta` si le plancher est atteint.
    """
    entry = ensure_user(user_id)
    before = entry["points"]
    entry["points"] = max(POINTS_FLOOR, before + delta)
    return entry["points"] - before


def get_points(user_id):
    return user_data.get(str(user_id), {}).get("points", 0)


def to_aware(value):
    """Normalise une date stockée (str ou datetime naïf) en datetime France."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = pytz.utc.localize(value).astimezone(france_tz)
    return value.astimezone(france_tz)


async def check_validity(user_id):
    """Renvoie les checks en cours, encore valides, ciblant `user_id`."""
    initiators = []
    valid_checks = []
    current_time = datetime.now(france_tz)

    for user, user_data_entry in user_data.items():
        for checked_user, check_timer_info in list(user_data_entry.get("scarlanat_timers", {}).items()):
            if (checked_user != str(user_id)
                    or check_timer_info.get("type") != "initial_check"
                    or check_timer_info.get("responded", False)):
                continue

            start_time = to_aware(check_timer_info["start_time"])

            if (current_time - start_time).total_seconds() <= CHECK_WINDOW:
                initiators.append(user)
                valid_checks.append({
                    'initiator': user,
                    'timer_info': check_timer_info
                })

    return initiators, valid_checks

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




def _serialize_timers(timers):
    """Les timers sont des dicts imbriqués : on sérialise leurs dates en ISO."""
    serialized = {}
    for checked_user, timer_info in timers.items():
        if isinstance(timer_info, dict):
            timer_info = {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in timer_info.items()
            }
        elif isinstance(timer_info, datetime):
            timer_info = timer_info.isoformat()
        serialized[checked_user] = timer_info
    return serialized


def _deserialize_timers(timers):
    """Inverse de `_serialize_timers` : remet des datetime conscients du fuseau."""
    restored = {}
    for checked_user, timer_info in timers.items():
        if isinstance(timer_info, dict) and "start_time" in timer_info:
            timer_info = dict(timer_info)
            timer_info["start_time"] = to_aware(timer_info["start_time"])
        elif isinstance(timer_info, (str, datetime)):
            timer_info = to_aware(timer_info)
        restored[checked_user] = timer_info
    return restored


def save_points():
    for user_id, data in user_data.items():
        # Convertir les datetime en chaînes ISO
        serializable_data = data.copy()
        serializable_data.pop('_id', None)  # champ immuable, jamais dans un $set
        if "scarlanat_timers" in serializable_data:
            serializable_data["scarlanat_timers"] = _serialize_timers(
                serializable_data["scarlanat_timers"]
            )
        if "check_cooldowns" in serializable_data:
            serializable_data["check_cooldowns"] = {
                k: v.isoformat() if isinstance(v, datetime) else v
                for k, v in serializable_data["check_cooldowns"].items()
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

        # Reconvertir les timers en datetime et compléter les clés manquantes
        for user_id in list(user_data):
            entry = ensure_user(user_id)
            entry["scarlanat_timers"] = _deserialize_timers(entry["scarlanat_timers"])
            entry["check_cooldowns"] = {
                k: to_aware(v) for k, v in entry["check_cooldowns"].items()
            }
            # Appliquer le plancher aux soldes hérités d'avant son introduction
            entry["points"] = max(POINTS_FLOOR, entry["points"])

    except pymongo.errors.ServerSelectionTimeoutError as e:
        print(f"Failed to connect to MongoDB: {e}")
        user_data = {}
    except Exception as e:
        print(f"An error occurred while loading points: {e}")
        user_data = {}


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


# `on_ready` est rejoué à chaque reconnexion : on ne réinitialise qu'une fois,
# sinon on écrase `user_data` sous les commandes en cours et on relance Flask
# sur un port déjà pris.
_initialise = False


@bot.event
async def on_ready():
    global _initialise
    print(f'{bot.user} est connecté !')

    if _initialise:
        return

    load_points()
    if not start_timer.is_running():
        start_timer.start()
    keep_alive()
    _initialise = True


async def notify_timer(guild, user_id, timer_type, time_left):
    """Prévient un joueur en MP de l'échéance ; à défaut, sur le salon de repli."""
    minutes = max(1, round(time_left / 60))
    if timer_type == "countercheck":
        message = (f"⏱️ Il vous reste environ {minutes} min pour répondre au countercheck, "
                   f"sinon vous perdez {int(COUNTERCHECK_PENALTY_RATE * 100)} % de vos points "
                   f"(au moins {COUNTERCHECK_PENALTY_MIN}).")
    else:
        message = (f"⏱️ Il vous reste environ {minutes} min pour répondre au check, "
                   f"sinon vous perdez {CHECK_TIMEOUT_PENALTY} points.")

    member = None
    if guild:
        member = guild.get_member(int(user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(user_id))
            except discord.HTTPException:
                member = None

    if member:
        try:
            await member.send(message)
            return
        except discord.HTTPException:
            pass  # MP fermés : on bascule sur le salon de repli

    channel = bot.get_channel(CHANNEL_ID) if CHANNEL_ID else None
    if channel:
        await channel.send(f"<@{user_id}> {message}")
    else:
        print(f"Impossible de prévenir {user_id} de l'expiration de son timer.")


@tasks.loop(seconds=TIMER_CHECK_INTERVAL)
async def start_timer():
    """Rappelle aux joueurs qu'un timer approche de son terme, et purge les
    timers laissés derrière par un redémarrage du bot."""
    current_time = datetime.now(france_tz)
    guild = bot.get_guild(GUILD_ID) if GUILD_ID else None
    dirty = False

    # Copies : les commandes modifient `user_data` pendant que la boucle tourne
    for initiator_id, data in list(user_data.items()):
        timers = data.get("scarlanat_timers", {})
        for target_id, timer_info in list(timers.items()):
            if not isinstance(timer_info, dict) or "start_time" not in timer_info:
                continue

            timer_type = timer_info.get("type", "initial_check")
            window = COUNTERCHECK_WINDOW if timer_type == "countercheck" else CHECK_WINDOW
            time_left = window - (current_time - to_aware(timer_info["start_time"])).total_seconds()

            # Purge des timers expirés. La marge laisse la commande encore en
            # vol attribuer ses points avant qu'on n'efface son timer ; la
            # boucle ne touche donc jamais aux points elle-même.
            if time_left <= -TIMER_CLEANUP_GRACE:
                del timers[target_id]
                dirty = True
                continue

            if timer_info.get("responded") or timer_info.get("status") == "responded":
                continue

            # Sur un countercheck, c'est l'initiateur du check qui doit répondre
            waiting_id = initiator_id if timer_type == "countercheck" else target_id

            # Un seul rappel, à l'approche de l'échéance
            if 0 < time_left <= TIMER_REMINDER_BEFORE and not timer_info.get("reminded"):
                timer_info["reminded"] = True
                dirty = True
                await notify_timer(guild, waiting_id, timer_type, time_left)

    if dirty:
        save_points()


@start_timer.before_loop
async def before_start_timer():
    await bot.wait_until_ready()


@start_timer.error
async def start_timer_error(error):
    # Une exception non gérée arrêterait définitivement la boucle en silence
    print(f"Erreur dans la surveillance des timers : {error}")
    if not start_timer.is_running():
        start_timer.start()

@bot.command(name="wassimchien", aliases=["wc"])
async def wassimchien(ctx):
    user_id = str(ctx.author.id)
    entry = ensure_user(user_id)

    # Les dates de streak suivent le fuseau français, comme le reste du bot
    maintenant = datetime.now(france_tz)
    aujourd_hui = maintenant.strftime("%Y-%m-%d")
    hier = (maintenant - timedelta(days=1)).strftime("%Y-%m-%d")

    # Logique de mise à jour du streak
    if entry["last_streak_date"] == aujourd_hui:
        # Déjà réclamé aujourd'hui
        await ctx.send("❌ Vous avez déjà gagné vos points aujourd'hui !")
        return
    elif entry["last_streak_date"] == hier:
        # Streak continue
        entry["streak"] += 1
    else:
        # Première utilisation ou streak interrompu
        entry["streak"] = 1

    # Calcul des points avec bonus de streak
    bonus_streak = min(entry["streak"] * DAILY_STREAK_BONUS, DAILY_STREAK_BONUS_MAX)
    points_totaux = DAILY_BASE_POINTS + bonus_streak

    # Mise à jour des données
    add_points(user_id, points_totaux)
    entry["last_streak_date"] = aujourd_hui

    save_points()

    # Message avec information de streak
    await ctx.send(f"🎉 Vous avez gagné {points_totaux} points ({DAILY_BASE_POINTS} points de base + {bonus_streak} points de streak) !"
                   f"\nVotre streak actuel est de {entry['streak']} jours !"
                   f"\nTotal de points : {entry['points']} points")
    
    
@bot.command(name="check")
async def check(ctx, *mentions: discord.Member):
    
    # Vérification des points négatifs de l'utilisateur qui lance la commande
    user_id = str(ctx.author.id)
    
    # Vérifier si l'utilisateur a des points négatifs
    if user_data.get(user_id, {}).get('points', 0) < 0:
        await ctx.send(f"❌ {ctx.author.mention}, vous ne pouvez pas utiliser cette commande car vous avez des points négatifs.")
        return
    
    # Vérifier si le bot est dans les mentions
    if bot.user in mentions:
        await ctx.send("❌ Vous ne pouvez pas check le bot !")
        return

    # Utiliser le fuseau horaire français
    current_time = datetime.now(france_tz)

    # Convertir l'heure actuelle en minutes depuis minuit
    current_minutes = current_time.hour * 60 + current_time.minute

    # Vérifier si l'heure actuelle est en dehors de la plage autorisée
    if current_minutes < CHECK_START_MINUTES or current_minutes >= CHECK_END_MINUTES:
        await ctx.send(f"❌ Vous ne pouvez utiliser cette commande qu'entre 5h30 et 14h. Heure actuelle : {current_time.strftime('%H:%M')}")
        return

    today = current_time.strftime("%Y-%m-%d")

    if ctx.author in mentions:
        await ctx.send("❌ Vous ne pouvez pas vous check vous-même !")
        return

    initiator = ensure_user(user_id)

    # Nettoyer les anciens timers avant d'en créer de nouveaux
    for checked_user_id, timer_info in list(initiator["scarlanat_timers"].items()):
        timer_info["start_time"] = to_aware(timer_info["start_time"])
        if (current_time - timer_info["start_time"]).total_seconds() > CHECK_WINDOW:
            del initiator["scarlanat_timers"][checked_user_id]

    # Dédoublonner les mentions en gardant l'ordre, et écarter les bots
    checked_users = []
    for user in mentions:
        if user.bot or user in checked_users:
            continue
        checked_users.append(user)

    # Anti-farm : on ne peut pas re-checker la même personne en boucle
    on_cooldown = []
    available_users = []
    for user in checked_users:
        last_check = initiator["check_cooldowns"].get(str(user.id))
        if last_check is not None:
            elapsed = (current_time - to_aware(last_check)).total_seconds()
            if elapsed < CHECK_TARGET_COOLDOWN:
                on_cooldown.append((user, int((CHECK_TARGET_COOLDOWN - elapsed) // 60) + 1))
                continue
        available_users.append(user)

    if on_cooldown:
        details = ", ".join(f"{user.mention} (encore {minutes} min)" for user, minutes in on_cooldown)
        await ctx.send(f"⏳ Vous avez déjà check récemment : {details}")

    if not available_users:
        return

    # Anti-farm : plafonner le nombre de cibles d'un même check
    if len(available_users) > CHECK_MAX_TARGETS:
        await ctx.send(f"⚠️ Maximum {CHECK_MAX_TARGETS} cibles par check : seules les {CHECK_MAX_TARGETS} premières sont retenues.")
        available_users = available_users[:CHECK_MAX_TARGETS]

    checked_users = available_users

    # Créer le timer de chaque cible et poser son cooldown
    for user in checked_users:
        user_check_id = str(user.id)
        ensure_user(user_check_id)
        initiator["check_cooldowns"][user_check_id] = current_time

        # Créer un nouveau timer uniquement s'il n'existe pas déjà
        if user_check_id not in initiator["scarlanat_timers"]:
            initiator["scarlanat_timers"][user_check_id] = {
                "start_time": current_time,
                "initiator": user_id,
                "type": "initial_check",
                "responded": False  # Nouveau champ pour suivre la réponse
            }

    # Message récapitulatif
    users_mention = " ".join([user.mention for user in checked_users])
    await ctx.send(
        f"Un compteur de {CHECK_WINDOW // 60} minutes a été lancé pour {users_mention}.\n"
        f"Répondre = +{CHECK_RESPONSE_REWARD} points, se taire = -{CHECK_TIMEOUT_PENALTY} points."
    )
    save_points()

    # Attente des réponses : l'échéance est fixe, une réponse ne relance pas
    # le compteur des autres cibles
    deadline = current_time + timedelta(seconds=CHECK_WINDOW)
    responded_users = set()
    try:
        while len(responded_users) < len(checked_users):
            remaining = (deadline - datetime.now(france_tz)).total_seconds()
            if remaining <= 0:
                raise asyncio.TimeoutError

            response = await bot.wait_for(
                "message",
                timeout=remaining,
                check=lambda m: (
                    m.author.id in [user.id for user in checked_users] and
                    m.channel == ctx.channel and
                    m.author.id not in responded_users
                )
            )

            # Identifier l'utilisateur qui a répondu
            responder_id = str(response.author.id)
            responder = ensure_user(responder_id)

            # Marquer le timer comme répondu
            timer_info = initiator["scarlanat_timers"].get(responder_id)
            if timer_info:
                timer_info["responded"] = True

            # Donner les points à l'utilisateur qui a répondu
            gained = add_points(responder_id, CHECK_RESPONSE_REWARD)
            responder["last_claim"] = today

            # Ajouter l'utilisateur aux répondants
            responded_users.add(response.author.id)
            save_points()

            # Message de confirmation
            await ctx.send(f"🎉 <@{response.author.id}> a répondu ! Il gagne {gained} points ! Total: {responder['points']} points")

        # Si tous les utilisateurs ont répondu
        await ctx.send("Tous les utilisateurs mentionnés ont répondu !")

    except asyncio.TimeoutError:
        # Liste des utilisateurs qui n'ont pas répondu
        non_responded = [user for user in checked_users if user.id not in responded_users]

        if non_responded:
            # Donner des points à l'initiateur pour chaque utilisateur silencieux
            points_earned = add_points(user_id, len(non_responded) * CHECK_TIMEOUT_REWARD)
            initiator["last_claim"] = today

            # Retirer des points aux utilisateurs qui n'ont pas répondu
            losses = {}
            for user_to_check in non_responded:
                losses[user_to_check.id] = -add_points(user_to_check.id, -CHECK_TIMEOUT_PENALTY)

            # Message indiquant qui n'a pas répondu
            non_responded_mentions = " ".join([user.mention for user in non_responded])
            await ctx.send(f"🎉 <@{ctx.author.id}> a gagné {points_earned} points car {non_responded_mentions} n'ont pas répondu ! Total: {initiator['points']} points")

            # Message pour les utilisateurs qui n'ont pas répondu
            for user in non_responded:
                perdu = losses[user.id]
                if perdu:
                    await ctx.send(f"<@{user.id}> a perdu {perdu} points pour ne pas avoir répondu. Nouveau total : {get_points(user.id)} points")
                else:
                    await ctx.send(f"<@{user.id}> n'a pas répondu, mais est déjà au plancher de {POINTS_FLOOR} points.")

    # Nettoyer les timers de ce check, quel que soit le dénouement
    for user in checked_users:
        initiator["scarlanat_timers"].pop(str(user.id), None)

    save_points()
    
@bot.command(name="pompecheck")
async def pompecheck(ctx):
    # Récupérer tous les membres connectés du serveur (hors bots)
    connected_members = [member for member in ctx.guild.members if member.status != discord.Status.offline and not member.bot]
    
    # Vérifier s'il y a des membres connectés
    if not connected_members:
        await ctx.send("Aucun membre connecté à taguer !")
        return

    # Préparer un message personnalisé par utilisateur
    challenge_messages = []
    
    for member in connected_members:
        user_id = str(member.id)
        
        # Récupérer les points de l'utilisateur
        points = get_points(user_id)

        # Déterminer le nombre de pompes selon les paliers configurés
        pompes = POMPES_MAX
        for seuil, nombre in POMPES_TIERS:
            if points < seuil:
                pompes = nombre
                break

        challenge_messages.append(f"{member.mention} Faites-moi {pompes} pompes, gros porc de gros sac !")
    
    # Envoyer le message global
    await ctx.send(" ".join(challenge_messages))
    
@bot.command(name="countercheck", aliases=["cc"])
async def countercheck(ctx):
    # Initialisation des variables
    user_id = str(ctx.author.id)
    current_time = datetime.now(france_tz)

    ensure_user(user_id)

    # Utiliser la fonction check_validity pour obtenir les checks valides
    _, valid_checks = await check_validity(user_id)

    # Si aucun check valide
    if not valid_checks:
        await ctx.send("❌ Vous n'êtes pas actuellement dans un check valide.")
        return

    # Traitement de chaque check
    for check in valid_checks:
        initiator_id = check['initiator']
        initiator = ensure_user(initiator_id)

        # Création du timer de countercheck
        initiator["scarlanat_timers"][user_id] = {
            "start_time": current_time,
            "type": "countercheck",
            "status": "waiting_response"
        }

        await ctx.send(
            f"<@{initiator_id}> a maintenant {COUNTERCHECK_WINDOW // 60} minutes pour répondre, "
            f"sinon il perd {int(COUNTERCHECK_PENALTY_RATE * 100)} % de ses points "
            f"(au moins {COUNTERCHECK_PENALTY_MIN}) !"
        )

    save_points()

    # Attente des réponses : échéance commune à tous les initiateurs
    deadline = current_time + timedelta(seconds=COUNTERCHECK_WINDOW)
    responses = []
    try:
        for _ in range(len(valid_checks)):
            remaining = (deadline - datetime.now(france_tz)).total_seconds()
            if remaining <= 0:
                raise asyncio.TimeoutError

            response = await bot.wait_for(
                "message",
                timeout=remaining,
                check=lambda m: (
                    m.author.id in [int(check['initiator']) for check in valid_checks] and
                    m.channel == ctx.channel and
                    m.author.id not in [r.author.id for r in responses]
                )
            )
            responses.append(response)

            # Marquer le countercheck comme réussi
            responder_id = str(response.author.id)
            timer_info = ensure_user(responder_id)["scarlanat_timers"].get(user_id)
            if timer_info:
                timer_info["status"] = "responded"
            await ctx.send(f"🎉 <@{response.author.id}> a répondu à temps !")

    except asyncio.TimeoutError:
        # Gestion des non-répondants
        responded_ids = {str(r.author.id) for r in responses}
        non_respondents = [
            check['initiator'] for check in valid_checks
            if str(check['initiator']) not in responded_ids
        ]

        for initiator_id in non_respondents:
            points_before = get_points(initiator_id)
            # Pénalité proportionnelle, avec un minimum, et bornée par le plancher
            penalite = max(COUNTERCHECK_PENALTY_MIN, int(points_before * COUNTERCHECK_PENALTY_RATE))
            perdu = -add_points(initiator_id, -penalite)

            await ctx.send(
                f"❌ <@{initiator_id}> n'a pas répondu à temps et perd {perdu} points ! "
                f"({points_before} → {get_points(initiator_id)})"
            )

    # Nettoyage des timers du countercheck
    for check in valid_checks:
        ensure_user(check['initiator'])["scarlanat_timers"].pop(user_id, None)

    save_points()
    
    
@bot.command(name="points")
async def points(ctx):
    """Affiche vos points actuels"""
    user_id = str(ctx.author.id)
    if user_id not in user_data:
        await ctx.send("Vous n'avez pas encore de points !")
        return

    entry = ensure_user(user_id)
    message = f"💰 Vous avez {entry['points']} points !"
    if entry["streak"]:
        message += f"\n🔥 Streak : {entry['streak']} jour(s)"
    if entry["points"] <= POINTS_FLOOR:
        message += f"\n⚠️ Vous êtes au plancher ({POINTS_FLOOR}) : faites !wc pour remonter."
    elif entry["points"] < 0:
        message += "\n⚠️ Points négatifs : vous ne pouvez pas lancer de !check."
    await ctx.send(message)

@bot.command(name="watercheck")
async def watercheck(ctx):
    # Récupérer tous les membres connectés du serveur (hors bots)
    connected_members = [member for member in ctx.guild.members if member.status != discord.Status.offline and not member.bot]
    
    # Vérifier s'il y a des membres connectés
    if not connected_members:
        await ctx.send("Aucun membre connecté à taguer !")
        return

    # Préparer la liste des membres à taguer
    users_mention = " ".join([member.mention for member in connected_members])
    
    # Message de défi
    await ctx.send(f"{users_mention} Allez boire de l'eau bandes de merde")


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
                    "points": data.get("points", 0)
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
    
@bot.command(name="reset")
async def reset_points(ctx):
    # Vérifier que seul @scrlnat peut utiliser la commande
    if ctx.author.id != 660241424746545168:  # Vous devrez définir SCRLNAT_USER_ID
        await ctx.send("❌ Vous n'avez pas la permission d'utiliser cette commande !")
        return
    
    # Réinitialiser les points, streaks, timers et cooldowns de tout le monde
    for user_id in list(user_data):
        entry = ensure_user(user_id)
        entry["points"] = 0
        entry["streak"] = 0
        entry["last_streak_date"] = None
        entry["scarlanat_timers"] = {}
        entry["check_cooldowns"] = {}

    # Sauvegarder les modifications dans la base de données
    save_points()
    
    # Confirmation
    await ctx.send("✅ Tous les points ont été réinitialisés à zéro !")
    
bot.remove_command('help')


@bot.command(name="help")
async def help(ctx):
    """Affiche l'aide du bot"""
    embed = discord.Embed(
        title="📖 Aide du Bot",
        description="Voici les commandes disponibles :",
        color=0x3498db
    )
    
    embed.add_field(
    name="🗓️ !wassimchien (!wc)",
    value=f"""
- Récompense quotidienne : {DAILY_BASE_POINTS} points de base
- + {DAILY_STREAK_BONUS} points par jour de streak (plafond {DAILY_STREAK_BONUS_MAX})
- Un jour manqué remet le streak à 1""",
        inline=False
    )

    embed.add_field(
    name="🎲 !check",
    value=f"""
        **!check @utilisateur**
- Lance un défi à un utilisateur (entre 5h30 et 14h)
- L'utilisateur ciblé a {CHECK_WINDOW // 60} minutes pour répondre
- Si réponse : +{CHECK_RESPONSE_REWARD} points pour le répondant
- Si pas de réponse : +{CHECK_TIMEOUT_REWARD} points pour l'initiateur, -{CHECK_TIMEOUT_PENALTY} points pour le ciblé
- Max {CHECK_MAX_TARGETS} cibles, et {CHECK_TARGET_COOLDOWN // 60} min de cooldown avant de re-checker la même personne""",
        inline=False
    )

    embed.add_field(
    name="🎲 !countercheck",
    value=f"""
        **!countercheck**
- Déclenché uniquement si vous êtes ciblé par un !check en cours
- Lance un contre-défi à l'initiateur du check
- L'initiateur a maintenant {COUNTERCHECK_WINDOW // 60} minutes pour répondre
- Si pas de réponse : il perd {int(COUNTERCHECK_PENALTY_RATE * 100)} % de ses points (au moins {COUNTERCHECK_PENALTY_MIN})""",
        inline=False
    )
    
    embed.add_field(
        name="!pompecheck",
        value="""- Demande a tout les utilisateurs en ligne de faire 10 pompes, toutes les personnes dans ce discord doivent le faire
- Seules les personnes indiquées comme en ligne peuvent exiger à une autre personne de faire les pompes.
- Si une personne hors-ligne fait cette commande, elle doit faire 2 fois plus de pompes.""",
        inline=False
    )
    
    embed.add_field(
    name="!watercheck",
    value="""- Demande a tout les utilisateurs en ligne de boire une gorgée d'eau.
- C'est bien de s'hydrater toute les demi-heure ahah.
- Devient un monstre de puissance tel RiadhOFG""",
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
        name="!help",
        value="Afficher ce message d'aide",
        inline=False
    )

    await ctx.send(embed=embed)

bot.run(token)
