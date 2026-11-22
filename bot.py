from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from pathlib import Path

import discord
from discord import app_commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("damall")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = Path(os.getenv("DB_PATH", "./data/damall.sqlite3"))
TEST_GUILD_ID = os.getenv("GUILD_ID", "").strip()

COLOR = 0x5865F2


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS subscriptions ("
                "guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
                "PRIMARY KEY (guild_id, user_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS signup_panels ("
                "guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, "
                "message_id INTEGER NOT NULL, PRIMARY KEY (guild_id, channel_id))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def subscribe(self, guild_id: int, user_id: int) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO subscriptions (guild_id, user_id) VALUES (?, ?)",
                (guild_id, user_id),
            )
            return cursor.rowcount > 0

    def unsubscribe(self, guild_id: int, user_id: int) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM subscriptions WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            )
            return cursor.rowcount > 0

    def subscribed(self, guild_id: int, user_id: int) -> bool:
        with self._connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM subscriptions WHERE guild_id=? AND user_id=?",
                    (guild_id, user_id),
                ).fetchone()
                is not None
            )

    def subscribers(self, guild_id: int) -> list[int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT user_id FROM subscriptions WHERE guild_id=? ORDER BY user_id",
                (guild_id,),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def panel_id(self, guild_id: int, channel_id: int) -> int | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT message_id FROM signup_panels WHERE guild_id=? AND channel_id=?",
                (guild_id, channel_id),
            ).fetchone()
        return int(row[0]) if row else None

    def save_panel(self, guild_id: int, channel_id: int, message_id: int) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO signup_panels (guild_id, channel_id, message_id) "
                "VALUES (?, ?, ?) ON CONFLICT (guild_id, channel_id) "
                "DO UPDATE SET message_id=excluded.message_id",
                (guild_id, channel_id, message_id),
            )


def signup_embed() -> discord.Embed:
    return discord.Embed(
        title="Annonces privées",
        description=(
            "Vous pouvez recevoir les annonces du serveur directement en message privé.\n\n"
            "**Recevoir les annonces** : vous acceptez de recevoir ces messages.\n"
            "**Ne plus recevoir** : désinscription immédiate.\n\n"
            "L'inscription est facultative. Vous pouvez changer d'avis à tout moment."
        ),
        color=COLOR,
    )


class SignupView(discord.ui.View):
    def __init__(self, bot: "DMAllBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Recevoir les annonces",
        style=discord.ButtonStyle.success,
        custom_id="damall:subscription:yes:v1",
    )
    async def subscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id is None or interaction.user.bot:
            return await interaction.response.send_message("Action indisponible.", ephemeral=True)
        added = self.bot.store.subscribe(interaction.guild_id, interaction.user.id)
        reply = (
            "Inscription confirmée. Vous recevrez les annonces en MP."
            if added else "Vous êtes déjà inscrit aux annonces."
        )
        await interaction.response.send_message(reply, ephemeral=True)

    @discord.ui.button(
        label="Ne plus recevoir",
        style=discord.ButtonStyle.secondary,
        custom_id="damall:subscription:no:v1",
    )
    async def unsubscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id is None:
            return await interaction.response.send_message("Action indisponible.", ephemeral=True)
        removed = self.bot.store.unsubscribe(interaction.guild_id, interaction.user.id)
        reply = (
            "Désinscription confirmée. Vous ne recevrez plus ces annonces."
            if removed else "Vous n'étiez pas inscrit."
        )
        await interaction.response.send_message(reply, ephemeral=True)


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
        self.attempted = 0
        self.started_at = time.monotonic()
        self.last_update = 0.0
        self.stop_requested = asyncio.Event()
        self.finished = False
        self.stopped = False
        self.panel: discord.Message | None = None

    def build_embed(self) -> discord.Embed:
        processed = self.skipped + self.sent + self.failed
        total = max(self.total, processed)
        status = "Arrêté" if self.stopped else ("Terminé" if self.finished else "En cours")
        return discord.Embed(
            title="DM All — " + status,
            description=(
                f"**Progression :** {processed}/{total} membres\n"
                f"**MP envoyés :** {self.sent}\n"
                f"**Ignorés :** {self.skipped}\n"
                f"**Échecs :** {self.failed}\n\n"
                "Ignorés = non-inscrits, départs du serveur, bots ou MP fermés."
            ),
            color=(0x2ECC71 if self.finished and not self.stopped else COLOR),
        )

    async def update_panel(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_update < 3:
            return
        self.last_update = now
        if self.panel is None:
            return
        try:
            await self.panel.edit(
                embed=self.build_embed(),
                view=None if self.finished else StopView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            log.warning("Impossible d'actualiser le panneau de progression.", exc_info=True)

    async def run(self) -> None:
        try:
            for user_id in self.recipients:
                if self.stop_requested.is_set():
                    self.stopped = True
                    break

                # Un désabonnement pendant un envoi est toujours respecté.
                if not self.bot.store.subscribed(self.guild.id, user_id):
                    self.skipped += 1
                    await self.update_panel()
                    continue

                try:
                    member = await self.guild.fetch_member(user_id)
                except discord.NotFound:
                    self.skipped += 1
                    await self.update_panel()
                    continue
                except discord.Forbidden:
                    self.skipped += 1
                    await self.update_panel()
                    continue
                except discord.HTTPException:
                    self.failed += 1
                    await self.update_panel()
                    continue

                if member.bot:
                    self.skipped += 1
                    await self.update_panel()
                    continue

                try:
                    # discord.py attends automatically to Discord's API rate limits.
                    await member.send(
                        self.message,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.Forbidden:
                    self.skipped += 1  # MP fermés / accès refusé
                except discord.HTTPException:
                    self.failed += 1
                else:
                    self.sent += 1

                self.attempted += 1
                await self.update_panel()
        except Exception:
            log.exception("Erreur inattendue pendant l'envoi des annonces.")
        finally:
            self.finished = True
            await self.update_panel(force=True)
            self.bot.active_campaigns.pop(self.guild.id, None)


class StopView(discord.ui.View):
    def __init__(self, campaign: Campaign):
        super().__init__(timeout=None)
        self.campaign = campaign

    @discord.ui.button(
        label="Arrêter l'envoi",
        style=discord.ButtonStyle.danger,
        custom_id="damall:stop:v1",
    )
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        admin = isinstance(user, discord.Member) and user.guild_permissions.administrator
        if not admin and user.id != self.campaign.author_id:
            return await interaction.response.send_message("Accès réservé au staff.", ephemeral=True)
        if self.campaign.finished:
            return await interaction.response.send_message("Envoi déjà terminé.", ephemeral=True)
        self.campaign.stop_requested.set()
        await interaction.response.send_message("Arrêt demandé. Le DM en cours sera terminé.", ephemeral=True)


class ConfirmView(discord.ui.View):
    def __init__(self, bot: "DMAllBot", message: str, owner_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.message = message
        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Ce panneau ne vous appartient pas.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmer l'envoi", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None or interaction.channel is None:
            return await interaction.response.send_message("Salon indisponible.", ephemeral=True)
        if guild.id in self.bot.active_campaigns:
            return await interaction.response.send_message(
                "Un envoi est déjà en cours sur ce serveur.", ephemeral=True
            )

        recipients = self.bot.store.subscribers(guild.id)
        if not recipients:
            return await interaction.response.send_message(
                "Aucun membre inscrit. Publiez d'abord le panneau d'inscription.", ephemeral=True
            )

        campaign = Campaign(self.bot, guild, interaction.user.id, self.message, recipients)
        try:
            panel = await interaction.channel.send(
                embed=campaign.build_embed(),
                view=StopView(campaign),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message(
                "Le bot doit avoir la permission d'envoyer des messages dans ce salon.",
                ephemeral=True,
            )

        campaign.panel = panel
        self.bot.active_campaigns[guild.id] = campaign
        await interaction.response.edit_message(
            content="Envoi démarré : " + panel.jump_url,
            embed=None,
            view=None,
        )
        asyncio.create_task(campaign.run(), name="damall-" + str(guild.id))

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Envoi annulé. Aucun MP n'a été envoyé.",
            embed=None, view=None,
        )


class MessageModal(discord.ui.Modal, title="Rédiger le message privé"):
    text = discord.ui.TextInput(
        label="Message à envoyer",
        placeholder="Rédige ton annonce ici…",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=1500,
        required=True,
    )

    def __init__(self, bot: "DMAllBot"):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Serveur introuvable.", ephemeral=True)
        content = str(self.text).strip()
        if not content:
            return await interaction.response.send_message("Message vide.", ephemeral=True)

        recipients = self.bot.store.subscribers(guild.id)
        total = int(guild.member_count or 0)
        embed = discord.Embed(
            title="Aperçu avant envoi",
            description=content,
            color=COLOR,
        )
        embed.add_field(
            name="Destinataires",
            value=f"{len(recipients)} inscrits / {total} membres du serveur",
            inline=False,
        )
        embed.set_footer(text="Aucun message ne part sans confirmation.")
        await interaction.response.send_message(
            embed=embed,
            view=ConfirmView(self.bot, content, interaction.user.id, guild.id),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class AdminView(discord.ui.View):
    def __init__(self, bot: "DMAllBot", owner_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        admin = isinstance(interaction.user, discord.Member) and (
            interaction.user.guild_permissions.administrator
        )
        if interaction.user.id != self.owner_id or not admin:
            await interaction.response.send_message("Accès administrateur requis.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Rédiger un DM", style=discord.ButtonStyle.primary)
    async def compose(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(MessageModal(self.bot))

    @discord.ui.button(label="Publier le panneau d'inscription", style=discord.ButtonStyle.secondary)
    async def panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or interaction.channel is None:
            return await interaction.response.send_message("Salon indisponible.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        channel_id = interaction.channel.id
        existing_id = self.bot.store.panel_id(guild_id, channel_id)
        panel: discord.Message | None = None

        if existing_id:
            try:
                panel = await interaction.channel.fetch_message(existing_id)
                await panel.edit(embed=signup_embed(), view=SignupView(self.bot))
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                panel = None

        if panel is None:
            try:
                panel = await interaction.channel.send(
                    embed=signup_embed(),
                    view=SignupView(self.bot),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                return await interaction.followup.send(
                    "Le bot doit pouvoir envoyer des messages dans ce salon.", ephemeral=True
                )

        self.bot.store.save_panel(guild_id, channel_id, panel.id)
        await interaction.followup.send(
            "Panneau d'inscription prêt : " + panel.jump_url, ephemeral=True
        )


class DMAllBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.store = Storage(DB_PATH)
        self.active_campaigns: dict[int, Campaign] = {}

    async def setup_hook(self):
        self.add_view(SignupView(self))
        if TEST_GUILD_ID:
            guild = discord.Object(id=int(TEST_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        log.info("%s commande(s) slash synchronisée(s).", len(synced))

    async def on_ready(self):
        log.info("Bot connecté : %s (%s)", self.user, self.user.id if self.user else "?")

    async def on_app_command_error(self, interaction: discord.Interaction, error):
        log.warning("Erreur commande : %r", error)
        message = (
            "Cette commande est réservée aux administrateurs."
            if isinstance(error, app_commands.MissingPermissions)
            else "Impossible d'exécuter cette action."
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


bot = DMAllBot()


@bot.tree.command(name="damall", description="Ouvrir le panneau d'annonces privées du serveur")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def damall(interaction: discord.Interaction):
    count = len(bot.store.subscribers(interaction.guild_id))
    embed = discord.Embed(
        title="DM All — Administration",
        description=(
            "Préparez une annonce ou publiez le panneau permettant aux membres "
            "d'accepter les MP.\n\n"
            f"**Membres inscrits :** {count}\n"
            f"**Membres du serveur :** {interaction.guild.member_count or 0}"
        ),
        color=COLOR,
    )
    await interaction.response.send_message(
        embed=embed, view=AdminView(bot, interaction.user.id), ephemeral=True
    )


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN absent. Ajoutez-le dans Railway > Variables.")
    bot.run(TOKEN, log_handler=None)
