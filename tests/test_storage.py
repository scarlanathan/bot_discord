"""Persistance : n'écrire que ce qui change, et ne rien perdre en chemin."""
from datetime import datetime

import config
import state
import storage
from conftest import FakeCollection


def ecritures(collection):
    """Ids écrits lors du dernier bulk_write, ou None si aucun appel."""
    return collection.appels[-1] if collection.appels else None


def document_complet(user_id, **extra):
    document = {'_id': user_id, 'points': 0, 'streak': 0, 'last_streak_date': None,
                'last_claim': None, 'scarlanat_timers': {}, 'check_cooldowns': {}}
    document.update(extra)
    return document


# ------------------------------------------------------------- écritures
def test_les_nouveaux_joueurs_partent_en_une_seule_requete():
    collection = storage.users_collection = FakeCollection()
    for user_id in 'ABC':
        state.ensure_user(user_id)

    storage.save_points()

    assert sorted(ecritures(collection)) == ['A', 'B', 'C']
    assert len(collection.appels) == 1


def test_sans_modification_aucune_requete():
    collection = storage.users_collection = FakeCollection()
    state.ensure_user('A')
    storage.save_points()
    collection.appels.clear()

    storage.save_points()
    storage.save_points()

    assert collection.appels == []


def test_un_seul_joueur_modifie_un_seul_document_ecrit():
    collection = storage.users_collection = FakeCollection()
    for user_id in 'ABC':
        state.ensure_user(user_id)
    storage.save_points()
    collection.appels.clear()

    state.add_points('B', 25)
    storage.save_points()

    assert ecritures(collection) == ['B']


def test_une_modification_imbriquee_est_detectee(poser_check):
    collection = storage.users_collection = FakeCollection()
    state.ensure_user('A')
    storage.save_points()
    collection.appels.clear()

    poser_check('A', 'B')
    storage.save_points()

    assert 'A' in ecritures(collection)


def test_un_champ_de_timer_modifie_est_detecte(poser_check):
    """Sans copie profonde de l'instantané, cette modification passerait
    inaperçue : il pointerait sur le même dict que `user_data`."""
    collection = storage.users_collection = FakeCollection()
    timer = poser_check('A', 'B')
    storage.save_points()
    collection.appels.clear()

    timer["resolved"] = True
    storage.save_points()

    assert ecritures(collection) == ['A']


def test_un_echec_reseau_n_efface_pas_la_modification():
    collection = storage.users_collection = FakeCollection()
    state.ensure_user('C')
    storage.save_points()
    collection.appels.clear()

    state.add_points('C', 10)
    collection.echouer = True
    storage.save_points()
    assert collection.appels == []

    collection.echouer = False
    storage.save_points()
    assert ecritures(collection) == ['C']


# ------------------------------------------------------------ chargement
def test_recharger_puis_sauvegarder_n_ecrit_rien():
    collection = storage.users_collection = FakeCollection([document_complet('X', points=100)])
    storage.load_points()
    storage.save_points()

    assert collection.appels == []


def test_un_document_incomplet_est_migre_puis_ecrit_une_fois():
    collection = storage.users_collection = FakeCollection([
        {'_id': 'Y', 'points': 100},        # ancien format, clés manquantes
        document_complet('Z', points=50),
    ])
    storage.load_points()
    storage.save_points()
    assert ecritures(collection) == ['Y']

    storage.save_points()
    assert len(collection.appels) == 1      # pas réécrit en boucle


def test_le_plancher_est_applique_au_chargement():
    collection = storage.users_collection = FakeCollection([
        document_complet('W', points=-5000)])
    storage.load_points()

    assert state.get_points('W') == config.POINTS_FLOOR
    storage.save_points()
    assert ecritures(collection) == ['W']


def test_le_chargement_ne_remplace_pas_le_dictionnaire_partage():
    """Les autres modules gardent une référence directe sur `state.user_data`."""
    reference = state.user_data
    storage.users_collection = FakeCollection([document_complet('X')])
    storage.load_points()

    assert state.user_data is reference
    assert 'X' in reference


# ---------------------------------------------------------- sérialisation
def test_les_dates_de_timer_font_l_aller_retour(poser_check):
    timer = poser_check('A', 'B')
    depart = timer["start_time"]

    document = storage.to_document(state.user_data['A'])
    assert isinstance(document["scarlanat_timers"]['B']["start_time"], str)

    restaure = storage._deserialize_timers(document["scarlanat_timers"])
    assert abs((restaure['B']["start_time"] - depart).total_seconds()) < 0.001


def test_les_cooldowns_font_l_aller_retour(poser_check):
    poser_check('A', 'B')
    document = storage.to_document(state.user_data['A'])

    valeur = document["check_cooldowns"]['B']
    assert isinstance(valeur, str)
    assert abs((state.to_aware(valeur)
                - state.user_data['A']["check_cooldowns"]['B']).total_seconds()) < 0.001


def test_l_identifiant_mongo_n_est_jamais_dans_le_set():
    """`_id` est immuable : l'inclure ferait échouer la mise à jour."""
    state.ensure_user('A')['_id'] = 'A'
    assert '_id' not in storage.to_document(state.user_data['A'])


def test_sans_connexion_la_sauvegarde_ne_plante_pas():
    storage.users_collection = None
    state.add_points('A', 10)
    storage.save_points()          # ne doit pas lever


def test_un_document_sans_timers_se_serialise(poser_check):
    document = storage.to_document({'points': 10})
    assert document == {'points': 10}
