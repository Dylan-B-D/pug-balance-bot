import discord
import re
import json
import trueskill
import random
import subprocess
import configparser
import time
import asyncio
import asyncssh
import pytz
from typing import Union
from discord import Member
from collections import Counter
from discord.ext import commands, tasks
from discord import Embed, Colour
from datetime import datetime, timedelta
from data.player_mappings import player_name_mapping
from data.map_name_mapping import map_name_mapping
from data.map_url_mapping import map_url_mapping
from data.map_weights import map_weights
from data.user_roles import privileged_users, bot_admins
from data.map_commands import map_commands, map_commands_arena
from modules.data_managment import (fetch_data, count_games_for_ids)
from modules.rating_calculations import (calculate_ratings)
from modules.team_logic import (balance_teams)
from modules.embeds_formatting import (create_embed, add_maps_to_embed, format_time)
from modules.charts import (create_map_weights_chart, create_rolling_percentage_chart, plot_game_lengths)
from modules.utilities import (get_user_id_by_name, check_bot_admin)


# ==============================
# CONFIGURATION
# ==============================

# Config file parsing
config = configparser.ConfigParser()
config.read('config.ini')
TOKEN = config['Bot']['Token']

# Bot command-related constants
MIN_REACTIONS = 9
MIN_REACTIONS_MAP = 5

# Bot command intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Bot initialization
bot = commands.Bot(command_prefix='!', intents=intents)
bot.remove_command('help')

# ==============================
# DATA STORES & MAPPINGS
# ==============================

# Message and result storage
last_messages = {}
most_recent_matched_ids = {}
matched_results_store = {}
substitution_store = {}
game_history_cache = {}

# Command mapping
reverse_map_commands = {v: k for k, v in map_commands.items()}

# Random map selection based on weights
maps, weights = zip(*map_weights.items())
selected_map = random.choices(maps, weights, k=1)[0]

# Cache for fetched servers list
cache = None


# ==============================
# FUNCTIONS
# ==============================



@bot.event
async def on_ready():
    print(f'We have logged in as {bot.user}')

    await update_cache()
    update_cache.start()

async def handle_substitution(message, start_date, end_date):
    # Extract player names from the substitution message
    player_names = re.findall(r'`([^`]+) has been substituted with ([^`]+)`', message.content)
    if not player_names:
        return  # Exit if no player names were found

    # Fetching ids for both the old and the new player
    old_player_name, new_player_name = player_names[0]
    old_player_id = get_user_id_by_name(message.guild, old_player_name)
    new_player_id = get_user_id_by_name(message.guild, new_player_name)

    embed_description = f"{old_player_name} ({old_player_id if old_player_id else 'Unknown'}) has been substituted with {new_player_name} ({new_player_id if new_player_id else 'Unknown'})"
    substitution_store[message.channel.id] = embed_description

    channel_id = message.channel.id
    if channel_id in most_recent_matched_ids:
        matched_results = most_recent_matched_ids[channel_id]
        matched_ids = matched_results.get('matched_ids', [])
        captains = matched_results.get('captains', [])

        # Directly replace the old player's ID with the new player's ID in matched_ids
        for index, user_id_str in enumerate(matched_ids):
            user_id = int(re.search(r'\((\d+)\)', user_id_str).group(1))
            if user_id == old_player_id:
                matched_ids[index] = user_id_str.replace(str(old_player_id), str(new_player_id))
                break

        # If the substituted player was a captain, then the new player should also be a captain.
        for index, captain_id in enumerate(captains):
            if captain_id == old_player_id:
                captains[index] = new_player_id  # directly use the integer ID for captains
                break
        
        # Update the stored matched_results with the modified matched_ids and captains
        matched_results['matched_ids'] = matched_ids
        matched_results['captains'] = captains
        most_recent_matched_ids[channel_id] = matched_results

        # Retrieve the required data again and create a new balanced match embed
        data = fetch_data(start_date, end_date, 'NA')
        player_ratings, _, _, _ = calculate_ratings(data, queue='NA')
        players = []
        for user_id_str in matched_ids + matched_results.get('matched_strings', []):
            user_id = int(re.search(r'\((\d+)\)', user_id_str).group(1))
            name = player_name_mapping.get(user_id)
            if not name:
                member = message.channel.guild.get_member(user_id)
                name = member.display_name if member else str(user_id)
            default_rating = trueskill.Rating(mu=15, sigma=5)
            players.append({'id': user_id, 'name': name, 'mu': player_ratings.get(user_id, default_rating).mu})

        await send_balanced_teams(message.channel, players, player_ratings, captains)

@bot.command()
async def startgame(ctx):
    allowed_user_id = 252190261734670336
    
    if ctx.author.id != allowed_user_id:
        embed = Embed(title="Permission Denied", description="This command is restricted.", color=0xff0000)
        await ctx.send(embed=embed)
        return
    
    """Sends an embed to manually trigger the start_map function with the map 'walledin'."""
    embed = Embed(title="Game Details", 
                  description="**Teams:**\nA Team: Player1, Player2, Player3\nB Team: Player4, Player5, Player6\n\n**Maps:** elysianbattlegrounds", 
                  color=0x00ff00)
    await ctx.send(embed=embed)


async def start_map(ctx, map_shortcut):
    """Starts the map based on the provided shortcut."""
    try:
        # Establishing SSH connection and sending the command
        connect_time = time.time()  # Timer before connecting to SSH
        async with asyncssh.connect(config['SSH']['IP'], username=config['SSH']['Username'], password=config['SSH']['Password']) as conn:
            print(f"SSH Connect Time: {time.time() - connect_time} seconds")
            
            if map_shortcut in map_commands:
                command = f"~/pugs.sh -m {map_shortcut}"
            elif map_shortcut in map_commands_arena:
                command = f"~/2v2b.sh -m {map_shortcut}"
            else:
                await ctx.send("Invalid map shortcut")
                return
            
            exec_time = time.time()  # Timer before executing command over SSH
            result = await conn.run(command)
            print(f"SSH Command Execution Time: {time.time() - exec_time} seconds")
            
            output, error = result.stdout, result.stderr

            # Handling the output or error from the SSH command
            if output:
                user_id = ctx.author.id if hasattr(ctx, 'author') else "System"  # Use 'System' as a fallback if ctx does not have an 'author' (e.g., when called from on_message)
                user_name = player_name_mapping.get(user_id, str(ctx.author) if hasattr(ctx, 'author') else "System")  # Use the discord username or 'System' as fallback
                
                embed = Embed(title=f"Map changed successfully by {user_name}", description=f"```{output}```", color=0x00ff00)
                message = await ctx.send(embed=embed)
                await asyncio.sleep(1)  # Sleep for 1 second to ensure that the message is sent before editing
                
                # Start the countdown
                for i in range(35, 0, -1):
                    # Edit the description of the embed to include the countdown
                    embed.description = f"```{output}```\nServer joinable in {i} seconds..."
                    await message.edit(embed=embed)
                    await asyncio.sleep(1)  # Sleep for 1 second between each edit
                
                # Once the countdown is over, edit the message to indicate that the server is joinable
                embed.description = f"```{output}```\nServer is now joinable!"
                await message.edit(embed=embed)
            elif error:
                embed = Embed(title="Error changing map", description=f"```{error}```", color=0xff0000)
                await ctx.send(embed=embed)
            else:
                embed = Embed(title="Map changed successfully", color=0x00ff00)
                await ctx.send(embed=embed)
            
    except Exception as e:
        await ctx.send(f"Error changing map: {e}")

@bot.event
async def on_message(message):
    await bot.process_commands(message)

    # Update the last message for the channel
    last_messages[message.channel.id] = message.content

    end_date = datetime.now()
    start_date = datetime(2018, 1, 1)

    if message.content.startswith("`") and " has been substituted with " in message.content and message.content.endswith("`"):
        await handle_substitution(message, start_date, end_date)

    if message.embeds:
        embed = message.embeds[0]
        # Check if 'Captains:' is in the embed description
        if embed.description and "Captains:" in embed.description:
            # Check the last message in the channel to see if it's the 2v2 message
            last_msg = last_messages.get(message.channel.id)
            if last_msg == "`Game '2v2' has begun`":
                print("2v2 game detected, skipping ID matching.")
                return


    if message.embeds:
        embed = message.embeds[0]
        # Check if 'Captains:' is in the embed description instead of the title
        if embed.description and "Captains:" in embed.description:
            data = fetch_data(start_date, end_date, 'NA')
            player_ratings, _, _, _ = calculate_ratings(data, queue='NA')
            matched_results = await match_ids(message.channel, embed)
            captains = matched_results.get('captains', [])

            matched_ids = matched_results.get('matched_ids', []) + matched_results.get('matched_strings', [])
            if not matched_ids:
                print("No matched IDs found.")
                return

            players = []
            for user_id_str in matched_ids:
                user_id = int(re.search(r'\((\d+)\)', user_id_str).group(1))
                name = player_name_mapping.get(user_id)
                
                if not name:
                    member = message.channel.guild.get_member(user_id)
                    name = member.display_name if member else str(user_id)

                default_rating = trueskill.Rating(mu=15, sigma=5)
                players.append({'id': user_id, 'name': name, 'mu': player_ratings.get(user_id, default_rating).mu})


            matched_results_store[message.channel.id] = matched_results
            # await send_player_info(message.channel, matched_results['matched_ids'] + matched_results['matched_strings'], data, player_ratings, player_name_mapping)
            await send_balanced_teams(message.channel, players, player_ratings, captains)
    
    if message.embeds:
        embed = message.embeds[0]
        description = embed.description
        if description and "**Maps:**" in description:
            map_name = description.split("**Maps:**")[1].strip().lower()  # Convert map name to lowercase

            # First try exact match
            map_shortcut = [shortcut for shortcut, full_name in map_commands.items() if map_name == full_name.lower()]
            if not map_shortcut:
                map_shortcut = [shortcut for shortcut, full_name in map_commands_arena.items() if map_name == full_name.lower()]
            
            # If exact match not found, try starts with
            if not map_shortcut:
                map_shortcut = [shortcut for shortcut, full_name in map_commands.items() if map_name.startswith(full_name.lower())]
            if not map_shortcut:
                map_shortcut = [shortcut for shortcut, full_name in map_commands_arena.items() if map_name.startswith(full_name.lower())]

            if map_shortcut:
                await start_map(message.channel, map_shortcut[0])  # Start the detected map



@bot.command()
async def clear(ctx):
    utc = pytz.UTC
    current_time = datetime.utcnow().replace(tzinfo=utc)  # timezone-aware datetime with UTC timezone
    minutes_ago = current_time - timedelta(minutes=5)

    # Only allow the user with the specific ID to use this command
    if ctx.author.id != 252190261734670336:
        await ctx.send("You do not have permission to use this command.")
        return
    
    # Delete bot's messages from the last 10 minutes
    async for message in ctx.channel.history(limit=100):  # Adjust limit if necessary
        if message.author == bot.user and message.created_at > minutes_ago:
            await message.delete()
            await asyncio.sleep(0.7)  # Delay of 700ms between deletions

    confirmation = await ctx.send("Bot messages from the last 10 minutes have been deleted.")
    await asyncio.sleep(5)  # Let the confirmation message stay for 5 seconds
    await confirmation.delete()



async def getids(channel, embed):
    matched_results = await match_ids(channel, embed)
    
    if matched_results is None:
        return {'matched_ids': [], 'matched_strings': []}
    
    if not matched_results.get('matched_ids', []) and not matched_results.get('matched_strings', []):
        print("No matched IDs found.")
        return {'matched_ids': [], 'matched_strings': []}
    
    return matched_results



async def match_ids(channel, embed):
    global most_recent_matched_ids
    matched_ids = []
    matched_strings = []
    unmatched_names = []
    captains = []
    
    regex_split = re.compile(r'[,&\s]+')
    regex_mention = re.compile(r'<@(\d+)>')
    
    content_string = embed.description
    
    # Extract captains and their ids
    captains_section = re.search(r'Captains:.*?\n', content_string)
    if captains_section:
        for mention in regex_mention.findall(captains_section.group()):
            captains.append(int(mention))

    captains_string = re.search(r'Captains: ([^&]+&[^&]+)', content_string)
    if captains_string:
        captains_string = captains_string.group(1)
        for mention in regex_mention.findall(captains_string):
            member = channel.guild.get_member(int(mention))
            if member:
                user_id_string = f"{member.display_name} ({mention})"
                matched_ids.append(user_id_string)
                content_string = content_string.replace(f'<@{mention}>', '')
                
    content_string = content_string.replace('Captains:', '').strip()

    # Create a dictionary mapping display names to their ids for all members of the guild
    members_names = {member.display_name: member.id for member in channel.guild.members}

    # Split by commas and strip whitespace to get potential full names
    potential_names = [name.strip() for name in content_string.split(',')]
    
    # Match by names in the list of words extracted from content_string
    for word in potential_names:
        user_id = get_user_id_by_name(channel.guild, word)
        if user_id and f"{word} ({user_id})" not in matched_ids:
            matched_ids.append(f"{word} ({user_id})")
            content_string = content_string.replace(word, '', 1)

    # Match by substring only if not matched as a full name
    for name, member_id in members_names.items():
        if name in content_string and f"{name} ({member_id})" not in matched_ids:
            matched_strings.append(f"{name} ({member_id})")
            content_string = content_string.replace(name, '', 1)
            
    # Find unmatched names
    for word in regex_split.split(content_string.strip()):
        if word and word not in ('&', '@'):
            unmatched_names.append(word)
            
    matched_results = {
        'matched_ids': matched_ids,
        'matched_strings': matched_strings,
        'unmatched_names': unmatched_names,
        'captains': captains
    }
    most_recent_matched_ids[channel.id] = matched_results
    return matched_results



def pick_two_maps():
    selected_maps = random.choices(maps, weights, k=2)
    while selected_maps[0] == selected_maps[1]:
        selected_maps = random.choices(maps, weights, k=2)
    return selected_maps

async def send_balanced_teams(channel, players, player_ratings, captains):
    try:
        balanced_teams_list = balance_teams(players, captains)
        if not balanced_teams_list or len(balanced_teams_list) <= 1:
            print("Could not find balanced teams.")
            return

        next_index = 1
        balanced_teams = balanced_teams_list[0]

        # Select two maps
        current_map, next_map = pick_two_maps()
        map_url = map_url_mapping.get(current_map)

        msg = await channel.send(embed=create_embed(balanced_teams_list[0], captains, player_ratings, map_name=current_map, map_url=map_url, description=f"Vote 🗺️ to skip to {next_map}"))
        await msg.add_reaction('🔁')
        await msg.add_reaction('🗺️')

        player_ids = set(player['id'] for player in players)
        map_voters = set()

        def check(reaction, user):
            return (
                user.id in player_ids
                and user != msg.author
                and str(reaction.emoji) in ['🔁', '🗺️']
                and reaction.message.id == msg.id
            )

        map_changed = False
        while next_index < len(balanced_teams_list):
            reaction, user = await bot.wait_for('reaction_add', check=check)
            emoji = str(reaction.emoji)

            if emoji == '🔁' and reaction.count-1 >= MIN_REACTIONS:
                balanced_teams = balanced_teams_list[next_index]
                msg = await channel.send(embed=create_embed(balanced_teams, captains, player_ratings, title=f'Rebalanced Teams #{next_index}', map_name=current_map, map_url=map_url, description=f"Vote 🗺️ to skip to {next_map}"))
                if next_index < len(balanced_teams_list) - 1:
                    await msg.add_reaction('🔁')
                if not map_changed:
                    await msg.add_reaction('🗺️')
                next_index += 1

            elif emoji == '🗺️' and user.id not in map_voters:
                map_voters.add(user.id)
                if reaction.count-1 >= MIN_REACTIONS_MAP and not map_changed:
                    map_changed = True
                    current_map, next_map = next_map, random.choice([map_ for map_ in maps if map_ != current_map])
                    map_url = map_url_mapping.get(current_map)

                    if next_index == 1:
                        title = 'Balanced Teams'
                    else:
                        title = f'Rebalanced Teams #{next_index - 1}'

                    await msg.edit(embed=create_embed(balanced_teams, captains, player_ratings, title=title, map_name=current_map, map_url=map_url))
                    # Clear existing reactions since the map can't be changed again
                    await msg.clear_reactions()
                    await msg.add_reaction('🔁')

    except Exception as e:
        print(f"Error in sending balanced teams info: {e}")




@bot.command()
async def match(ctx):
    # Specified user ID
    allowed_user_id = 252190261734670336
    
    if ctx.author.id != allowed_user_id:
        embed = Embed(title="Permission Denied", description="This command is restricted.", color=0xff0000)
        await ctx.send(embed=embed)
        return
    
    # Create the embed
    embed = discord.Embed(
        title="",
        description="**Captains: <@252190261734670336> & <@1146906869827391529>**\n"
                    "Iced, Jive, Mastin, Nerve, frogkabobs, CRO, Theobald the Bird, freefood, JackTheBlack, Vorpalkitty[Snipe Intern2],Karu , Karuciel",
        color=discord.Color.blue()
    )

    # Send both the content and embed in one message
    await ctx.send(content="`Game 'PUG' has begun`", embed=embed)


@bot.command(name='info')
async def info(ctx):
    filename = create_map_weights_chart(map_weights)

    # Create a discord.File object
    file = discord.File(filename, filename="map_weights_chart.png")
    
    embed = discord.Embed(title="Bot Information", description="Details about bot settings and configurations.", color=discord.Colour.blue())
    
    # Adding bot configuration info
    embed.add_field(name="Votes Required to Rebalance Teams", value=str(MIN_REACTIONS), inline=False)
    embed.add_field(name="Votes Required to Skip Map", value=str(MIN_REACTIONS_MAP), inline=False)
    
    # Set the image url to reference the attached file
    embed.set_image(url="attachment://map_weights_chart.png")
    
    # Send the embed and the file
    await ctx.send(file=file, embed=embed)
    
@bot.command()
async def kill(ctx, container: str = None):
    # Only proceed if the author's ID is in the allowed list
    if ctx.author.id not in privileged_users:
        embed = Embed(title="Permission Denied", description="Elevated admin permissions required to execute this command.", color=0xff0000)
        await ctx.send(embed=embed)
        return

    if container not in ["2v2", "pug", "all"]:
        embed = Embed(title="Invalid Argument", description="Please specify a valid container to kill (`2v2`, `pug`, or `all`).", color=0xff0000)
        await ctx.send(embed=embed)
        return

    containers_killed = []

    # Connect to the remote server via SSH
    try:
        async with asyncssh.connect(config['SSH']['IP'], username=config['SSH']['Username'], password=config['SSH']['Password']) as conn:
            if container == "2v2" or container == "all":
                result_2v2 = await conn.run('docker kill taserver_2v2b_4')
                if not result_2v2.stderr:
                    containers_killed.append("2V2")
            if container == "pug" or container == "all":
                result_pug = await conn.run('docker kill PUGS')
                if not result_pug.stderr:
                    containers_killed.append("PUG")
            
            killed_containers_str = " and ".join(containers_killed)
            user_id = ctx.author.id
            user_name = player_name_mapping.get(user_id, str(ctx.author))  # Use the discord username as fallback
            embed_title = f"{killed_containers_str} container{'s' if len(containers_killed) > 1 else ''} shut down successfully by {user_name}"
            embed_description = f"Container{'s' if len(containers_killed) > 1 else ''} {killed_containers_str} have been shut down."
            embed = Embed(title=embed_title, description=embed_description, color=0x00ff00)
            await ctx.send(embed=embed)

    except Exception as e:
        embed = Embed(title="Error", description=f"Error shutting down containers: {e}", color=0xff0000)
        await ctx.send(embed=embed)
        
@bot.command()
async def setmap(ctx, map_shortcut: str = None):
    start_time = time.time()  # Start timer when the command is triggered
    print(f"Start Time: {start_time}")
    
    if not await check_bot_admin(ctx):
        return

    
    # Convert map_shortcut to lowercase if it is not None
    map_shortcut = map_shortcut.lower() if map_shortcut is not None else None
    
    # If map_shortcut is None or not in map_commands and map_commands_arena, send the list of valid commands
    if map_shortcut is None or (map_shortcut not in map_commands and map_shortcut not in map_commands_arena):
        embed_creation_time = time.time()  # Timer before creating the embed
        embed = Embed(title="Map Commands", description="Use '!setmap `id`' to start maps.", color=0x00ff00)
        
        add_maps_to_embed(embed, map_commands, "CTF Maps", 3)
        embed.add_field(name='\u200b', value='\u200b', inline=False)
        add_maps_to_embed(embed, map_commands_arena, "Arena Maps", 1)
        
        await ctx.send(embed=embed)
        
        embed_sent_time = time.time()  # Timer after sending the embed
        print(f"Embed Creation and Send Time: {embed_sent_time - embed_creation_time} seconds")
        return
    
    # Start the map using the start_map function
    await start_map(ctx, map_shortcut)

    end_time = time.time()  
    print(f"Total Execution Time: {end_time - start_time} seconds")


@bot.command()
async def help(ctx):
    
    # Organized commands by categories
    commands_dict = {
        "Restricted": {
            "!clear": "Clear the bot's messages from the last 10 mins.",
            "!match": "Simulate a game starting.",
            "!ranks": "Show player ranks.",
            "!startgame": "Simulate when teams have been picked."
        },
        "Elevated Admin": {
            "!kill": "Shutdown docker containers. Usage: `!kill [container]`"
        },
        "Admin": {
            "!debug": "Debug ID matching results for previous game.",
            "!setmap": "Set the PUG/2v2 map. Returns list of map IDs if no parameter given. Usage: `!setmap [map_id]`"
        },
        "Others": {
            "!gamelengths": "Shows a graph of game lengths over time.",
            "!gamehistory": "Shows the last few maps played (Max 10). Usage: `!gamehistory [optional_history_count]`",
            "!help": "Shows a list of commands and how to use them.",
            "!info": "Shows map weighting for generated games, and voting thresholds.",
            "!listadmins": "Lists all the admins.",
            "!servers": "Shows server list for 'PUG Login'.",
            "!serverstats": "Gets some simple stats for the PUG queues.",
            "!setname": "Sets the player's username which is used to display stats/balanced teams etc. Usage: `!setname [@User][new_username]`",
            "!showstats": "Shows some basic stats for a given player. Usage: `!showstats [@User]`",
            "!status": "Shows server status for PUGs with no param, or shows status for Mixers with a param of '2'. Usage: `!status [optional_server_index]`"
        }
    }

    for category, commands in commands_dict.items():
        embed = Embed(title=f"TA-Bot Commands - {category}", description="", color=0x00ff00)
        
        for cmd, desc in commands.items():
            embed.add_field(name=cmd, value=desc, inline=False)

        await ctx.send(embed=embed)



@bot.command(name='debug')
async def debug(ctx):
    # Check if the author of the command is in the list of admins
    if not await check_bot_admin(ctx):
        return


    channel_id = ctx.channel.id
    debug_embed = discord.Embed(title="Debug Information", color=discord.Color.blue())

    # Substitution Information
    embed_description = substitution_store.get(channel_id)
    if embed_description:
        player_names = re.findall(r'([^()]+) \((\d+)\) has been substituted with ([^()]+) \((\d+)\)', embed_description)
        if player_names:
            old_player_name, old_player_id, new_player_name, new_player_id = player_names[0]
            debug_embed.add_field(name="Substituted Player", value=f"Name: {old_player_name}\nID: {old_player_id}", inline=True)
            debug_embed.add_field(name="Replacement Player", value=f"Name: {new_player_name}\nID: {new_player_id}", inline=True)

    # Matched Results
    matched_results = matched_results_store.get(channel_id)
    if matched_results:
        matched_ids = matched_results.get('matched_ids', [])
        if matched_ids:
            matched_players = "\n".join([f"Name: {name.split(' (')[0]}\nID: {name.split('(')[1].rstrip(')')}" for name in matched_ids])
            debug_embed.add_field(name="Matched Players", value=matched_players, inline=False)

        unmatched_names = matched_results.get('unmatched_names', [])
        if unmatched_names:
            unmatched_players = "\n".join(unmatched_names)
            debug_embed.add_field(name="Unmatched Names", value=unmatched_players, inline=False)

        captains = matched_results.get('captains', [])
        if captains:
            captain_names = "\n".join([str(captain) for captain in captains])
            debug_embed.add_field(name="Captains", value=captain_names, inline=False)
    else:
        debug_embed.add_field(name="Matched Results", value="No matched results found for this channel.", inline=False)

    await ctx.send(embed=debug_embed)



@bot.command(name='serverstats')
async def server_stats(ctx):
    try:
        start_date_total = datetime.min
        end_date = datetime.now()
        start_date_7_days = end_date - timedelta(days=7)
        
        total_data_2v2 = fetch_data(start_date_total, end_date, '2v2')
        total_data_NA = fetch_data(start_date_total, end_date, 'NA')
        total_data = total_data_2v2 + total_data_NA
        last_7_days_data = [game for game in total_data if start_date_7_days <= datetime.fromtimestamp(game['timestamp'] / 1000) <= end_date]
        
        total_games = len(total_data)
        unique_players_total = len(set(player['user']['id'] for game in total_data for player in game['players']))
        
        games_last_7_days = len(last_7_days_data)
        unique_players_last_7_days = len(set(player['user']['id'] for game in last_7_days_data for player in game['players']))
        
        # Find top players with most games in the last 7 days
        player_counter = Counter(player['user']['id'] for game in last_7_days_data for player in game['players'])
        top_players = player_counter.most_common(5)
        
        top_players_text = ""
        for i, (player_id, count) in enumerate(top_players, start=1):
            player_id = int(player_id)  # Convert to int if player_id is a string in your data
            # Directly use the mapping for the username
            player_name = player_name_mapping.get(player_id, f"Unknown User ({player_id})")
            top_players_text += f"{i}. {player_name} - {count} games\n"
        
        # Create and send embed
        embed = Embed(title="Server Stats", color=Colour.blue())
        embed.add_field(name="Total Games", value=str(total_games), inline=True)
        embed.add_field(name="Unique Players", value=str(unique_players_total), inline=True)
        embed.add_field(name='\u200B', value='\u200B', inline=False)
        embed.add_field(name="Last 7 Days Games", value=str(games_last_7_days), inline=True)
        embed.add_field(name="Last 7 Days Players", value=str(unique_players_last_7_days), inline=True)
        embed.add_field(name='\u200B', value='\u200B', inline=False)
        embed.add_field(name="Top Players (Last 7 Days)", value=top_players_text.strip(), inline=False)

        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Error fetching server stats: {e}")




@tasks.loop(seconds=45)
async def update_cache():
    global cache
    try:
        # Create subprocess.
        process = await asyncio.create_subprocess_exec(
            'node', 'ta-network-api/index.js',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        
        # Wait for the subprocess to finish and get the stdout and stderr.
        stdout, stderr = await process.communicate()
        
        # Check the returncode to see if subprocess exited with an error.
        if process.returncode != 0:
            raise Exception(f"Subprocess exited with error: {stderr.decode().strip()}")
        
        cache = json.loads(stdout.decode().strip())

        if cache:
            max_players_server = max(cache, key=lambda server: server['numberOfPlayers'])
            if max_players_server['numberOfPlayers'] > 0:
                server_name = max_players_server['name']
                max_players = max_players_server['maxNumberOfPlayers']
                current_players = max_players_server['numberOfPlayers']
                scores = "N/A"
                time_remaining = "N/A"
                
                if 'scores' in max_players_server:
                    scores = f"{max_players_server['scores']['bloodEagle']} - {max_players_server['scores']['diamondSword']}"
                
                if 'timeRemaining' in max_players_server:
                    time_remaining = format_time(max_players_server['timeRemaining'])
                
                activity_message = f"Players in {server_name}: {current_players}/{max_players}, Score: {scores}, Time Remaining: {time_remaining}"
            else:
                activity_message = f"All Servers on 'PUG Login' Empty"
            
            await bot.change_presence(activity=discord.Game(name=activity_message))
        
    except Exception as e:
        print(f"An error occurred while updating the cache: {e}")


@update_cache.after_loop
async def after_update_cache():
    if update_cache.failed():
        print('The task to update the cache has failed!')

@bot.command(name='servers')
async def servers(ctx):
    global cache 
    try:
        if cache:
            print("Serving from cache")
            servers = cache
        else:
            print("Cache is not available, fetching...")
            result = subprocess.run(['node', 'ta-network-api/index.js',], capture_output=True, text=True, check=True)
            servers = json.loads(result.stdout)

        embed = Embed(title='\'PUG Login\' Game Servers', description='List of available game servers', color=0x00ff00)
        
        for server in servers:
            if server['numberOfPlayers'] == 0:
                server_info = (
                    f"Players: {server['numberOfPlayers']}/{server['maxNumberOfPlayers']}\n"
                )
                if 'map' in server:
                    server_info += f"Map: {server['map']['name']} ({server['map']['gamemode']})\n"
            else:
                server_info = (
                    f"Region: {server['region']['name']}\n"
                    f"Players: {server['numberOfPlayers']}/{server['maxNumberOfPlayers']}\n"
                )
                if 'scores' in server:
                    server_info += f"Scores: BE: {server['scores']['bloodEagle']} - {server['scores']['diamondSword']} :DS\n"
                    
                if 'map' in server:
                    server_info += f"Map: {server['map']['name']} ({server['map']['gamemode']})\n"
                    
                if 'timeRemaining' in server:
                    server_info += f"Time Remaining: {format_time(server['timeRemaining'])}\n"

                if 'specificServerInfo' in server and 'players' in server['specificServerInfo']:
                    player_info = {'Blood Eagle': [], 'Diamond Sword': []} 
                    for player in server['specificServerInfo']['players']:
                        team_name = player.get('team', 'Unknown Team')
                        player_info[team_name].append(player['name'])
                    
                    for team, players in player_info.items():
                        server_info += f"{team}: {', '.join(players) if players else 'No players'}\n"

            embed.add_field(name=server['name'], value=server_info, inline=False)

        await ctx.send(embed=embed)
    except subprocess.CalledProcessError as e:
        error_embed = Embed(title="Error", description="An error occurred while fetching the server information. Please try again later.", color=0xFF0000)
        await ctx.send(embed=error_embed)
    except json.JSONDecodeError as e:
        error_embed = Embed(title="JSON Error", description="There was an issue decoding the server information.", color=0xFF0000)
        await ctx.send(embed=error_embed)
    except Exception as e:
        error_embed = Embed(title="Unexpected Error", description="An unexpected error occurred. Please try again later.", color=0xFF0000)
        await ctx.send(embed=error_embed)

countdown_task = None

async def countdown(message, time_remaining, server_with_id_3):
    try:
        while time_remaining > 0:
            if server_with_id_3['scores']['diamondSword'] == 7:
                await message.edit(content="Diamond Sword wins!")
                return
            if server_with_id_3['scores']['bloodEagle'] == 7:
                await message.edit(content="Blood Eagle wins!")
                return
            
            embed = message.embeds[0]  # Get the existing embed from the message
            mins, secs = divmod(time_remaining, 60)
            embed.set_field_at(2, name='Time Remaining', value=f"{mins}m {secs}s", inline=True)  # Update the time_remaining field in the embed
            await message.edit(embed=embed)
            
            await asyncio.sleep(1)
            time_remaining -= 1
        
        await message.edit(content="Game Over!")
    except asyncio.CancelledError:
        pass  # Task has been cancelled, do nothing
    except Exception as e:
        await message.edit(content=f"An unexpected error occurred: {e}")

@bot.command(name='status')
async def status(ctx, server_id: int = None):  # Add an optional server_id argument with a default value of None
    global cache
    global countdown_task

    target_server_id = 4 if server_id == 2 else 3
    
    try:
        if countdown_task:
            countdown_task.cancel()
        
        if cache:
            print("Serving from cache")
            servers = cache
        else:
            print("Cache is not available, fetching...")
            result = subprocess.run(['node', 'ta-network-api/index.js',], capture_output=True, text=True, check=True)
            servers = json.loads(result.stdout)
        
        server = next((server for server in servers if server['id'] == target_server_id), None)
        
        if server and server['numberOfPlayers'] > 0:
            embed = Embed(color=0x00ff00)
            
            if server_id == 2:
                embed.title = "Mixer Status"  # Set title if server_id == 2
            
            if 'scores' in server:
                embed.add_field(name='Scores', value=f"DS: {server['scores']['diamondSword']}\nBE: {server['scores']['bloodEagle']}", inline=True)
                
            embed.add_field(name='\u200b', value='\u200b', inline=True)  # Spacer column
            
            if 'timeRemaining' in server:
                time_remaining = server['timeRemaining']
                embed.add_field(name='Time Remaining', value=f"{format_time(time_remaining)}", inline=True)
            
            if 'map' in server:
                map_info = f"{server['map']['name']}"  # Only display map name, exclude gamemode
                embed.add_field(name='Map', value=map_info, inline=True)  # Updated column for Map Information

            message = await ctx.send(embed=embed)
            
            if time_remaining < 1500:
                countdown_task = asyncio.create_task(countdown(message, time_remaining, server))
        
    except subprocess.CalledProcessError as e:
        error_embed = Embed(title="Error", description="An error occurred while fetching the server information. Please try again later.", color=0xFF0000)
        await ctx.send(embed=error_embed)
    except json.JSONDecodeError as e:
        error_embed = Embed(title="JSON Error", description="There was an issue decoding the server information.", color=0xFF0000)
        await ctx.send(embed=error_embed)
    except Exception as e:
        error_embed = Embed(title="Unexpected Error", description="An unexpected error occurred. Please try again later.", color=0xFF0000)
        await ctx.send(embed=error_embed)





async def send_match_results(channel, matched_results):
    result_embed = Embed(title='ID Matching', colour=0x3498db)

    if matched_results['matched_ids']:

        half_len = (len(matched_results['matched_ids']) + 1) // 2  
        first_half = matched_results['matched_ids'][:half_len]
        second_half = matched_results['matched_ids'][half_len:]

        while len(first_half) < len(second_half):
            first_half.append('\u200B')
        while len(second_half) < len(first_half):
            second_half.append('\u200B')
        
        # Display the two columns
        result_embed.add_field(name='Matched IDs:', value='\n'.join(first_half) or '[Empty]', inline=True)
        result_embed.add_field(name='\u200B', value='\u200B', inline=True)  # One blank column as a spacer
        result_embed.add_field(name='Matched IDs:', value='\n'.join(second_half) or '[Empty]', inline=True)

    if matched_results['matched_strings']:
        result_embed.add_field(name='Matched IDs From Substring:', value='\n'.join(matched_results['matched_strings']), inline=False)
        
    if matched_results['unmatched_names']:
        result_embed.add_field(name='Unmatched Names:', value='\n'.join(matched_results['unmatched_names']), inline=False)

    await channel.send(embed=result_embed)


@bot.command()
async def listadmins(ctx):

    
    embed = Embed(title="Bot Admins", description="List of users with admin for TA-Bot:", color=0x00ff00)

    admin_names = sorted([player_name_mapping.get(admin_id, str(admin_id)) for admin_id in bot_admins])
    
    num_cols = 3  # Number of columns
    num_rows = -(-len(admin_names) // num_cols)  
    
    for col in range(num_cols):
        field_value = []  
        for row in range(num_rows):
            idx = col * num_rows + row  
            if idx < len(admin_names):
                field_value.append(admin_names[idx])
        
        field_name = "Admins" if col == 0 else '\u200b' 
        embed.add_field(name=field_name, value="\n".join(field_value), inline=True)
    
    await ctx.send(embed=embed)

def determine_rank(percentile):
    if percentile >= 0.99: return "Grandmaster"
    if percentile >= 0.95: return "Master"
    if percentile >= 0.85: return "Sapphire"
    if percentile >= 0.70: return "Ruby"
    if percentile >= 0.55: return "Diamond"
    if percentile >= 0.40: return "Emerald"
    if percentile >= 0.30: return "Platinum"
    if percentile >= 0.20: return "Gold"
    if percentile >= 0.10: return "Silver"
    return "Bronze"

@bot.command()
async def ranks(ctx):
    if not ctx.guild:
        await ctx.send("This command can only be used within a server.")
        return

    allowed_user_id = 252190261734670336
    
    if ctx.author.id != allowed_user_id:
        embed = Embed(title="Permission Denied", description="This command is restricted.", color=0xff0000)
        await ctx.send(embed=embed)
        return
    
    try:
        # Fetching the data and calculating ratings
        end_date = datetime.now()
        start_date = datetime(2018, 1, 1)
        data = fetch_data(start_date, end_date, 'NA')
        player_ratings, _, _, _ = calculate_ratings(data, queue='NA')

        # Transforming the player_ratings dict into a list and sorting by mu
        players_list = [{'id': user_id, 'mu': rating.mu} for user_id, rating in player_ratings.items()]
        players_list.sort(key=lambda x: x['mu'], reverse=True)
        total_players = len(players_list)

        # Correcting the percentile calculation
        for index, player in enumerate(players_list):
            player["percentile"] = 1 - (index / total_players)  # Highest-rated player gets a percentile close to 1
            player["rank"] = determine_rank(player["percentile"])

        # Group players by rank
        players_grouped_by_rank = {}
        for player in players_list:
            players_grouped_by_rank.setdefault(player["rank"], []).append(player)

        # Constants for formatting
        PLAYERS_PER_EMBED = 60  # 20 players per column, 3 columns
        PLAYERS_PER_COLUMN = 20

        for rank, players_of_rank in players_grouped_by_rank.items():
            total_players_of_rank = len(players_of_rank)

            for i in range(0, total_players_of_rank, PLAYERS_PER_EMBED):
                embed = Embed(title=f"{rank} Rankings", color=0x00ff00)
                chunk = players_of_rank[i:i+PLAYERS_PER_EMBED]

                column_data = ["", "", ""]
                for j, player in enumerate(chunk):
                    user_id = player['id']
                    mu = player['mu']
                    rank_position = i + j + 1

                    # Getting the player name from the mapping or the guild members
                    name = player_name_mapping.get(user_id)
                    if not name:
                        member = ctx.guild.get_member(user_id)
                        name = member.display_name if member else str(user_id)

                    col_idx = j // PLAYERS_PER_COLUMN
                    column_data[col_idx] += f"{rank_position}. {name} - µ: {mu:.2f}\n"

                for idx, col in enumerate(column_data):
                    if col:
                        embed.add_field(name=f"Column {idx+1}", value=col, inline=True)  # Giving each column a heading for clarity

                await ctx.send(embed=embed)

    except Exception as e:
        embed = Embed(title="Error", description="An error occurred while fetching player rankings.", color=0xff0000)
        await ctx.send(embed=embed)
        print(f"Error in !ranks command: {e}")

def time_ago(past):
    now = datetime.now()

    # Time differences in different periods
    delta_seconds = (now - past).total_seconds()
    delta_minutes = delta_seconds / 60
    delta_hours = delta_minutes / 60
    delta_days = delta_hours / 24
    delta_weeks = delta_days / 7
    delta_months = delta_days / 30.44  # Average number of days in a month
    delta_years = delta_days / 365.25  # Average number of days in a year considering leap years

    # Determine which format to use based on the period
    if delta_seconds < 60:
        return "{} second(s) ago".format(int(delta_seconds))
    elif delta_minutes < 60:
        return "{} minute(s) ago".format(int(delta_minutes))
    elif delta_hours < 24:
        return "{} hour(s) ago".format(int(delta_hours))
    elif delta_days < 7:
        return "{} day(s) ago".format(int(delta_days))
    elif delta_weeks < 4.35:  # Approximately number of weeks in a month
        return "{} week(s) ago".format(int(delta_weeks))
    elif delta_months < 12:
        return "{} month(s) ago".format(int(delta_months))
    else:
        return "{} year(s) ago".format(int(delta_years))

def calculate_last_played(player_id, game_data):
    for game in reversed(game_data):
        for player in game['players']:
            if player['user']['id'] == player_id:
                last_played = datetime.fromtimestamp(game['timestamp'] / 1000)
                return time_ago(last_played)
    return None


@bot.command()
async def showstats(ctx, *, player_input: Union[Member, str]):
    try:
        # If the input is a Discord member, use their ID directly
        if isinstance(player_input, Member):
            player_id = player_input.id
            display_name = player_name_mapping.get(player_id, player_input.display_name)  # Use the mapped name if available

        # If the input is a string, check if it's in our name mapping or do a partial match search
        elif isinstance(player_input, str):
            if player_input in player_name_mapping.values():
                player_id = next(k for k, v in player_name_mapping.items() if v == player_input)
                display_name = player_input
            else:
                player_id = next((k for k, v in player_name_mapping.items() if player_input.lower() in v.lower()), None)
                if not player_id:
                    await ctx.send(f"Cannot find a player with the name {player_input}.")
                    return
                display_name = player_name_mapping.get(player_id, player_input)  # Use the mapped name if available

        # Fetch data using the ALL queue
        start_date = datetime(2018, 1, 1)
        end_date = datetime.now()

        total_data_ALL = fetch_data(start_date, end_date, 'ALL')

        # Filter games for each queue
        total_data_NA = [game for game in total_data_ALL if game['queue']['name'] == 'PUGz']
        total_data_2v2 = [game for game in total_data_ALL if game['queue']['name'] == '2v2']

        games_for_player_NA = sum(1 for game in total_data_NA for player in game['players'] if player['user']['id'] == player_id)
        games_for_player_2v2 = sum(1 for game in total_data_2v2 for player in game['players'] if player['user']['id'] == player_id)
        games_for_player_ALL = games_for_player_NA + games_for_player_2v2

        # Assuming total_data_ALL is sorted in descending order of time (i.e., recent games first)
        last_10_games_played = sum(1 for game in total_data_ALL[:10] for player in game['players'] if player['user']['id'] == player_id)

        chart_filename_NA = create_rolling_percentage_chart(player_id, total_data_NA, 'NA')
        chart_filename_2v2 = create_rolling_percentage_chart(player_id, total_data_2v2, '2v2')

        # Create the embed for stats
        embed = Embed(title=f"{display_name}'s Stats", color=Colour.blue())
        
        last_played_str = calculate_last_played(player_id, total_data_ALL)
        if not last_played_str:
            last_played_str = "Not available"


        # Create the embed for stats
        embed = Embed(title=f"{display_name}'s Stats", color=Colour.blue())

        # Add Last Played Information
        embed.add_field(name="Last Played", value=last_played_str, inline=False)

        # Multi-column format for queue and games played
        embed.add_field(name="Queue", value="NA\n2v2\nTotal", inline=True)
        embed.add_field(name="Games Played", value=f"{games_for_player_NA}\n{games_for_player_2v2}\n{games_for_player_ALL}", inline=True)
        
        await ctx.send(embed=embed)
        
        # Create and send the NA chart if games were played in that queue
        if games_for_player_NA > 0:
            chart_filename_NA = create_rolling_percentage_chart(player_id, total_data_NA, 'PUG')
            file_NA = discord.File(chart_filename_NA, filename="rolling_percentage_chart_NA.png")
            embed_NA = Embed(title="Percentage played over the last 10 games (NA)", color=Colour.blue())
            embed_NA.set_image(url="attachment://rolling_percentage_chart_NA.png")
            await ctx.send(embed=embed_NA, file=file_NA)

        # Create and send the 2v2 chart if games were played in that queue
        if games_for_player_2v2 > 0:
            chart_filename_2v2 = create_rolling_percentage_chart(player_id, total_data_2v2, '2v2')
            file_2v2 = discord.File(chart_filename_2v2, filename="rolling_percentage_chart_2v2.png")
            embed_2v2 = Embed(title="Percentage played over the last 10 games (2v2)", color=Colour.blue())
            embed_2v2.set_image(url="attachment://rolling_percentage_chart_2v2.png")
            await ctx.send(embed=embed_2v2, file=file_2v2)
    except Exception as e:
        await ctx.send(f"Error fetching stats for {player_input}: {e}")

@bot.command()
async def gamelengths(ctx, queue: str = 'ALL'):
    try:
        # Fetch and process the data
        timestamps, game_lengths = get_game_lengths(queue)

        # Plot the data and get the filename
        filename = plot_game_lengths(timestamps, game_lengths, queue)

        # Send the image to Discord
        file = discord.File(filename, filename=filename)
        embed = discord.Embed(title="Game Length Over Time", description=f"", color=discord.Colour.blue())
        embed.set_image(url=f"attachment://{filename}")
        await ctx.send(embed=embed, file=file)

    except Exception as e:
        await ctx.send(f"Error fetching and plotting game lengths: {e}")

def get_game_lengths(queue):
    """
    Fetch game data, calculate game lengths and return them.
    :param queue: The game queue.
    :return: List of timestamps and game lengths.
    """
    start_date = datetime(2018, 1, 1)
    end_date = datetime.now()
    game_data = fetch_data(start_date, end_date, queue)

    # Calculate the game lengths and their respective timestamps
    timestamps = [game['timestamp'] for game in game_data]
    game_lengths = [(game['completionTimestamp'] - game['timestamp']) / (60 * 1000) for game in game_data]  # in minutes

    return timestamps, game_lengths

@bot.command()
async def gamehistory(ctx, number_of_maps: int = 5):
    # Checking if number of maps requested is within limits
    if number_of_maps > 10:
        await ctx.send("Error: Cannot fetch more than 10 maps.")
        return

    # Parsing game history from channel
    maps = await parse_game_history_from_channel(ctx.channel, number_of_maps)
    
    if not maps:
        await ctx.send("No maps found in recent history.")
        return

    # Constructing the embed
    embed = Embed(title=f"Last {len(maps)} Maps", description="", color=0x00ff00)

    for map_data in maps:
        map_name = map_data["name"]
        map_date = map_data["date"]
        time_difference = time_ago(map_date)  # Directly pass the datetime object
        embed.add_field(name=map_name, value=f"Played `{time_difference}`", inline=False)

    await ctx.send(embed=embed)


async def parse_game_history_from_channel(channel, limit):
    maps = []
    last_message_id = None
    continue_search = True

    while len(maps) < limit and continue_search:
        if last_message_id:
            history = channel.history(limit=100, before=discord.Object(id=last_message_id))
        else:
            history = channel.history(limit=100)

        fetched_messages = 0
        async for message in history:
            fetched_messages += 1

            if len(maps) == limit:  # Stop once we have enough
                break
            if message.embeds:
                embed = message.embeds[0]
                description = embed.description
                if description and "**Maps:**" in description:
                    map_name = description.split("**Maps:**")[1].strip()
                    naive_datetime = message.created_at.replace(tzinfo=None)
                    maps.append({"name": map_name, "date": naive_datetime})  # Store entire datetime

            last_message_id = message.id

        # If the loop didn't break due to the limit and we didn't fetch a full 100 messages, 
        # it means there's no more history
        if fetched_messages < 100:
            continue_search = False

    return maps



@bot.command()
async def setname(ctx, member: Member, *, new_name: str):

    if not await check_bot_admin(ctx):
        return


    try:
        # Update the in-memory mapping
        player_name_mapping[member.id] = new_name
        
        # Write the changes to the file
        with open('data/player_mappings.py', 'w', encoding='utf-8') as file:
            file.write("player_name_mapping = {\n")
            for id, name in player_name_mapping.items():
                file.write(f"    {id}: \"{name}\",\n")
            file.write("}\n")

        # Send a confirmation using an embed
        embed = discord.Embed(title="Name Updated", color=0x00ff00) # Green color for success
        embed.description = f"Name for `{member.display_name}` has been set to `{new_name}`."
        await ctx.send(embed=embed)
        
    except Exception as e:
        # Send an error using an embed
        embed = discord.Embed(title="Error Setting Name", description=f"`{e}`", color=0xff0000) # Red color for error
        await ctx.send(embed=embed)



bot.run(TOKEN)