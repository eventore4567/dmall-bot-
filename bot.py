from __future__ import annotations

import asyncio
import logging
import os
import time

import discord
from discord import app_commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dmall")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = os.getenv("GUILD_ID", "").strip()
ROLE_NAME = os.getenv("DM_OPT_IN_ROLE_NAME", "DM Annonces").strip() or "DM Annonces"
MEMBERS_ENABLED = os.getenv("ENABLE_MEMBER_INTENT", "").lower() in ("true", "1", "yes", "on")
COLOR = 0x5865F2


def get_opt_in_role(guild: discord.Guild) -> discord.Role | None:
    return next(
        (role for role in guild.roles
         if role.name.casefold() == ROLE_NAME.casefold() and not role.is_default()),
        None,
    )


async def find_recipients(guild: discord.Guild) -> list[int]:
    role = get_opt_in_role(guild)
    if role is None:
        raise ValueError(
            f"Crée le rôle « {ROLE_NAME} » et propose-le volontairement aux membres "
            "via les rôles à choisir de ton serveur."
        )

    recipients: list[int] = []
    # On utilise la liste officielle des membres et non une cache partielle.
    async for member in guild.fetch_members(limit=None):
        if not member.bot and role in member.roles:
            recipients.append(member.id)
    return recipients


class Campaign:
    def __init__(
        self, bot: "DMAllBot", guild: discord.Guild, author_id: int,
        message: str, recipients: list[int],
    ):
        self.bot = bot
        self.guild = guild
        self.author_id = author_id
        self.message = message
        self.recipients = recipients
        self.total = max(int(guild.member_count or 0), len(recipients))
        self.sent = 0
        self.skipped = max(0, self.total - len(recipients))
        self.failed = 0
        self.stopped = False
        self.finished = False
        self.last_update = 0.0
        self.stop_requested = asyncio.Event()
        self.panel: discord.Message | None = None

    def embed(self) -> discord.Embed:
        processed = self.sent + self.skipped + self.failed
        status = "Arrêté" if self.stopped else ("Terminé" if self.finished else "En cours")
        return discord.Embed(
            title=f"DM All — {status}",
            description=(
                f"**Membres traités :** {processed}/{self.total}\n"
                f"**Messages envoyés :** {self.sent}\n"
                f"**Ignorés :** {self.skipped}\n"
                f"**Échecs :** {self.failed}\n\n"
                f"Destinataires : membres ayant choisi le rôle « {ROLE_NAME} ».\n"
                "Ignorés = autres membres, bots, départs, rôle retiré ou MP fermés."
            ),
            color=0x2ECC71 if self.finished and not self.stopped else COLOR,
        )

    async def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_update < 3:
            return
        self.last_update = now
        if self.panel is None:
            return
        try:
            await self.panel.edit(
                embed=self.embed(),
                view=None if self.finished else StopView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.exception("Impossible de mettre à jour la progression")

    async def run(self) -> None:
        try:
            for user_id in self.recipients:
                if self.stop_requested.is_set():
                    self.stopped = True
                    break

                try:
                    member = await self.guild.fetch_member(user_id)
                except (discord.NotFound, discord.Forbidden):
                    self.skipped += 1
                    await self.refresh()
                    continue
                except discord.HTTPException:
                    self.failed += 1
                    await self.refresh()
                    continue

                # Révocation immédiate du consentement si le rôle a été retiré.
                role = get_opt_in_role(self.guild)
                if member.bot or role is None or role not in member.roles:
                    self.skipped += 1
                    await self.refresh()
                    continue

                try:
                    # discord.py applique automatiquement les limites API Discord.
                    await member.send(
                        self.message,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.Forbidden:
                    self.skipped += 1
                except discord.HTTPException:
                    self.failed += 1
                else:
                    self.sent += 1

                await self.refresh()
        except Exception:
            log.exception("Erreur inattendue durant la campagne")
        finally:
            self.finished = True
            await self.refresh(force=True)
            self.bot.active_campaigns.pop(self.guild.id, None)


class StopView(discord.ui.View):
    def __init__(self, campaign: Campaign):
        super().__init__(timeout=3600)
        self.campaign = campaign

    @discord.ui.button(label="Arrêter l'envoi", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        is_admin = isinstance(user, discord.Member) and user.guild_permissions.administrator
        if user.id != self.campaign.author_id and not is_admin:
            return await interaction.response.send_message("Réservé aux administrateurs.", ephemeral=True)
        if self.campaign.finished:
            return await interaction.response.send_message("Cette campagne est déjà terminée.", ephemeral=True)
        self.campaign.stop_requested.set()
        await interaction.response.send_message("Arrêt demandé.", ephemeral=True)


class ConfirmView(discord.ui.View):
    def __init__(
        self, bot: "DMAllBot", guild_id: int, owner_id: int,
        content: str, recipients: list[int],
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.content = content
        self.recipients = recipients
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmer l'envoi", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None or interaction.channel is None:
            return await interaction.response.send_message("Serveur indisponible.", ephemeral=True)
        if self.confirmed or guild.id in self.bot.active_campaigns:
            return await interaction.response.send_message("Un envoi est déjà en cours.", ephemeral=True)
        if not self.recipients:
            return await interaction.response.send_message(
                f"Aucun membre n'a choisi le rôle « {ROLE_NAME} ».", ephemeral=True
            )

        self.confirmed = True
        campaign = Campaign(self.bot, guild, self.owner_id, self.content, self.recipients)
        self.bot.active_campaigns[guild.id] = campaign
        try:
            panel = await interaction.channel.send(
                embed=campaign.embed(),
                view=StopView(campaign),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            self.confirmed = False
            self.bot.active_campaigns.pop(guild.id, None)
            return await interaction.response.send_message(
                "Le bot a besoin de la permission Envoyer des messages dans ce salon.",
                ephemeral=True,
            )

        campaign.panel = panel
        await interaction.response.edit_message(
            content=f"Campagne démarrée : {panel.jump_url}",
            embed=None, view=None,
        )
        asyncio.create_task(campaign.run(), name=f"dmall-{guild.id}")

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Annulé. Aucun MP envoyé.", embed=None, view=None
        )


class MessageModal(discord.ui.Modal, title="DM All — Nouveau message"):
    message = discord.ui.TextInput(
        label="Message à envoyer",
        style=discord.TextStyle.paragraph,
        placeholder="Écris ton message ici…",
        min_length=1,
        max_length=1800,
    )

    def __init__(self, bot: "DMAllBot"):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Serveur indisponible.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            recipients = await find_recipients(guild)
        except ValueError as error:
            return await interaction.followup.send(str(error), ephemeral=True)
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.followup.send(
                "Impossible de lire les membres : vérifie l'intent Server Members "
                "dans le portail Discord et ENABLE_MEMBER_INTENT=1 sur Railway.",
                ephemeral=True,
            )

        content = self.message.value.strip()
        if not content:
            return await interaction.followup.send("Le message est vide.", ephemeral=True)
        total = max(guild.member_count or 0, len(recipients))
        embed = discord.Embed(title="DM All — Aperçu", description=content, color=COLOR)
        embed.add_field(
            name="Destinataires",
            value=f"{len(recipients)} abonnés au rôle « {ROLE_NAME} » / {total} membres",
            inline=False,
        )
        embed.set_footer(text="Confirmation requise — aucun MP envoyé pour l'instant.")
        await interaction.followup.send(
            embed=embed,
            view=ConfirmView(self.bot, guild.id, interaction.user.id, content, recipients),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class DMAllBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = False
        intents.members = MEMBERS_ENABLED
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.active_campaigns: dict[int, Campaign] = {}

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            commands = await self.tree.sync(guild=guild)
        else:
            commands = await self.tree.sync()
        log.info("%s commande(s) synchronisée(s)", len(commands))

    async def on_ready(self):
        log.info("Bot connecté : %s", self.user)


bot = DMAllBot()


@bot.tree.error
async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.warning("Erreur de commande : %r", error)
    message = (
        "Commande réservée aux administrateurs."
        if isinstance(error, app_commands.MissingPermissions)
        else "Une erreur est survenue."
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


@bot.tree.command(name="damall", description="Préparer une annonce privée aux abonnés du serveur")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def damall(interaction: discord.Interaction):
    if not MEMBERS_ENABLED:
        return await interaction.response.send_message(
            "Active d'abord Server Members Intent dans le portail Discord, "
            "puis ENABLE_MEMBER_INTENT=1 sur Railway. "
            f"Les destinataires doivent avoir choisi le rôle « {ROLE_NAME} ».",
            ephemeral=True,
        )
    if get_opt_in_role(interaction.guild) is None:
        return await interaction.response.send_message(
            f"Crée d'abord le rôle « {ROLE_NAME} » et rends-le disponible en auto-attribution.",
            ephemeral=True,
        )
    if interaction.guild_id in bot.active_campaigns:
        return await interaction.response.send_message(
            "Un envoi est déjà en cours sur ce serveur.", ephemeral=True
        )
    await interaction.response.send_modal(MessageModal(bot))


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN absent de Railway.")
    bot.run(TOKEN, log_handler=None)
