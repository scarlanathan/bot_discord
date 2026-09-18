import discord # type: ignore
from discord.ext import commands, tasks # type: ignore
import json
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv # type: ignore
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


def timer_window(timer_type):
    """Délai accordé selon le type de timer."""
    return COUNTERCHECK_WINDOW if timer_type == "countercheck" else CHECK_WINDOW


def is_resolved(timer_info):
    """`responded` et `status` sont les champs de l'ancien format."""
    return bool(timer_info.get("resolved")
                or timer_info.get("responded")
                or timer_info.get("status") == "responded")


def mark_resolved(timer_info, outcome):
    """Clôt un timer. `outcome` : responded, expired ou countered."""
    timer_info["resolved"] = True
    timer_info["outcome"] = outcome
    timer_info["responded"] = True  # compatibilité avec l'ancien format


def pending_timers():
    """Tous les timers encore en jeu, sous la forme
    (initiateur, clé, timer, type, joueur attendu).

    C'est la seule source de vérité sur les défis en cours : elle est
    persistée en base, donc elle survit à un redémarrage du bot.
    """
    # Copies : les commandes modifient `user_data` pendant l'itération
    for initiator_id, data in list(user_data.items()):
        for key, timer_info in list(data.get("scarlanat_timers", {}).items()):
            if not isinstance(timer_info, dict) or "start_time" not in timer_info:
                continue
            if is_resolved(timer_info):
                continue

            timer_type = timer_info.get("type", "initial_check")
            # Sur un countercheck, c'est l'initiateur du check qui doit répondre
            waiting_id = initiator_id if timer_type == "countercheck" else key
            yield initiator_id, key, timer_info, timer_type, waiting_id


async def check_validity(user_id):
    """Renvoie les checks en cours, encore valides, ciblant `user_id`."""
    initiators = []
    valid_checks = []
    current_time = datetime.now(france_tz)

    for initiator_id, key, timer_info, timer_type, _ in pending_timers():
        if key != str(user_id) or timer_type != "initial_check":
            continue

        start_time = to_aware(timer_info["start_time"])

        if (current_time - start_time).total_seconds() <= CHECK_WINDOW:
            initiators.append(initiator_id)
            valid_checks.append({
                'initiator': initiator_id,
                'timer_info': timer_info
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


async def announce(timer_info, text):
    """Publie le dénouement d'un timer dans le salon où il a été lancé."""
    channel = bot.get_channel(timer_info.get("channel_id") or 0)
    if channel is None and CHANNEL_ID:
        channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"Aucun salon disponible pour annoncer : {text}")
        return
    await channel.send(text)


async def resolve_check_response(target_id, timer_info):
    """La cible d'un check a donné signe de vie : elle encaisse sa récompense."""
    gained = add_points(target_id, CHECK_RESPONSE_REWARD)
    ensure_user(target_id)["last_claim"] = datetime.now(france_tz).strftime("%Y-%m-%d")
    mark_resolved(timer_info, "responded")
    await announce(timer_info, f"🎉 <@{target_id}> a répondu ! Il gagne {gained} points ! "
                               f"Total : {get_points(target_id)} points")


async def resolve_countercheck_response(initiator_id, timer_info):
    """L'initiateur a réagi au contre-défi : il échappe à la pénalité."""
    mark_resolved(timer_info, "responded")
    await announce(timer_info, f"🎉 <@{initiator_id}> a répondu à temps, il échappe à la pénalité !")


def expire_check(initiator_id, target_id, timer_info):
    """Personne n'a répondu : l'initiateur encaisse, la cible paie.

    Ne publie rien : la boucle regroupe les messages d'un même initiateur.
    """
    earned = add_points(initiator_id, CHECK_TIMEOUT_REWARD)
    lost = -add_points(target_id, -CHECK_TIMEOUT_PENALTY)
    mark_resolved(timer_info, "expired")
    return earned, lost


async def expire_countercheck(initiator_id, timer_info):
    """L'initiateur a laissé filer le contre-défi : pénalité proportionnelle."""
    before = get_points(initiator_id)
    penalite = max(COUNTERCHECK_PENALTY_MIN, int(before * COUNTERCHECK_PENALTY_RATE))
    lost = -add_points(initiator_id, -penalite)
    mark_resolved(timer_info, "expired")
    await announce(timer_info, f"❌ <@{initiator_id}> n'a pas répondu au countercheck "
                               f"et perd {lost} points ! ({before} → {get_points(initiator_id)})")


async def settle_expirations(expirations):
    """Règle les timers échus. Les checks d'un même initiateur sont regroupés
    en un seul message : après une longue coupure, la boucle en rattrape
    potentiellement plusieurs d'un coup."""
    par_initiateur = {}
    for initiator_id, key, timer_info, timer_type in expirations:
        if timer_type == "countercheck":
            await expire_countercheck(initiator_id, timer_info)
        else:
            par_initiateur.setdefault(initiator_id, []).append((key, timer_info))

    for initiator_id, checks in par_initiateur.items():
        gains = 0
        lignes = []
        for target_id, timer_info in checks:
            earned, lost = expire_check(initiator_id, target_id, timer_info)
            gains += earned
            if lost:
                lignes.append(f"<@{target_id}> perd {lost} points (total : {get_points(target_id)})")
            else:
                lignes.append(f"<@{target_id}> est déjà au plancher de {POINTS_FLOOR} points")

        await announce(checks[0][1],
                       "⏱️ Temps écoulé, personne n'a répondu.\n"
                       f"🎉 <@{initiator_id}> gagne {gains} points "
                       f"(total : {get_points(initiator_id)})\n" + "\n".join(lignes))


def purge_resolved_timers(current_time):
    """Efface les timers clos, une fois passée la marge de sécurité."""
    purged = False
    for data in list(user_data.values()):
        timers = data.get("scarlanat_timers", {})
        for key, timer_info in list(timers.items()):
            if not isinstance(timer_info, dict) or "start_time" not in timer_info:
                del timers[key]
                purged = True
                continue

            age = (current_time - to_aware(timer_info["start_time"])).total_seconds()
            if is_resolved(timer_info) and age > TIMER_CLEANUP_GRACE:
                del timers[key]
                purged = True
    return purged


@tasks.loop(seconds=TIMER_CHECK_INTERVAL)
async def start_timer():
    """Fait vivre les défis en dehors des commandes : rappels, règlement des
    échéances, purge. C'est ce qui permet à un check de se conclure même si le
    bot a redémarré entre son lancement et son expiration."""
    current_time = datetime.now(france_tz)
    guild = bot.get_guild(GUILD_ID) if GUILD_ID else None
    dirty = False
    expirations = []

    for initiator_id, key, timer_info, timer_type, waiting_id in pending_timers():
        elapsed = (current_time - to_aware(timer_info["start_time"])).total_seconds()
        time_left = timer_window(timer_type) - elapsed

        if time_left <= 0:
            expirations.append((initiator_id, key, timer_info, timer_type))
            continue

        # Un seul rappel, à l'approche de l'échéance
        if time_left <= TIMER_REMINDER_BEFORE and not timer_info.get("reminded"):
            timer_info["reminded"] = True
            dirty = True
            await notify_timer(guild, waiting_id, timer_type, time_left)

    if expirations:
        await settle_expirations(expirations)
        dirty = True

    if purge_resolved_timers(current_time):
        dirty = True

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

@bot.listen('on_message')
async def resolve_timers_on_message(message):
    """Un message du joueur attendu règle son timer. Passer par un listener
    plutôt que par `wait_for` permet à un check de se conclure même si la
    commande qui l'a lancé n'existe plus (redémarrage du bot)."""
    if message.author.bot:
        return

    # Une commande n'est pas une réponse, sinon !points rapporterait des points
    context = await bot.get_context(message)
    if context.valid:
        return

    author_id = str(message.author.id)
    current_time = datetime.now(france_tz)
    resolved = False

    for initiator_id, key, timer_info, timer_type, waiting_id in pending_timers():
        if waiting_id != author_id:
            continue

        # Le défi se joue dans le salon où il a été lancé
        channel_id = timer_info.get("channel_id")
        if channel_id and message.channel.id != channel_id:
            continue

        # Déjà expiré : c'est à la boucle de trancher, pas à ce message
        elapsed = (current_time - to_aware(timer_info["start_time"])).total_seconds()
        if elapsed > timer_window(timer_type):
            continue

        if timer_type == "countercheck":
            await resolve_countercheck_response(initiator_id, timer_info)
        else:
            await resolve_check_response(key, timer_info)
        resolved = True

    if resolved:
        save_points()


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

    if ctx.author in mentions:
        await ctx.send("❌ Vous ne pouvez pas vous check vous-même !")
        return

    initiator = ensure_user(user_id)

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
                "channel_id": ctx.channel.id,
                "resolved": False,
                "responded": False
            }

    # Message récapitulatif
    users_mention = " ".join([user.mention for user in checked_users])
    await ctx.send(
        f"Un compteur de {CHECK_WINDOW // 60} minutes a été lancé pour {users_mention}.\n"
        f"Répondre = +{CHECK_RESPONSE_REWARD} points, se taire = -{CHECK_TIMEOUT_PENALTY} points."
    )

    # La commande s'arrête ici : ce sont le listener de messages et la boucle
    # de fond qui concluent le défi, à partir des timers enregistrés en base.
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

        # Le check initial se referme sans gain ni perte : plutôt que de
        # répondre, la cible a renvoyé la pression sur l'initiateur.
        mark_resolved(check['timer_info'], "countered")

        # Le timer de countercheck prend sa place, sur la même clé
        initiator["scarlanat_timers"][user_id] = {
            "start_time": current_time,
            "initiator": initiator_id,
            "type": "countercheck",
            "channel_id": ctx.channel.id,
            "resolved": False,
            "status": "waiting_response"
        }

        await ctx.send(
            f"<@{initiator_id}> a maintenant {COUNTERCHECK_WINDOW // 60} minutes pour répondre, "
            f"sinon il perd {int(COUNTERCHECK_PENALTY_RATE * 100)} % de ses points "
            f"(au moins {COUNTERCHECK_PENALTY_MIN}) !"
        )

    # Comme pour !check, le dénouement est confié au listener et à la boucle
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

@bot.command(name="watercheck", aliases=["eau", "water"])
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
- L'utilisateur ciblé a {CHECK_WINDOW // 60} minutes pour écrire dans le salon
- Si réponse : +{CHECK_RESPONSE_REWARD} points pour le répondant
- Si pas de réponse : +{CHECK_TIMEOUT_REWARD} points pour l'initiateur, -{CHECK_TIMEOUT_PENALTY} points pour le ciblé
- Une commande ne compte pas comme une réponse, il faut un vrai message
- Max {CHECK_MAX_TARGETS} cibles, et {CHECK_TARGET_COOLDOWN // 60} min de cooldown avant de re-checker la même personne""",
        inline=False
    )

    embed.add_field(
    name="🎲 !countercheck (!cc)",
    value=f"""
        **!countercheck**
- Déclenché uniquement si vous êtes ciblé par un !check en cours
- Renvoie la pression sur l'initiateur : le check initial se referme sans point
- L'initiateur a maintenant {COUNTERCHECK_WINDOW // 60} minutes pour répondre
- S'il répond : personne ne gagne ni ne perd rien
- S'il se tait : il perd {int(COUNTERCHECK_PENALTY_RATE * 100)} % de ses points (au moins {COUNTERCHECK_PENALTY_MIN})""",
        inline=False
    )
    
    embed.add_field(
        name="💪 !pompecheck",
        value=f"""- Demande a tout les utilisateurs en ligne de faire des pompes, toutes les personnes dans ce discord doivent le faire
- Le nombre dépend de vos points : {POMPES_TIERS[0][1]} pompes sous {POMPES_TIERS[0][0]} points, jusqu'à {POMPES_MAX} au-delà de {POMPES_TIERS[-1][0]}
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

    await ctx.send(embed=embed)

bot.run(token)
