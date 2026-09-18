# Bot Discord

Bot Discord de points et de défis entre membres d'un serveur (`!check`,
`!countercheck`, `!pompecheck`, `!watercheck`, streak quotidien, classement).
Les données sont stockées dans MongoDB, et un petit serveur Flask (`keep_alive.py`)
expose une page HTTP pour garder le bot éveillé sur un hébergeur gratuit.

## Prérequis

- Python 3.10 ou plus (testé avec 3.13)
- Une base MongoDB accessible (MongoDB Atlas par exemple)
- Une application Discord avec un bot et son token
  ([Discord Developer Portal](https://discord.com/developers/applications))

Dans le portail Discord, activez les **Privileged Gateway Intents** :
`PRESENCE INTENT`, `SERVER MEMBERS INTENT` et `MESSAGE CONTENT INTENT`.
Le bot utilise `discord.Intents.all()` et ne démarrera pas sans elles.

## Installation

```bash
git clone <url-du-repo>
cd bot_discord

# Environnement virtuel (recommandé)
python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

Créez un fichier `.env` à la racine du projet (il est ignoré par git) :

```env
DISCORD_TOKEN=le_token_de_votre_bot
MONGODB_URI=mongodb+srv://utilisateur:motdepasse@cluster.mongodb.net/?retryWrites=true&w=majority
GUILD_ID=123456789012345678
CHANNEL_ID=123456789012345678
```

| Variable | Obligatoire | Rôle |
| --- | --- | --- |
| `DISCORD_TOKEN` | oui | Token du bot Discord |
| `MONGODB_URI` | oui | URI de connexion MongoDB (le bot lève une erreur si absente) |
| `GUILD_ID` | oui | ID du serveur Discord utilisé par les timers |
| `CHANNEL_ID` | oui | ID du salon de repli pour les notifications de timer |
| `PORT` | non | Port du serveur Flask (`8080` par défaut) |

Le bot écrit dans la base `nathan`, collection `users`.

Pour récupérer un ID Discord : activez le **Mode développeur**
(Paramètres → Avancés), puis clic droit sur le serveur ou le salon → *Copier l'identifiant*.

## Lancer le projet

```bash
python main.py
```

Au démarrage vous devez voir :

```
Connected successfully to MongoDB!
<NomDuBot#1234> est connecté !
```

Le serveur Flask démarre en parallèle sur `http://localhost:8080` et répond
`Le bot est en ligne!` — utile pour un service de monitoring (UptimeRobot, etc.).

Pour arrêter le bot : `Ctrl + C`.

## Déploiement

Le projet est prêt pour un hébergeur type Render / Railway / Replit :

- **Build command** : `pip install -r requirements.txt`
- **Start command** : `python main.py`
- Renseignez `DISCORD_TOKEN`, `MONGODB_URI`, `GUILD_ID` et `CHANNEL_ID` dans les
  variables d'environnement du service (pas de fichier `.env` en production).
- Le port est lu depuis `$PORT`, fourni automatiquement par l'hébergeur.

## Commandes du bot

Préfixe : `!`

| Commande | Description |
| --- | --- |
| `!wassimchien` (`!wc`) | Points quotidiens : 60 points + bonus de streak (max +90) |
| `!check @utilisateur` | Lance un défi ; la cible a 30 min pour répondre |
| `!countercheck` (`!cc`) | Contre-défi quand on est ciblé ; l'initiateur a 10 min |
| `!pompecheck` | Demande des pompes à tous les membres en ligne (selon leurs points) |
| `!watercheck` | Demande à tout le monde de boire une gorgée d'eau |
| `!points` | Affiche vos points, votre streak et vos éventuels malus |
| `!classement` | Top 10 des joueurs |
| `!reset` | Remet points, streaks et timers à zéro (réservé au propriétaire du bot) |
| `!help` | Affiche l'aide dans Discord |

## Système de points

Toutes les valeurs d'équilibrage sont regroupées dans un bloc de constantes en
haut de `main.py` : il n'y a rien d'autre à modifier dans le fichier pour
retoucher le jeu.

| Constante | Valeur | Effet |
| --- | --- | --- |
| `DAILY_BASE_POINTS` | 60 | Points de base du `!wc` quotidien |
| `DAILY_STREAK_BONUS` | 10 | Bonus par jour consécutif |
| `DAILY_STREAK_BONUS_MAX` | 90 | Plafond du bonus (atteint au 9e jour) |
| `CHECK_WINDOW` | 1800 s | Délai laissé à la cible d'un `!check` |
| `CHECK_RESPONSE_REWARD` | 15 | Gagné par la cible qui répond |
| `CHECK_TIMEOUT_REWARD` | 25 | Gagné par l'initiateur, par cible silencieuse |
| `CHECK_TIMEOUT_PENALTY` | 20 | Perdu par une cible qui n'a pas répondu |
| `CHECK_MAX_TARGETS` | 5 | Cibles maximum par `!check` |
| `CHECK_TARGET_COOLDOWN` | 3600 s | Délai avant de re-checker la même personne |
| `COUNTERCHECK_WINDOW` | 600 s | Délai laissé à l'initiateur contre-attaqué |
| `COUNTERCHECK_PENALTY_RATE` | 0.25 | Part des points perdus s'il ne répond pas |
| `COUNTERCHECK_PENALTY_MIN` | 50 | Perte plancher du `!countercheck` |
| `POINTS_FLOOR` | -100 | Plancher global : les points ne descendent pas plus bas |
| `POMPES_TIERS` | 600/1500/3000 | Seuils de points → 10/20/30 pompes (40 au-delà) |
| `TIMER_REMINDER_BEFORE` | 300 s | Rappel en MP avant l'expiration d'un timer |
| `TIMER_CHECK_INTERVAL` | 30 s | Fréquence de la boucle de surveillance |

Règles annexes :

- Un `!check` ne peut être lancé qu'entre 5h30 et 14h (heure de Paris), et pas
  par un joueur dont le solde est négatif.
- Le compte à rebours d'un `!check` est commun à toutes les cibles : une réponse
  ne relance pas le délai des autres.
- Le `!countercheck` n'est valide que sur un `!check` en cours auquel vous
  n'avez pas encore répondu.
- Une tâche de fond prévient en MP (ou sur `CHANNEL_ID` si les MP sont fermés)
  5 minutes avant l'expiration d'un timer, une seule fois par timer, et purge
  les timers laissés par un redémarrage du bot. Elle ne modifie jamais les
  points elle-même : c'est la commande qui attribue gains et pénalités.
- `GUILD_ID` doit être un ID valide pour que les rappels fonctionnent ; sinon le
  bot le signale au démarrage et se contente de désactiver les rappels.

## Structure

```
bot_discord/
├── main.py          # Bot Discord : commandes, logique de points, accès MongoDB
├── keep_alive.py    # Serveur Flask minimal pour garder le service éveillé
├── requirements.txt # Dépendances Python
└── .env             # Secrets (non versionné)
```

## Problèmes courants

- **`Failed to connect to MongoDB`** : vérifiez `MONGODB_URI` et que votre IP est
  autorisée dans *Network Access* sur MongoDB Atlas (`0.0.0.0/0` pour un hébergeur).
- **`MONGODB_URI not set`** : le fichier `.env` est absent ou n'est pas à la racine.
- **`LoginFailure: Improper token`** : token Discord invalide ou régénéré.
- **Le bot ne répond pas aux commandes** : le `MESSAGE CONTENT INTENT` n'est pas activé.
