import dateutil.utils
import discord, sqlite3, discord.ext.commands
from discord import app_commands
from dotenv import load_dotenv
from os import getenv

# Load environment variables from .env file
# Used for securely loading the bot token
load_dotenv()
BOT_TOKEN: str = getenv("BOT_TOKEN")  # type: ignore[reportOptionalMemberAccess]
COMMAND_PREFIX = "/"
PLAYER_LISTS = "Player Lists.db"
global player_options, player_hate

# Set up Discord bot intents (controls what events the bot can see)
# 'message_content' is required to read message text
# 'members' is required for some presence/status features
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Initialize the bot with the specified command prefix and intents
bot = discord.ext.commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)


def init_player_list_db():
    """
    Create the player list and hate mapping tables if they don't exist
    This function is only called at startup
    """
    connect = sqlite3.connect(PLAYER_LISTS)
    cursor = connect.cursor()
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

    connect.commit()
    connect.close()


def read_player_db() -> tuple[list, list]:
    """
    Read player options and hate mappings from the database
    Returns two lists: player_options and player_hate
    """
    connect = sqlite3.connect(PLAYER_LISTS)
    cursor = connect.cursor()

    cursor.execute("SELECT first_name, last_name FROM player_options")
    player_options_rows = cursor.fetchall()
    player_options = [f"{first} {last}" for first, last in player_options_rows]
    del player_options_rows
    # player_options = cursor.fetchall()

    cursor.execute(
        "SELECT mentioned_first_name, mentioned_last_name, hated_first_name, hated_last_name FROM player_hate"
    )
    player_hate_rows = cursor.fetchall()
    player_hate = [
        [first, last, resp_first, resp_last]
        for first, last, resp_first, resp_last in player_hate_rows
    ]
    del player_hate_rows

    return player_options, player_hate


# Initialize the database and load player data at startup
init_player_list_db()
player_options, player_hate = read_player_db()


def update_player_hate():
    """
    Update the global player_hate list from the database
    This is called after any change to the hate mappings
    """
    global player_hate
    connect = sqlite3.connect(PLAYER_LISTS)
    cursor = connect.cursor()

    cursor.execute(
        "SELECT mentioned_first_name, mentioned_last_name, hated_first_name, hated_last_name FROM player_hate"
    )
    player_hate_rows = cursor.fetchall()
    player_hate = [
        [first, last, resp_first, resp_last]
        for first, last, resp_first, resp_last in player_hate_rows
    ]

    connect.close()


# Event: Runs every time a message is sent in a server the bot can see
# Handles the logic for triggering hate responses based on player names
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or len(message.content) < 5:
        return

    # Build a dict of last name -> list of (first, last, hated_first, hated_last)
    # This allows efficient lookup for all mappings sharing a last name
    last_name_map = {}
    for mentioned_first, mentioned_last, hated_first, hated_last in player_hate:
        key = mentioned_last.lower()
        last_name_map.setdefault(key, []).append(
            (mentioned_first, mentioned_last, hated_first, hated_last)
        )

    msg_lower = message.content.lower()
    triggered = set()
    for last_lower, mappings in last_name_map.items():
        if last_lower in msg_lower:
            # Find all first names for this last name
            firsts = [m[0].lower() for m in mappings]
            # Check if any mapped first name is present
            matched_firsts = [f for f in firsts if f in msg_lower]
            if matched_firsts:
                # Trigger only those mappings where both first and last are present
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
                # No mapped first names present, trigger all mappings for this last name
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


# Event: Runs when the bot successfully connects to Discord
# Sets the bot's status and syncs slash commands
@bot.event
async def on_ready():
    """
    Bot is ready.
    """

    await bot.change_presence(status=discord.Status.online)
    print(f"We have logged in as {bot.user}")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Error syncing commands: {e}")


# Command: List all current hate mappings
# Shows which player triggers a hate response for which other player
@bot.tree.command(
    name="list_hated_players", description="Show a list of all hated players."
)
async def list_hated_players(interaction: discord.Interaction):
    """
    Show a list of all hated players.
    """
    if not player_hate:
        await interaction.response.send_message("No hated players found.")
        return

    hate_list = "\n".join(
        [
            f"- `{mentioned_first} {mentioned_last}`: `{hated_first} {hated_last}`"
            for mentioned_first, mentioned_last, hated_first, hated_last in player_hate
        ]
    )
    await interaction.response.send_message(
        f"Hated Players map [mentioned player]: [hated player]\n{hate_list}"
    )


# Command: Add a new hate mapping
# Validates input, checks for duplicates, and updates the database
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
    Add a player to the hate list.

    Expected format for both players: First Last (e.g., "Connor McDavid")
    Use autocomplete to select valid players from the database.
    """
    # Validate input format
    if " " not in mentioned_player or " " not in hated_player:
        await interaction.response.send_message(
            "Invalid format. Both players must be in 'First Last' format (e.g., 'Connor McDavid')."
        )
        return

    mentioned_first, mentioned_last = mentioned_player.split(" ", 1)
    hated_first, hated_last = hated_player.split(" ", 1)

    # Check if the hated player is in the player options
    if hated_player not in player_options:
        await interaction.response.send_message(
            f"`{hated_player}` is not a valid player. Please choose an active NHL player."
        )
        return

    # Check if the player is already in the hate list
    if [mentioned_first, mentioned_last, hated_first, hated_last] in player_hate:
        await interaction.response.send_message(
            f"Player hate map is already in the hate list."
        )
        return

    connect = sqlite3.connect(PLAYER_LISTS)
    cursor = connect.cursor()
    cursor.execute(
        """
        INSERT INTO player_hate (mentioned_first_name, mentioned_last_name, hated_first_name, hated_last_name)
        VALUES (?, ?, ?, ?)
        """,
        (mentioned_first, mentioned_last, hated_first, hated_last),
    )

    connect.commit()
    connect.close()

    # Update the in-memory hate list
    update_player_hate()

    await interaction.response.send_message(
        f"Added player to the hate list. Now, when `{mentioned_player}` is mentioned, `{hated_player}` will be hated."
    )


# Autocomplete for the 'mentioned_player' parameter in map_hated_player
# Suggests player names as the user types
@map_hated_player.autocomplete("mentioned_player")
async def mentioned_player_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    # Filter players based on current input
    current = current.lower()
    autocomplete_list = []

    for player in player_options:
        first, last = player.split(" ", 1)

        if (
            current in first.lower()
            or current in last.lower()
            or current in player.lower()
        ):
            autocomplete_list.append(app_commands.Choice(name=player, value=player))

    return autocomplete_list[:25]


# Autocomplete for the 'hated_player' parameter in map_hated_player
# Suggests player names as the user types
@map_hated_player.autocomplete("hated_player")
async def hated_player_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    # Similar autocomplete for the second parameter
    current = current.lower()
    autocomplete_list = []

    for player in player_options:
        first, last = player.split(" ", 1)

        if (
            current in first.lower()
            or current in last.lower()
            or current in player.lower()
        ):
            autocomplete_list.append(app_commands.Choice(name=player, value=player))

    return autocomplete_list[:25]


# Command: Remove a hate mapping
# Validates input, checks for existence, and updates the database
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
    Remove a player hate mapping from the hate list.
    """
    # Validate input format
    if " " not in mentioned_player or " " not in hated_player:
        await interaction.response.send_message(
            "Invalid format. Both players must be in 'First Last' format (e.g., 'Connor McDavid')."
        )
        return

    mentioned_first, mentioned_last = mentioned_player.split(" ", 1)
    hated_first, hated_last = hated_player.split(" ", 1)

    # Check if the mapping exists
    if [mentioned_first, mentioned_last, hated_first, hated_last] not in player_hate:
        await interaction.response.send_message(f"Mapping not found in the hate list.")
        return

    # Remove from database
    connect = sqlite3.connect(PLAYER_LISTS)
    cursor = connect.cursor()
    cursor.execute(
        """
        DELETE FROM player_hate WHERE mentioned_first_name=? AND mentioned_last_name=? AND hated_first_name=? AND hated_last_name=?
        """,
        (mentioned_first, mentioned_last, hated_first, hated_last),
    )
    connect.commit()
    connect.close()

    # Update the in-memory hate list
    update_player_hate()

    await interaction.response.send_message(
        f"Removed mapping: When `{mentioned_player}` is mentioned, `{hated_player}` will no longer be hated."
    )


# Autocomplete for the 'mentioned_player' parameter in remove_hated_player_map
# Suggests player names as the user types
@remove_hated_player_map.autocomplete("mentioned_player")
async def remove_mentioned_player_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    current = current.lower()
    autocomplete_list = []
    for mentioned_first, mentioned_last, hated_first, hated_last in player_hate:
        player = f"{mentioned_first} {mentioned_last}"
        if (
            current in player.lower()
            and app_commands.Choice(name=player, value=player) not in autocomplete_list
        ):
            autocomplete_list.append(app_commands.Choice(name=player, value=player))
    return autocomplete_list[:25]


# Autocomplete for the 'hated_player' parameter in remove_hated_player_map
# Suggests player names as the user types
@remove_hated_player_map.autocomplete("hated_player")
async def remove_hated_player_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    current = current.lower()
    autocomplete_list = []
    for mentioned_first, mentioned_last, hated_first, hated_last in player_hate:
        player = f"{hated_first} {hated_last}"
        if (
            current in player.lower()
            and app_commands.Choice(name=player, value=player) not in autocomplete_list
        ):
            autocomplete_list.append(app_commands.Choice(name=player, value=player))
    return autocomplete_list[:25]


# Event: Handles errors in command execution
# Sends the error message to the user
@bot.event
async def on_command_error(interaction: discord.Interaction, error: Exception):
    """
    Handle command errors.
    """
    await interaction.response.send_message(f"An error occurred: {error}")


# Main entry point: Starts the bot
# Only runs if this file is executed directly
if __name__ == "__main__":

    # print(f"Player Options: {player_options[0:10]} ... {player_options[-10:]}")
    # print(f"Player Hate: {player_hate[0:10]} ... {player_hate[-10:]}")
    bot.run(BOT_TOKEN)


# %%
def populate_player_options(PLAYER_LISTS: str):
    """
    Function that populates the player_options table with all active NHL players.
    Uses nhlpy to fetch rosters for all teams
    THIS IS A COMPUTATIONALLY INTENSE PROCESS, CALL FUNCTION SPARINGLY
    """
    import nhlpy, json, dateutil, sqlite3

    nhl_c = nhlpy.NHLClient()._http_client
    teams_api = nhlpy.api.teams.Teams(nhl_c)  # type: ignore[reportAttributeAccessIssue]
    teams = teams_api.teams_info()
    t = []

    for team in teams:
        t.append(team["abbr"])

    teams = t
    # print(json.dumps(teams, indent=4))

    connect = sqlite3.connect(PLAYER_LISTS)
    cursor = connect.cursor()

    # Clear the table 'player_options' of all rows before adding new players
    cursor.execute("DELETE FROM player_options")

    for team in teams:
        t = json.loads(
            json.dumps(
                nhlpy.api.teams.Teams(nhl_c).roster(  # type: ignore[reportAttributeAccessIssue]
                    team,
                    f"{dateutil.utils.today().year-1}{dateutil.utils.today().year}",  # type: ignore[reportAttributeAccessIssue]
                ),
                indent=4,
            )
        )
        print(team)
        for fwd in t["forwards"]:

            cursor.execute(
                """
                INSERT INTO player_options (first_name, last_name)
                VALUES (?, ?)
                """,
                (fwd["firstName"]["default"], fwd["lastName"]["default"]),
            )
            # print(f"{fwd['firstName']['default']} {fwd['lastName']['default']}")
            pass
        for dfs in t["defensemen"]:
            cursor.execute(
                """
                INSERT INTO player_options (first_name, last_name)
                VALUES (?, ?)
                """,
                (dfs["firstName"]["default"], dfs["lastName"]["default"]),
            )
            # print(f"{dfs['firstName']['default']} {dfs['lastName']['default']}")
            pass

        for gtd in t["goalies"]:
            cursor.execute(
                """
                INSERT INTO player_options (first_name, last_name)
                VALUES (?, ?)
                """,
                (gtd["firstName"]["default"], gtd["lastName"]["default"]),
            )
            # print(f"{gtd['firstName']['default']} {gtd['lastName']['default']}")
            pass

    connect.commit()
    connect.close()


# populate_player_options("Player Lists.db")
