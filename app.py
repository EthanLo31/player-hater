import sqlite3
from datetime import date

import discord
import discord.ext.commands
from discord import app_commands
from dotenv import load_dotenv
from os import getenv
from urllib.request import urlopen

from nhlpy.team import Team

# Load environment variables from the local .env file.
load_dotenv()
BOT_TOKEN: str = getenv("BOT_TOKEN")  # type: ignore[reportOptionalMemberAccess]
COMMAND_PREFIX = "/"
PLAYER_DB_PATH = "Player Lists.db"

PlayerMapping = tuple[str, str, str, str]
NHL_API_BASE_URL = "https://statsapi.web.nhl.com/api/v1"

# Configure the Discord bot intents it needs to operate.
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Initialize the bot with the configured command prefix and intents.
bot = discord.ext.commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)


def init_player_list_db():
    """
    Create the player list and hate mapping tables if they do not exist.
    """
    with sqlite3.connect(PLAYER_DB_PATH) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS player_options (
                first_name TEXT,
                last_name TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS player_hate (
                mentioned_first_name TEXT,
                mentioned_last_name TEXT,
                hated_first_name TEXT,
                hated_last_name TEXT
            )
            """
        )


def load_player_state() -> tuple[list[str], list[PlayerMapping]]:
    """Load player options and hate mappings from the database."""
    with sqlite3.connect(PLAYER_DB_PATH) as connection:
        cursor = connection.cursor()

        cursor.execute("SELECT first_name, last_name FROM player_options")
        player_options_rows = cursor.fetchall()
        player_options = [f"{first} {last}" for first, last in player_options_rows]

        cursor.execute(
            "SELECT mentioned_first_name, mentioned_last_name, hated_first_name, hated_last_name FROM player_hate"
        )
        player_hate_rows = cursor.fetchall()
        player_hate = [tuple(row) for row in player_hate_rows]

    return player_options, player_hate


init_player_list_db()
player_options, player_hate = load_player_state()


def update_player_hate() -> None:
    """
    Refresh the in-memory hate mappings from the database.
    """
    global player_hate
    _, player_hate = load_player_state()


def build_player_choices(current: str) -> list[app_commands.Choice[str]]:
    """Build autocomplete choices for the provided search text."""
    current = current.lower()
    autocomplete_list: list[app_commands.Choice[str]] = []

    for player in player_options:
        first, last = player.split(" ", 1)
        if (
            current in first.lower()
            or current in last.lower()
            or current in player.lower()
        ):
            autocomplete_list.append(app_commands.Choice(name=player, value=player))

    return autocomplete_list[:25]


def _extract_roster_entries(roster_payload: dict) -> list[dict]:
    """Return the roster entries from an NHL team roster payload."""
    teams = roster_payload.get("teams", [])
    if not teams:
        return []

    roster = teams[0].get("roster", {})
    if isinstance(roster, dict):
        if isinstance(roster.get("roster"), list):
            return roster["roster"]

        for position_group in ("forwards", "defensemen", "goalies"):
            players = roster.get(position_group)
            if isinstance(players, list):
                return players

    return []


def _extract_player_name(player_entry: dict) -> tuple[str, str]:
    """Extract a player's first and last name from a roster entry."""
    person = player_entry.get("person", player_entry)

    first_name = person.get("firstName")
    last_name = person.get("lastName")

    if isinstance(first_name, dict):
        first_name = first_name.get("default", "")
    if isinstance(last_name, dict):
        last_name = last_name.get("default", "")

    if not first_name or not last_name:
        full_name = person.get("fullName", "")
        if " " in full_name:
            first_name, last_name = full_name.split(" ", 1)

    return str(first_name), str(last_name)


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or len(message.content) < 5:
        return

    # Group mappings by last name so matching stays local to the message text.
    last_name_map: dict[str, list[PlayerMapping]] = {}
    for mentioned_first, mentioned_last, hated_first, hated_last in player_hate:
        key = mentioned_last.lower()
        last_name_map.setdefault(key, []).append(
            (mentioned_first, mentioned_last, hated_first, hated_last)
        )

    msg_lower = message.content.lower()
    triggered = set()
    for last_lower, mappings in last_name_map.items():
        if last_lower in msg_lower:
            firsts = [m[0].lower() for m in mappings]
            matched_firsts = [f for f in firsts if f in msg_lower]
            if matched_firsts:
                for (
                    mentioned_first,
                    mentioned_last,
                    hated_first,
                    hated_last,
                ) in mappings:
                    if mentioned_first.lower() in msg_lower and last_lower in msg_lower:
                        key = (mentioned_first, mentioned_last, hated_first, hated_last)
                        if key not in triggered:
                            await message.channel.send(
                                f"Fuck {hated_first} {hated_last}"
                            )
                            triggered.add(key)
            else:
                for (
                    mentioned_first,
                    mentioned_last,
                    hated_first,
                    hated_last,
                ) in mappings:
                    key = (mentioned_first, mentioned_last, hated_first, hated_last)
                    if key not in triggered:
                        await message.channel.send(f"Fuck {hated_first} {hated_last}")
                        triggered.add(key)


@bot.event
async def on_ready():
    """
    Run once the bot has connected successfully.
    """

    await bot.change_presence(status=discord.Status.online)
    print(f"Logged in as {bot.user}")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as exc:
        print(f"Error syncing commands: {exc}")


@bot.tree.command(
    name="list_hated_players", description="Show a list of all hated players."
)
async def list_hated_players(interaction: discord.Interaction):
    """
    Show all current hate mappings.
    """
    if not player_hate:
        embed = discord.Embed(
            title="Hated Players",
            description="No hated players found.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)
        return

    embed = discord.Embed(
        title="Hated Players Map",
        description="[Mentioned Player] → [Hated Player]",
        color=discord.Color.blue()
    )
    for mentioned_first, mentioned_last, hated_first, hated_last in player_hate:
        embed.add_field(
            name=f"{mentioned_first} {mentioned_last}",
            value=f"→ {hated_first} {hated_last}",
            inline=False
        )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="map_hated_player", description="Add a player map to the hate list"
)
@app_commands.describe(
    mentioned_player="When this player is mentioned (format: First Last) ...",
    hated_player="... this player will be hated (format: First Last).",
)
@app_commands.checks.has_permissions(use_application_commands=True)
async def map_hated_player(
    interaction: discord.Interaction, mentioned_player: str, hated_player: str
):
    """
    Add a player hate mapping.

    Expected format for both players: First Last (e.g., "Connor McDavid")
    Use autocomplete to select valid players from the database.
    """
    if " " not in mentioned_player or " " not in hated_player:
        embed = discord.Embed(
            title="Error",
            description="Invalid format. Both players must be in 'First Last' format (e.g., 'Connor McDavid').",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return

    mentioned_first, mentioned_last = mentioned_player.split(" ", 1)
    hated_first, hated_last = hated_player.split(" ", 1)

    if hated_player not in player_options:
        embed = discord.Embed(
            title="Error",
            description=f"`{hated_player}` is not a valid player. Please choose an active NHL player.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return

    if [mentioned_first, mentioned_last, hated_first, hated_last] in player_hate:
        embed = discord.Embed(
            title="Error",
            description="Player hate map is already in the hate list.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return

    with sqlite3.connect(PLAYER_DB_PATH) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO player_hate (
                mentioned_first_name,
                mentioned_last_name,
                hated_first_name,
                hated_last_name
            )
            VALUES (?, ?, ?, ?)
            """,
            (mentioned_first, mentioned_last, hated_first, hated_last),
        )

    update_player_hate()

    embed = discord.Embed(
        title="Player Added",
        description=f"When `{mentioned_player}` is mentioned, `{hated_player}` will be hated.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)


@map_hated_player.autocomplete("mentioned_player")
async def mentioned_player_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return build_player_choices(current)


@map_hated_player.autocomplete("hated_player")
async def hated_player_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return build_player_choices(current)


@bot.tree.command(
    name="remove_hated_player_map",
    description="Remove a player map from the hate list.",
)
@app_commands.describe(
    mentioned_player="When this player is mentioned (format: First Last) ...",
    hated_player="... this player will be hated (format: First Last).",
)
@app_commands.checks.has_permissions(use_application_commands=True)
async def remove_hated_player_map(
    interaction: discord.Interaction, mentioned_player: str, hated_player: str
):
    """
    Remove a player hate mapping.
    """
    if " " not in mentioned_player or " " not in hated_player:
        embed = discord.Embed(
            title="Error",
            description="Invalid format. Both players must be in 'First Last' format (e.g., 'Connor McDavid').",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return

    mentioned_first, mentioned_last = mentioned_player.split(" ", 1)
    hated_first, hated_last = hated_player.split(" ", 1)

    if (mentioned_first, mentioned_last, hated_first, hated_last) not in player_hate:
        embed = discord.Embed(
            title="Error",
            description="Mapping not found in the hate list.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return

    with sqlite3.connect(PLAYER_DB_PATH) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            DELETE FROM player_hate
            WHERE mentioned_first_name=?
              AND mentioned_last_name=?
              AND hated_first_name=?
              AND hated_last_name=?
            """,
            (mentioned_first, mentioned_last, hated_first, hated_last),
        )

    update_player_hate()

    embed = discord.Embed(
        title="Player Removed",
        description=f"When `{mentioned_player}` is mentioned, `{hated_player}` will no longer be hated.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)


@remove_hated_player_map.autocomplete("mentioned_player")
async def remove_mentioned_player_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    current = current.lower()
    autocomplete_list: list[app_commands.Choice[str]] = []
    seen: set[str] = set()

    for mentioned_first, mentioned_last, hated_first, hated_last in player_hate:
        player = f"{mentioned_first} {mentioned_last}"
        if current in player.lower() and player not in seen:
            autocomplete_list.append(app_commands.Choice(name=player, value=player))
            seen.add(player)
    return autocomplete_list[:25]


@remove_hated_player_map.autocomplete("hated_player")
async def remove_hated_player_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    current = current.lower()
    autocomplete_list: list[app_commands.Choice[str]] = []
    seen: set[str] = set()

    for mentioned_first, mentioned_last, hated_first, hated_last in player_hate:
        player = f"{hated_first} {hated_last}"
        if current in player.lower() and player not in seen:
            autocomplete_list.append(app_commands.Choice(name=player, value=player))
            seen.add(player)
    return autocomplete_list[:25]


@bot.event
async def on_command_error(interaction: discord.Interaction, error: Exception):
    """
    Send a generic error message when a command fails.
    """
    embed = discord.Embed(
        title="Error",
        description=f"An error occurred: {error}",
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    bot.run(BOT_TOKEN)


def populate_player_options(db_path: str):
    """
    Populate the player_options table with active NHL players.

    This function uses ``nhlpy`` to fetch rosters for every team and is
    intentionally expensive to run.
    """
    import json

    import sqlite3

    with urlopen(f"{NHL_API_BASE_URL}/teams", timeout=30) as response:
        teams = json.loads(response.read().decode("utf-8")).get("teams", [])

    team_ids = [team["id"] for team in teams if "id" in team]

    connect = sqlite3.connect(db_path)
    cursor = connect.cursor()

    # Replace the current contents with a fresh roster snapshot.
    cursor.execute("DELETE FROM player_options")

    season = f"{date.today().year - 1}{date.today().year}"
    for team_id in team_ids:
        roster = Team(team_id).roster()
        roster_entries = _extract_roster_entries(roster)

        print(team_id)
        for player_entry in roster_entries:
            first_name, last_name = _extract_player_name(player_entry)
            if not first_name or not last_name:
                continue

            cursor.execute(
                """
                INSERT INTO player_options (first_name, last_name)
                VALUES (?, ?)
                """,
                (first_name, last_name),
            )

    connect.commit()
    connect.close()


# populate_player_options("Player Lists.db")
