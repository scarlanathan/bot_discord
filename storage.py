"""Persistance MongoDB de l'état du jeu.

La connexion n'est pas ouverte à l'import : `connect()` doit être appelé au
démarrage. Les tests peuvent donc importer ce module et remplacer
`users_collection` par un double, sans réseau.
"""
import copy
from datetime import datetime

import certifi
import pymongo  # type: ignore
from pymongo import MongoClient  # type: ignore
from pymongo.server_api import ServerApi  # type: ignore

import config
import state
from state import user_data

users_collection = None

# Dernier état écrit en base, pour n'envoyer que ce qui a réellement changé.
# Se fonder sur une comparaison plutôt que sur des appels explicites évite
# d'oublier de signaler une modification quelque part dans le code.
_last_saved = {}


def connect():
    """Ouvre la connexion MongoDB et mémorise la collection des joueurs."""
    global users_collection

    if not config.MONGODB_URI:
        raise ValueError("MONGODB_URI not set")

    client = MongoClient(
        config.MONGODB_URI,
        server_api=ServerApi('1'),
        tls=True,
        tlsCAFile=certifi.where(),
        retryWrites=True,
    )
    client.admin.command('ping')
    print("Connected successfully to MongoDB!")

    users_collection = client[config.DB_NAME][config.COLLECTION_NAME]
    return users_collection


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
            timer_info["start_time"] = state.to_aware(timer_info["start_time"])
        elif isinstance(timer_info, (str, datetime)):
            timer_info = state.to_aware(timer_info)
        restored[checked_user] = timer_info
    return restored


def to_document(data):
    """Version sérialisable d'un utilisateur, telle qu'elle part en base."""
    document = data.copy()
    document.pop('_id', None)  # champ immuable, jamais dans un $set
    if "scarlanat_timers" in document:
        document["scarlanat_timers"] = _serialize_timers(document["scarlanat_timers"])
    if "check_cooldowns" in document:
        document["check_cooldowns"] = {
            k: v.isoformat() if isinstance(v, datetime) else v
            for k, v in document["check_cooldowns"].items()
        }
    return document


def save_points():
    """Écrit les seuls utilisateurs modifiés, en une requête groupée."""
    if users_collection is None:
        print("⚠️ Sauvegarde ignorée : aucune connexion MongoDB.")
        return

    documents = {}
    operations = []

    for user_id, data in user_data.items():
        document = to_document(data)
        documents[user_id] = document
        if _last_saved.get(user_id) != document:
            operations.append(
                pymongo.UpdateOne({'_id': user_id}, {'$set': document}, upsert=True)
            )

    if not operations:
        return

    try:
        users_collection.bulk_write(operations, ordered=False)
    except pymongo.errors.PyMongoError as e:
        # `_last_saved` reste en arrière : la prochaine sauvegarde réessaiera
        print(f"Échec de la sauvegarde de {len(operations)} document(s) : {e}")
        return

    # Copie profonde : l'instantané ne doit plus bouger avec `user_data`
    _last_saved.clear()
    _last_saved.update({uid: copy.deepcopy(doc) for uid, doc in documents.items()})


def load_points():
    """Recharge l'état depuis la base, en place dans `state.user_data`."""
    try:
        documents = {doc['_id']: doc for doc in users_collection.find()}
    except pymongo.errors.PyMongoError as e:
        print(f"Failed to connect to MongoDB: {e}")
        user_data.clear()
        _last_saved.clear()
        return

    # `user_data` est modifié en place : les autres modules gardent leur référence
    user_data.clear()
    user_data.update(documents)

    # Ce qui vient d'être lu est, par définition, ce que contient la base
    _last_saved.clear()
    _last_saved.update({
        user_id: copy.deepcopy(to_document(data))
        for user_id, data in user_data.items()
    })

    # Reconvertir les timers en datetime et compléter les clés manquantes.
    # Ce qui est ajouté ici diffère de `_last_saved`, donc la première
    # sauvegarde persistera la migration, et elle seule.
    for user_id in list(user_data):
        entry = state.ensure_user(user_id)
        entry["scarlanat_timers"] = _deserialize_timers(entry["scarlanat_timers"])
        entry["check_cooldowns"] = {
            k: state.to_aware(v) for k, v in entry["check_cooldowns"].items()
        }
        # Appliquer le plancher aux soldes hérités d'avant son introduction
        entry["points"] = max(config.POINTS_FLOOR, entry["points"])
