"""Règles du jeu : points, plancher, streak, cooldowns, timers."""
from datetime import timedelta

import config
import state


# ------------------------------------------------------------------- points
def test_utilisateur_inconnu_est_cree_avec_toutes_ses_cles():
    entry = state.ensure_user('42')
    assert set(entry) == {"points", "streak", "last_streak_date", "last_claim",
                          "scarlanat_timers", "check_cooldowns"}
    assert entry["points"] == 0


def test_add_points_cree_l_utilisateur_au_besoin():
    assert state.add_points('42', 15) == 15
    assert state.get_points('42') == 15


def test_les_points_ne_descendent_pas_sous_le_plancher():
    state.add_points('42', 15)
    applique = state.add_points('42', -1000)

    assert state.get_points('42') == config.POINTS_FLOOR
    # La variation renvoyée est celle réellement appliquée, pas -1000
    assert applique == config.POINTS_FLOOR - 15


def test_au_plancher_une_perte_supplementaire_ne_change_rien():
    state.add_points('42', -1000)
    assert state.add_points('42', -50) == 0
    assert state.get_points('42') == config.POINTS_FLOOR


def test_les_identifiants_numeriques_et_texte_designent_le_meme_joueur():
    state.add_points(42, 10)
    assert state.get_points('42') == 10


# ------------------------------------------------------------------ streak
def test_premiere_reclamation_demarre_le_streak_a_un():
    total, bonus, streak = state.claim_daily('A', state.now())
    assert streak == 1
    assert bonus == config.DAILY_STREAK_BONUS
    assert total == config.DAILY_BASE_POINTS + bonus


def test_deux_reclamations_le_meme_jour_sont_refusees():
    moment = state.now()
    state.claim_daily('A', moment)
    assert state.claim_daily('A', moment) is None


def test_reclamation_la_veille_prolonge_le_streak():
    moment = state.now()
    state.claim_daily('A', moment - timedelta(days=1))
    _, _, streak = state.claim_daily('A', moment)
    assert streak == 2


def test_un_jour_manque_remet_le_streak_a_un():
    moment = state.now()
    state.claim_daily('A', moment - timedelta(days=3))
    _, _, streak = state.claim_daily('A', moment)
    assert streak == 1


def test_le_bonus_de_streak_est_plafonne():
    moment = state.now()
    for jour in range(20, 0, -1):
        state.claim_daily('A', moment - timedelta(days=jour))
    _, bonus, _ = state.claim_daily('A', moment)
    assert bonus == config.DAILY_STREAK_BONUS_MAX


# --------------------------------------------------------------- cooldowns
def test_une_cible_fraichement_checkee_est_en_cooldown(poser_check):
    poser_check('A', 'B')
    disponibles, en_cooldown = state.split_by_cooldown('A', ['B'], state.now())

    assert disponibles == []
    assert en_cooldown[0][0] == 'B'
    assert en_cooldown[0][1] <= config.CHECK_TARGET_COOLDOWN // 60 + 1


def test_le_cooldown_expire(poser_check):
    poser_check('A', 'B')
    plus_tard = state.now() + timedelta(seconds=config.CHECK_TARGET_COOLDOWN + 1)
    disponibles, en_cooldown = state.split_by_cooldown('A', ['B'], plus_tard)

    assert disponibles == ['B']
    assert en_cooldown == []


def test_le_cooldown_est_propre_a_chaque_initiateur(poser_check):
    poser_check('A', 'B')
    disponibles, _ = state.split_by_cooldown('C', ['B'], state.now())
    assert disponibles == ['B']


# ------------------------------------------------------------------ timers
def test_un_timer_clos_n_est_plus_en_attente(poser_check):
    timer = poser_check('A', 'B')
    assert len(list(state.pending_timers())) == 1

    state.mark_resolved(timer, "responded")
    assert list(state.pending_timers()) == []


def test_l_ancien_format_de_timer_est_reconnu_comme_clos(poser_check):
    timer = poser_check('A', 'B')
    del timer["resolved"]
    timer["responded"] = True
    assert list(state.pending_timers()) == []


def test_sur_un_countercheck_c_est_l_initiateur_qui_est_attendu(poser_countercheck):
    poser_countercheck('A', 'D')
    _, _, _, timer_type, waiting_id = next(state.pending_timers())
    assert timer_type == "countercheck"
    assert waiting_id == 'A'


def test_check_valide_tant_que_le_delai_court(poser_check):
    poser_check('A', 'B', minutes_ecoulees=29)
    assert len(state.valid_checks_against('B', state.now())) == 1


def test_check_expire_n_est_plus_contre_attaquable(poser_check):
    poser_check('A', 'B', minutes_ecoulees=31)
    assert state.valid_checks_against('B', state.now()) == []


def test_check_deja_repondu_n_est_plus_contre_attaquable(poser_check):
    timer = poser_check('A', 'B')
    state.mark_resolved(timer, "responded")
    assert state.valid_checks_against('B', state.now()) == []


def test_les_timers_clos_sont_purges_apres_la_marge(poser_check):
    timer = poser_check('A', 'B', minutes_ecoulees=31)
    state.mark_resolved(timer, "expired")

    assert state.purge_resolved_timers(state.now()) is True
    assert state.user_data['A']["scarlanat_timers"] == {}


def test_un_timer_clos_reste_pendant_la_marge(poser_check):
    timer = poser_check('A', 'B')
    state.mark_resolved(timer, "responded")

    assert state.purge_resolved_timers(state.now()) is False
    assert 'B' in state.user_data['A']["scarlanat_timers"]


def test_un_timer_en_cours_n_est_jamais_purge(poser_check):
    poser_check('A', 'B', minutes_ecoulees=29)
    assert state.purge_resolved_timers(state.now()) is False


def test_un_second_check_n_ecrase_pas_le_defi_en_cours(poser_check):
    premier = poser_check('A', 'B', minutes_ecoulees=5)
    encore = state.register_check('A', 'B', state.now(), 999)
    assert encore is premier


# ------------------------------------------------------------- dénouements
def test_expiration_recompense_l_initiateur_et_penalise_la_cible(poser_check):
    timer = poser_check('A', 'B', minutes_ecoulees=31)
    earned, lost = state.expire_check('A', 'B', timer)

    assert earned == config.CHECK_TIMEOUT_REWARD
    assert lost == config.CHECK_TIMEOUT_PENALTY
    assert state.get_points('B') == -config.CHECK_TIMEOUT_PENALTY
    assert state.is_resolved(timer)


def test_expiration_contre_une_cible_au_plancher_ne_retire_rien(poser_check):
    timer = poser_check('A', 'B', minutes_ecoulees=31)
    state.add_points('B', -10_000)
    _, lost = state.expire_check('A', 'B', timer)
    assert lost == 0


def test_penalite_de_countercheck_proportionnelle(poser_countercheck):
    state.add_points('A', 400)
    timer = poser_countercheck('A', 'D', minutes_ecoulees=11)
    before, lost = state.expire_countercheck('A', timer)

    assert before == 400
    assert lost == 100                  # 25 % de 400
    assert state.get_points('A') == 300


def test_penalite_de_countercheck_plancher_minimum(poser_countercheck):
    state.add_points('A', 40)
    timer = poser_countercheck('A', 'D', minutes_ecoulees=11)
    _, lost = state.expire_countercheck('A', timer)

    # 25 % de 40 = 10, mais le minimum s'applique
    assert lost == config.COUNTERCHECK_PENALTY_MIN


def test_reponse_recompense_la_cible(poser_check):
    timer = poser_check('A', 'B')
    gained = state.reward_response('B', timer, state.now())

    assert gained == config.CHECK_RESPONSE_REWARD
    assert state.is_resolved(timer)


def test_reset_efface_points_streaks_et_timers(poser_check):
    poser_check('A', 'B')
    state.add_points('A', 500)
    state.claim_daily('A', state.now())

    state.reset_all()

    assert state.get_points('A') == 0
    assert state.user_data['A']["streak"] == 0
    assert state.user_data['A']["scarlanat_timers"] == {}
    assert state.user_data['A']["check_cooldowns"] == {}


# -------------------------------------------------------------- pompecheck
def test_paliers_de_pompes():
    assert [state.pompes_for(p) for p in (0, 599, 600, 1499, 1500, 2999, 3000, 9999)] \
        == [10, 10, 20, 20, 30, 30, 40, 40]


# -------------------------------------------------------------------- dates
def test_un_datetime_naif_est_lu_comme_de_l_utc():
    """C'est sous cette forme que MongoDB renvoie les dates."""
    moment = state.now()
    naif = moment.astimezone(state.pytz.utc).replace(tzinfo=None)
    assert abs((state.to_aware(naif) - moment).total_seconds()) < 0.001


def test_une_date_iso_est_relue_a_l_identique():
    moment = state.now()
    assert abs((state.to_aware(moment.isoformat()) - moment).total_seconds()) < 0.001
