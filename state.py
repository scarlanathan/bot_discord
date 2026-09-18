"""Logique du jeu, sans dépendance à Discord ni à MongoDB.

Tout ce qui décide qui gagne ou perd des points vit ici, et se teste donc sans
réseau. Les cogs se contentent de traduire ces décisions en messages Discord,
et `storage` de les persister.
"""
from datetime import datetime, timedelta

import pytz

import config

# État en mémoire, rempli par `storage.load_points`.
# Ce dictionnaire n'est jamais remplacé, seulement modifié en place : les autres
# modules en gardent une référence directe.
user_data = {}


# --------------------------------------------------------------- utilisateurs
def ensure_user(user_id):
    """Garantit qu'un utilisateur existe avec toutes ses clés, et le renvoie."""
    entry = user_data.setdefault(str(user_id), {})
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
    entry["points"] = max(config.POINTS_FLOOR, before + delta)
    return entry["points"] - before


def get_points(user_id):
    return user_data.get(str(user_id), {}).get("points", 0)


def now():
    return datetime.now(config.FRANCE_TZ)


def to_aware(value):
    """Normalise une date stockée (str ou datetime naïf) en datetime France."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = pytz.utc.localize(value).astimezone(config.FRANCE_TZ)
    return value.astimezone(config.FRANCE_TZ)


# ------------------------------------------------------------------ quotidien
def claim_daily(user_id, moment):
    """Applique la récompense quotidienne et fait avancer le streak.

    Renvoie (points gagnés, bonus de streak, streak), ou None si la récompense
    a déjà été réclamée aujourd'hui.
    """
    entry = ensure_user(user_id)
    aujourd_hui = moment.strftime("%Y-%m-%d")
    hier = (moment - timedelta(days=1)).strftime("%Y-%m-%d")

    if entry["last_streak_date"] == aujourd_hui:
        return None

    if entry["last_streak_date"] == hier:
        entry["streak"] += 1
    else:
        # Première utilisation, ou streak interrompu
        entry["streak"] = 1

    bonus = min(entry["streak"] * config.DAILY_STREAK_BONUS,
                config.DAILY_STREAK_BONUS_MAX)
    total = config.DAILY_BASE_POINTS + bonus
    add_points(user_id, total)
    entry["last_streak_date"] = aujourd_hui
    return total, bonus, entry["streak"]


# --------------------------------------------------------------------- timers
def timer_window(timer_type):
    """Délai accordé selon le type de timer."""
    return config.COUNTERCHECK_WINDOW if timer_type == "countercheck" else config.CHECK_WINDOW


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

    C'est la seule source de vérité sur les défis en cours : elle est persistée
    en base, donc elle survit à un redémarrage du bot.
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


def time_left(timer_info, timer_type, moment):
    """Secondes restantes avant l'échéance ; négatif si le délai est dépassé."""
    elapsed = (moment - to_aware(timer_info["start_time"])).total_seconds()
    return timer_window(timer_type) - elapsed


def purge_resolved_timers(moment):
    """Efface les timers clos, une fois passée la marge de sécurité."""
    purged = False
    for data in list(user_data.values()):
        timers = data.get("scarlanat_timers", {})
        for key, timer_info in list(timers.items()):
            if not isinstance(timer_info, dict) or "start_time" not in timer_info:
                del timers[key]
                purged = True
                continue

            age = (moment - to_aware(timer_info["start_time"])).total_seconds()
            if is_resolved(timer_info) and age > config.TIMER_CLEANUP_GRACE:
                del timers[key]
                purged = True
    return purged


# ---------------------------------------------------------------------- check
def valid_checks_against(user_id, moment):
    """Les checks en cours, encore dans les temps, qui ciblent `user_id`."""
    valides = []
    for initiator_id, key, timer_info, timer_type, _ in pending_timers():
        if key != str(user_id) or timer_type != "initial_check":
            continue
        if time_left(timer_info, timer_type, moment) >= 0:
            valides.append({'initiator': initiator_id, 'timer_info': timer_info})
    return valides


def split_by_cooldown(initiator_id, target_ids, moment):
    """Sépare les cibles disponibles de celles encore sous cooldown.

    Renvoie (disponibles, [(cible, minutes restantes)]).
    """
    entry = ensure_user(initiator_id)
    disponibles = []
    en_cooldown = []

    for target_id in target_ids:
        dernier = entry["check_cooldowns"].get(str(target_id))
        if dernier is not None:
            elapsed = (moment - to_aware(dernier)).total_seconds()
            if elapsed < config.CHECK_TARGET_COOLDOWN:
                restant = config.CHECK_TARGET_COOLDOWN - elapsed
                en_cooldown.append((target_id, int(restant // 60) + 1))
                continue
        disponibles.append(target_id)

    return disponibles, en_cooldown


def register_check(initiator_id, target_id, moment, channel_id):
    """Pose le timer et le cooldown d'un check. Renvoie le timer créé."""
    entry = ensure_user(initiator_id)
    target_id = str(target_id)
    ensure_user(target_id)
    entry["check_cooldowns"][target_id] = moment

    # Ne pas écraser un défi déjà en cours sur la même cible
    if target_id not in entry["scarlanat_timers"]:
        entry["scarlanat_timers"][target_id] = {
            "start_time": moment,
            "initiator": str(initiator_id),
            "type": "initial_check",
            "channel_id": channel_id,
            "resolved": False,
            "responded": False,
        }
    return entry["scarlanat_timers"][target_id]


def register_countercheck(initiator_id, target_id, moment, channel_id):
    """Le contre-défi remplace le check initial, sur la même clé."""
    entry = ensure_user(initiator_id)
    entry["scarlanat_timers"][str(target_id)] = {
        "start_time": moment,
        "initiator": str(initiator_id),
        "type": "countercheck",
        "channel_id": channel_id,
        "resolved": False,
        "status": "waiting_response",
    }
    return entry["scarlanat_timers"][str(target_id)]


# ------------------------------------------------------ dénouements chiffrés
def reward_response(target_id, timer_info, moment):
    """La cible a donné signe de vie : elle encaisse sa récompense."""
    gained = add_points(target_id, config.CHECK_RESPONSE_REWARD)
    ensure_user(target_id)["last_claim"] = moment.strftime("%Y-%m-%d")
    mark_resolved(timer_info, "responded")
    return gained


def expire_check(initiator_id, target_id, timer_info):
    """Personne n'a répondu : l'initiateur encaisse, la cible paie.

    Renvoie (gain de l'initiateur, perte réelle de la cible).
    """
    earned = add_points(initiator_id, config.CHECK_TIMEOUT_REWARD)
    lost = -add_points(target_id, -config.CHECK_TIMEOUT_PENALTY)
    mark_resolved(timer_info, "expired")
    return earned, lost


def expire_countercheck(initiator_id, timer_info):
    """Contre-défi ignoré : pénalité proportionnelle, avec un minimum.

    Renvoie (points avant, perte réelle).
    """
    before = get_points(initiator_id)
    penalite = max(config.COUNTERCHECK_PENALTY_MIN,
                   int(before * config.COUNTERCHECK_PENALTY_RATE))
    lost = -add_points(initiator_id, -penalite)
    mark_resolved(timer_info, "expired")
    return before, lost


def reset_all():
    """Remet points, streaks, timers et cooldowns de tout le monde à zéro."""
    for user_id in list(user_data):
        entry = ensure_user(user_id)
        entry["points"] = 0
        entry["streak"] = 0
        entry["last_streak_date"] = None
        entry["scarlanat_timers"] = {}
        entry["check_cooldowns"] = {}


# ----------------------------------------------------------------- pompecheck
def pompes_for(points):
    """Nombre de pompes dû, selon les paliers configurés."""
    for seuil, nombre in config.POMPES_TIERS:
        if points < seuil:
            return nombre
    return config.POMPES_MAX
