# DM All — bot Discord

Bot autonome de diffusion d'annonces privées, avec une seule commande slash : **/damall**.

## Fonctionnement

- Un administrateur lance **/damall**.
- Le bouton **Publier le panneau d'inscription** place un panneau dans le salon. Les membres choisissent librement de recevoir les MP ou de se désinscrire.
- **Rédiger un DM** ouvre un formulaire pour écrire l'annonce.
- Le bot présente un aperçu et le nombre de membres inscrits. **Confirmer l'envoi** est obligatoire.
- Un message de progression s'affiche dans le salon : membres traités / membres du serveur, MP envoyés, ignorés et échecs. **Arrêter l'envoi** permet au staff d'interrompre la campagne.

Les membres non inscrits, les comptes ayant quitté le serveur, les bots et les MP fermés sont ignorés. Une désinscription est prise en compte même pendant une campagne. Le bot n'essaie pas de contourner les limites ou protections de Discord.

Le bot est volontairement limité aux envois consentis. Discord interdit les messages privés de masse non sollicités.

## Déploiement Railway

Ce dépôt contient le point d'entrée **bot.py**, les dépendances **requirements.txt**, et **railway.json** avec la commande de démarrage.

Dans Railway, sélectionne le service lié au dépôt eventore4567/dmall-bot- et ajoute les variables suivantes dans l'onglet **Variables** :

| Variable | Valeur | Obligatoire |
| --- | --- | --- |
| DISCORD_TOKEN | Token du bot créé sur https://discord.com/developers/applications | Oui, secret |
| DB_PATH | /data/damall.sqlite3 | Recommandé |
| GUILD_ID | ID numérique du serveur Discord où synchroniser la commande immédiatement | Facultatif |

**Important : ne mets jamais le token dans GitHub, dans une capture ni dans le chat.** Ajoute-le uniquement comme variable secrète Railway.

Ajoute un volume Railway persistant monté sur **/data** pour conserver les inscriptions au fil des redéploiements. Sans volume, le fichier SQLite créé localement peut disparaître lors d'un nouveau déploiement.

Une fois le token ajouté et le service déployé, consulte **Railway > Deployments > Logs**. La ligne "Bot connecté" indique que la connexion Discord fonctionne.

## Configuration Discord

Dans le portail développeur Discord :

1. Crée une application et un bot.
2. Copie son token dans la variable Railway **DISCORD_TOKEN**.
3. Dans l'installateur de l'application, ajoute les scopes **bot** et **applications.commands**.
4. Invite le bot sur ton serveur. Donne-lui la permission **Envoyer des messages** dans le salon voulu.
5. La commande **/damall** demande les permissions administrateur.

Aucun intent privilégié Member/Message Content n'est requis : le bot n'énumère pas tous les membres. Il ne contacte que les utilisateurs qui se sont inscrits via le panneau. Le compteur total s'appuie sur le nombre de membres annoncé par Discord.

Si **GUILD_ID** est renseigné, la commande est synchronisée sur ce serveur au démarrage. Sinon, elle est enregistrée globalement et peut mettre plus longtemps à apparaître.

## Démarrer en local

~~~
python -m pip install -r requirements.txt
DISCORD_TOKEN="ton-token" python bot.py
~~~

Pour conserver les inscriptions localement, le programme utilise par défaut le fichier **data/damall.sqlite3**.

## Confidentialité et limitations

- Seuls les identifiants Discord des membres ayant accepté les annonces sont conservés, avec l'identifiant du serveur.
- Les membres peuvent supprimer leur inscription à tout moment en cliquant sur **Ne plus recevoir**.
- Les messages de DM et les campagnes ne sont pas stockés dans la base.
- Les messages bloqués sont classés comme ignorés, sans tentative pour contourner leurs paramètres de confidentialité.
- La bibliothèque discord.py gère les limites de requêtes de l'API. Un nombre élevé d'inscrits peut nécessiter du temps.
