"""Déroulement d'un défi : réponse en direct, expiration, survie au redémarrage."""
import asyncio

import config
import state
import storage
from conftest import SALON, FakeChannel, FakeMessage


def lancer(coro):
    return asyncio.run(coro)


def redemarrage():
    """Simule un redémarrage : aller-retour par la base, mémoire reconstruite.

    Les commandes en vol disparaissent ; seul l'état persisté subsiste.
    """
    dump = {}
    for user_id, data in state.user_data.items():
        document = storage.to_document(data)
        dump[user_id] = document

    state.user_data.clear()
    for user_id, document in dump.items():
        document["scarlanat_timers"] = storage._deserialize_timers(
            document.get("scarlanat_timers", {}))
        state.user_data[user_id] = document


# --------------------------------------------------------- réponse en direct
def test_la_cible_qui_repond_gagne_ses_points(challenges, salon, poser_check):
    poser_check('A', 'B')
    lancer(challenges.handle_message(FakeMessage('B', salon)))

    assert state.get_points('B') == config.CHECK_RESPONSE_REWARD
    assert "a répondu" in salon.envoyes[0]


def test_une_commande_ne_vaut_pas_reponse(challenges, salon, poser_check):
    poser_check('A', 'B')
    lancer(challenges.handle_message(FakeMessage('B', salon, est_commande=True)))

    assert state.get_points('B') == 0
    assert salon.envoyes == []


def test_une_reponse_dans_un_autre_salon_ne_compte_pas(challenges, poser_check):
    poser_check('A', 'B')
    lancer(challenges.handle_message(FakeMessage('B', FakeChannel(999))))
    assert state.get_points('B') == 0


def test_un_message_de_bot_est_ignore(challenges, salon, poser_check):
    poser_check('A', 'B')
    lancer(challenges.handle_message(FakeMessage('B', salon, bot=True)))
    assert state.get_points('B') == 0


def test_une_reponse_hors_delai_ne_rapporte_rien(challenges, salon, poser_check):
    poser_check('A', 'B', minutes_ecoulees=31)
    lancer(challenges.handle_message(FakeMessage('B', salon)))

    # C'est à la boucle de trancher, pas à ce message
    assert state.get_points('B') == 0


def test_un_message_ne_regle_que_ses_propres_timers(challenges, salon, poser_check):
    poser_check('A', 'B')
    poser_check('A', 'C')
    lancer(challenges.handle_message(FakeMessage('B', salon)))

    assert state.get_points('B') == config.CHECK_RESPONSE_REWARD
    assert state.get_points('C') == 0


# ------------------------------------------------------------- expirations
def test_l_expiration_est_reglee_par_la_boucle(challenges, poser_check):
    poser_check('A', 'B', minutes_ecoulees=31)
    lancer(challenges.process_timers())

    assert state.get_points('A') == config.CHECK_TIMEOUT_REWARD
    assert state.get_points('B') == -config.CHECK_TIMEOUT_PENALTY


def test_les_expirations_d_un_initiateur_tiennent_en_un_message(
        challenges, salon, poser_check):
    poser_check('A', 'B', minutes_ecoulees=31)
    poser_check('A', 'C', minutes_ecoulees=31)
    lancer(challenges.process_timers())

    assert len(salon.envoyes) == 1
    assert "<@B>" in salon.envoyes[0] and "<@C>" in salon.envoyes[0]
    assert state.get_points('A') == 2 * config.CHECK_TIMEOUT_REWARD


def test_la_boucle_ne_regle_pas_deux_fois(challenges, poser_check):
    poser_check('A', 'B', minutes_ecoulees=31)
    lancer(challenges.process_timers())
    apres_premier_tour = (state.get_points('A'), state.get_points('B'))

    lancer(challenges.process_timers())
    lancer(challenges.process_timers())

    assert (state.get_points('A'), state.get_points('B')) == apres_premier_tour
    assert state.user_data['A']["scarlanat_timers"] == {}


def test_le_plancher_tient_a_l_expiration(challenges, salon, poser_check):
    poser_check('A', 'B', minutes_ecoulees=31)
    state.add_points('B', -10_000)
    lancer(challenges.process_timers())

    assert state.get_points('B') == config.POINTS_FLOOR
    assert "plancher" in salon.envoyes[0]


def test_un_rappel_est_envoye_une_seule_fois(challenges, salon, poser_check):
    poser_check('A', 'B', minutes_ecoulees=26)   # il reste 4 min
    lancer(challenges.process_timers())
    lancer(challenges.process_timers())

    rappels = [envoye for envoye in salon.envoyes if "Il vous reste" in str(envoye)]
    assert len(rappels) == 1


def test_pas_de_rappel_trop_tot(challenges, salon, poser_check):
    poser_check('A', 'B', minutes_ecoulees=5)
    lancer(challenges.process_timers())
    assert salon.envoyes == []


# ------------------------------------------- survie au redémarrage du bot
def test_un_check_expire_se_regle_apres_un_redemarrage(challenges, poser_check):
    poser_check('A', 'B', minutes_ecoulees=31)
    redemarrage()                       # la commande !check n'existe plus
    lancer(challenges.process_timers())

    assert state.get_points('A') == config.CHECK_TIMEOUT_REWARD
    assert state.get_points('B') == -config.CHECK_TIMEOUT_PENALTY


def test_une_reponse_est_creditee_apres_un_redemarrage(challenges, salon, poser_check):
    poser_check('A', 'B', minutes_ecoulees=5)
    redemarrage()
    lancer(challenges.handle_message(FakeMessage('B', salon)))

    assert state.get_points('B') == config.CHECK_RESPONSE_REWARD


def test_le_redemarrage_ne_decale_pas_les_echeances(challenges, poser_check):
    """Le bug historique : MongoDB renvoie des dates UTC naïves, relocalisées
    en heure française, d'où un décalage de deux heures."""
    poser_check('A', 'B', minutes_ecoulees=29)
    redemarrage()
    lancer(challenges.process_timers())

    # Encore dans les temps : rien ne doit être réglé
    assert state.get_points('A') == 0
    assert state.get_points('B') == 0


# ------------------------------------------------------------ countercheck
def test_un_countercheck_ignore_coute_un_quart_des_points(
        challenges, poser_countercheck):
    state.add_points('A', 400)
    poser_countercheck('A', 'D', minutes_ecoulees=11)
    lancer(challenges.process_timers())

    assert state.get_points('A') == 300
    assert state.get_points('D') == 0      # la cible n'est jamais pénalisée


def test_un_countercheck_honore_ne_coute_rien(challenges, salon, poser_countercheck):
    state.add_points('A', 400)
    poser_countercheck('A', 'D', minutes_ecoulees=2)
    lancer(challenges.handle_message(FakeMessage('A', salon)))

    assert state.get_points('A') == 400
    assert state.get_points('D') == 0
    assert "échappe" in salon.envoyes[0]


def test_le_check_contre_attaque_ne_penalise_plus_la_cible(
        challenges, poser_check, poser_countercheck):
    """Après un !countercheck, le check initial est clos : la cible ne doit
    pas être pénalisée par la boucle pour être restée silencieuse."""
    timer = poser_check('A', 'B', minutes_ecoulees=5)
    state.mark_resolved(timer, "countered")
    poser_countercheck('A', 'B', minutes_ecoulees=5)

    lancer(challenges.process_timers())

    assert state.get_points('B') == 0
