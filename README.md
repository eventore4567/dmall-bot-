# DM All — Bot Discord

Bot autonome utilisant **une seule commande : /damall**.

## Fonctionnement

- Un administrateur lance **/damall** : un formulaire de rédaction s'ouvre directement.
- Il écrit son message, puis voit un aperçu et le nombre de destinataires.
- **Confirmer l'envoi** lance l'annonce. **Annuler** ne fait rien.
- Une progression publique affiche les membres traités / membres du serveur, les MP envoyés, les personnes ignorées et les échecs.
- **Arrêter l'envoi** interrompt les messages suivants.

Il n'existe plus de bouton ou de panneau d'inscription dans le bot.

## Destinataires

Le bot envoie seulement aux utilisateurs qui ont **volontairement choisi** le rôle Discord **DM Annonces**. Le rôle peut être proposé via les rôles à choisir de SentriX ou l'onboarding de Discord.

Les membres sans ce rôle, les bots, les utilisateurs ayant quitté le serveur, les personnes ayant retiré le rôle et les MP fermés sont comptés comme ignorés.

Cette règle évite les MP de masse non sollicités et permet à chacun de se retirer simplement en retirant le rôle. Le bot n'essaie pas de contourner les protections ou limites de Discord.

## Variables Railway

| Variable | Valeur | Nécessaire |
| --- | --- | --- |
| DISCORD_TOKEN | Token du bot Discord (secret) | Oui |
| ENABLE_MEMBER_INTENT | 1 | Oui, après activation dans le portail Discord |
| DM_OPT_IN_ROLE_NAME | DM Annonces | Optionnel (nom par défaut) |
| GUILD_ID | ID du serveur pour synchroniser la commande rapidement | Optionnel |

L'ancienne variable DB_PATH et l'ancien volume Railway ne sont plus utilisés par cette version. Ils peuvent rester en place ; les données précédentes ne sont ni effacées ni utilisées pour cibler des utilisateurs.

## Configuration Discord

1. Ouvre https://discord.com/developers/applications et sélectionne ton application.
2. Dans **Bot > Privileged Gateway Intents**, active **Server Members Intent**.
3. Dans Railway, définis **ENABLE_MEMBER_INTENT=1**.
4. Crée un rôle serveur nommé exactement **DM Annonces** (ou configure DM_OPT_IN_ROLE_NAME).
5. Propose ce rôle à l'auto-attribution ; il ne faut pas le donner automatiquement à tout le serveur.
6. Assure-toi que le bot peut envoyer des messages dans le salon utilisé.
7. Lance **/damall** avec un compte administrateur.

Le bot utilise la liste des membres autorisée par Discord pour identifier les personnes ayant demandé ces messages.

## Exécution locale

~~~
python -m pip install -r requirements.txt
DISCORD_TOKEN="ton-token" ENABLE_MEMBER_INTENT=1 python bot.py
~~~

Ne publie jamais de token dans GitHub, dans un chat ou dans une capture. La bibliothèque discord.py prend en charge les limites de requêtes imposées par Discord.
