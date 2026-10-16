"""!check, !countercheck, et les deux mécanismes qui les concluent.

Le défi ne vit pas dans la commande qui l'a lancé : celle-ci enregistre un
timer en base puis rend la main. La suite est prise en charge par le listener
de messages et par la boucle de fond, qui relisent cet état persisté — c'est ce
qui permet à un check de se conclure après un redémarrage du bot.
"""
import discord  # type: ignore
from discord.ext import commands, tasks  # type: ignore

import config
import state
import storage


class Challenges(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.watch_timers.start()

    async def cog_unload(self):
        self.watch_timers.cancel()

    # ------------------------------------------------------------- utilitaires
    async def announce(self, timer_info, text):
        """Publie le dénouement d'un timer dans le salon où il a été lancé."""
        channel = self.bot.get_channel(timer_info.get("channel_id") or 0)
        if channel is None and config.CHANNEL_ID:
            channel = self.bot.get_channel(config.CHANNEL_ID)
        if channel is None:
            print(f"Aucun salon disponible pour annoncer : {text}")
            return
        await channel.send(text)

    async def notify(self, timer_info, user_id, timer_type, seconds_left):
        """Prévient un joueur de l'échéance, dans le salon où le défi a été lancé."""
        minutes = max(1, round(seconds_left / 60))
        if timer_type == "countercheck":
            message = (f"⏱️ Il vous reste environ {minutes} min pour répondre au countercheck, "
                       f"sinon vous perdez {int(config.COUNTERCHECK_PENALTY_RATE * 100)} % "
                       f"de vos points (au moins {config.COUNTERCHECK_PENALTY_MIN}).")
        else:
            message = (f"⏱️ Il vous reste environ {minutes} min pour répondre au check, "
                       f"sinon vous perdez {config.CHECK_TIMEOUT_PENALTY} points.")

        await self.announce(timer_info, f"<@{user_id}> {message}")

    # -------------------------------------------------------------- règlements
    async def settle_expirations(self, expirations):
        """Règle les timers échus. Les checks d'un même initiateur sont
        regroupés en un seul message : après une longue coupure, la boucle en
        rattrape potentiellement plusieurs d'un coup."""
        par_initiateur = {}
        for initiator_id, key, timer_info, timer_type in expirations:
            if timer_type == "countercheck":
                before, lost = state.expire_countercheck(initiator_id, timer_info)
                await self.announce(
                    timer_info,
                    f"❌ <@{initiator_id}> n'a pas répondu au countercheck et perd "
                    f"{lost} points ! ({before} → {state.get_points(initiator_id)})")
            else:
                par_initiateur.setdefault(initiator_id, []).append((key, timer_info))

        for initiator_id, checks in par_initiateur.items():
            gains = 0
            lignes = []
            for target_id, timer_info in checks:
                earned, lost = state.expire_check(initiator_id, target_id, timer_info)
                gains += earned
                if lost:
                    lignes.append(f"<@{target_id}> perd {lost} points "
                                  f"(total : {state.get_points(target_id)})")
                else:
                    lignes.append(f"<@{target_id}> est déjà au plancher "
                                  f"de {config.POINTS_FLOOR} points")

            await self.announce(
                checks[0][1],
                "⏱️ Temps écoulé, personne n'a répondu.\n"
                f"🎉 <@{initiator_id}> gagne {gains} points "
                f"(total : {state.get_points(initiator_id)})\n" + "\n".join(lignes))

    # -------------------------------------------------------- boucle de fond
    async def process_timers(self):
        """Un tour de surveillance : rappels, échéances, purge.

        Méthode à part pour être appelable directement par les tests.
        """
        moment = state.now()
        dirty = False
        expirations = []

        for initiator_id, key, timer_info, timer_type, waiting_id in state.pending_timers():
            seconds_left = state.time_left(timer_info, timer_type, moment)

            if seconds_left <= 0:
                expirations.append((initiator_id, key, timer_info, timer_type))
                continue

            # Un seul rappel, à l'approche de l'échéance
            if seconds_left <= config.TIMER_REMINDER_BEFORE and not timer_info.get("reminded"):
                timer_info["reminded"] = True
                dirty = True
                await self.notify(timer_info, waiting_id, timer_type, seconds_left)

        if expirations:
            await self.settle_expirations(expirations)
            dirty = True

        if state.purge_resolved_timers(moment):
            dirty = True

        if dirty:
            storage.save_points()

    @tasks.loop(seconds=config.TIMER_CHECK_INTERVAL)
    async def watch_timers(self):
        await self.process_timers()

    @watch_timers.before_loop
    async def before_watch_timers(self):
        await self.bot.wait_until_ready()

    @watch_timers.error
    async def watch_timers_error(self, error):
        # Une exception non gérée arrêterait définitivement la boucle en silence
        print(f"Erreur dans la surveillance des timers : {error}")
        if not self.watch_timers.is_running():
            self.watch_timers.start()

    # ------------------------------------------------------ réponses en direct
    async def handle_message(self, message):
        """Règle les timers que ce message vient satisfaire.

        Méthode à part pour être appelable directement par les tests.
        """
        if message.author.bot:
            return

        # Une commande n'est pas une réponse, sinon !points rapporterait des points
        context = await self.bot.get_context(message)
        if context.valid:
            return

        author_id = str(message.author.id)
        moment = state.now()
        resolved = False

        for initiator_id, key, timer_info, timer_type, waiting_id in state.pending_timers():
            if waiting_id != author_id:
                continue

            # Le défi se joue dans le salon où il a été lancé
            channel_id = timer_info.get("channel_id")
            if channel_id and message.channel.id != channel_id:
                continue

            # Déjà expiré : c'est à la boucle de trancher, pas à ce message
            if state.time_left(timer_info, timer_type, moment) < 0:
                continue

            if timer_type == "countercheck":
                state.mark_resolved(timer_info, "responded")
                await self.announce(
                    timer_info,
                    f"🎉 <@{initiator_id}> a répondu à temps, il échappe à la pénalité !")
            else:
                gained = state.reward_response(key, timer_info, moment)
                await self.announce(
                    timer_info,
                    f"🎉 <@{key}> a répondu ! Il gagne {gained} points ! "
                    f"Total : {state.get_points(key)} points")
            resolved = True

        if resolved:
            storage.save_points()

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.handle_message(message)

    # ------------------------------------------------------------- commandes
    @commands.command(name="check")
    async def check(self, ctx, *mentions: discord.Member):
        user_id = str(ctx.author.id)

        if state.get_points(user_id) < 0:
            await ctx.send(f"❌ {ctx.author.mention}, vous ne pouvez pas utiliser "
                           "cette commande car vous avez des points négatifs.")
            return

        if self.bot.user in mentions:
            await ctx.send("❌ Vous ne pouvez pas check le bot !")
            return

        moment = state.now()
        minutes_du_jour = moment.hour * 60 + moment.minute
        if not config.CHECK_START_MINUTES <= minutes_du_jour < config.CHECK_END_MINUTES:
            await ctx.send("❌ Vous ne pouvez utiliser cette commande qu'entre 5h30 "
                           f"et 14h. Heure actuelle : {moment.strftime('%H:%M')}")
            return

        if ctx.author in mentions:
            await ctx.send("❌ Vous ne pouvez pas vous check vous-même !")
            return

        state.ensure_user(user_id)

        # Dédoublonner les mentions en gardant l'ordre, et écarter les bots
        cibles = []
        for user in mentions:
            if user.bot or user in cibles:
                continue
            cibles.append(user)

        # Anti-farm : on ne peut pas re-checker la même personne en boucle
        par_id = {str(user.id): user for user in cibles}
        disponibles, en_cooldown = state.split_by_cooldown(user_id, list(par_id), moment)

        if en_cooldown:
            details = ", ".join(f"{par_id[tid].mention} (encore {minutes} min)"
                                for tid, minutes in en_cooldown)
            await ctx.send(f"⏳ Vous avez déjà check récemment : {details}")

        if not disponibles:
            return

        # Anti-farm : plafonner le nombre de cibles d'un même check
        if len(disponibles) > config.CHECK_MAX_TARGETS:
            await ctx.send(f"⚠️ Maximum {config.CHECK_MAX_TARGETS} cibles par check : "
                           f"seules les {config.CHECK_MAX_TARGETS} premières sont retenues.")
            disponibles = disponibles[:config.CHECK_MAX_TARGETS]

        for target_id in disponibles:
            state.register_check(user_id, target_id, moment, ctx.channel.id)

        mentions_texte = " ".join(par_id[tid].mention for tid in disponibles)
        await ctx.send(
            f"Un compteur de {config.CHECK_WINDOW // 60} minutes a été lancé pour "
            f"{mentions_texte}.\nRépondre = +{config.CHECK_RESPONSE_REWARD} points, "
            f"se taire = -{config.CHECK_TIMEOUT_PENALTY} points.")

        # La commande s'arrête ici : le listener et la boucle concluent le défi
        storage.save_points()

    @commands.command(name="countercheck", aliases=["cc"])
    async def countercheck(self, ctx):
        user_id = str(ctx.author.id)
        moment = state.now()
        state.ensure_user(user_id)

        valid_checks = state.valid_checks_against(user_id, moment)
        if not valid_checks:
            await ctx.send("❌ Vous n'êtes pas actuellement dans un check valide.")
            return

        for check in valid_checks:
            initiator_id = check['initiator']

            # Le check initial se referme sans gain ni perte : plutôt que de
            # répondre, la cible a renvoyé la pression sur l'initiateur.
            state.mark_resolved(check['timer_info'], "countered")
            state.register_countercheck(initiator_id, user_id, moment, ctx.channel.id)

            await ctx.send(
                f"<@{initiator_id}> a maintenant {config.COUNTERCHECK_WINDOW // 60} "
                f"minutes pour répondre, sinon il perd "
                f"{int(config.COUNTERCHECK_PENALTY_RATE * 100)} % de ses points "
                f"(au moins {config.COUNTERCHECK_PENALTY_MIN}) !")

        storage.save_points()


async def setup(bot):
    await bot.add_cog(Challenges(bot))
