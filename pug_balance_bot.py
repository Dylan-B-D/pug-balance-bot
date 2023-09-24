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
from modules.charts import (create_map_weights_chart)
from modules.utilities import (get_user_id_by_name)


# ==============================
# CONFIGURATION
# ==============================

# Config file parsing
config = configparser.ConfigParser()
config.read('config.ini')
TOKEN = config['Bot']['Token']

# Bot command-related constants
MIN_REACTIONS = 5
MIN_REACTIONS_MAP = 7

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

        # Replace the old_player_id with new_player_id in the list of matched_ids
        for index, user_id_str in enumerate(matched_ids):
            user_id = int(user_id_str) if isinstance(user_id_str, int) else int(re.search(r'\((\d+)\)', user_id_str).group(1))
            if user_id == old_player_id:
                matched_ids[index] = f"{new_player_name} ({new_player_id})"
                break

        # If the substituted player was a captain, then the new player should also be a captain.
        for index, user_id_str in enumerate(captains):
            user_id = int(user_id_str) if isinstance(user_id_str, int) else int(re.search(r'\((\d+)\)', user_id_str).group(1))
            if user_id == old_player_id:
                captains[index] = new_player_id  # replaced with the new player’s ID as an integer
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
    description_words = set(word.strip(' &@,') for word in regex_split.split(content_string) if word.strip(' &@,'))
    
    members_names = {member.display_name: member.id for member in channel.guild.members}
    
    # Match by names in the list of words extracted from content_string
    for word in description_words:
        user_id = get_user_id_by_name(channel.guild, word)
        if user_id and f"{word} ({user_id})" not in matched_ids:
            matched_ids.append(f"{word} ({user_id})")
            content_string = content_string.replace(word, '', 1)
            
    # Match by substring
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


async def send_balanced_teams(channel, players, player_ratings, captains):
    try:
        balanced_teams_list = balance_teams(players, captains)
        if not balanced_teams_list or len(balanced_teams_list) <= 1:
            print("Could not find balanced teams.")
            return
        
        next_index = 1
        
        balanced_teams = balanced_teams_list[0]
        
        selected_map = random.choices(maps, weights, k=1)[0]
        map_url = map_url_mapping.get(selected_map) 

        msg = await channel.send(embed=create_embed(balanced_teams_list[0], captains, player_ratings, map_name=selected_map, map_url=map_url))
        await msg.add_reaction('🔁')
        await msg.add_reaction('🗺️')
        
        player_ids = set(player['id'] for player in players)
        
        
        def check(reaction, user):
            return (
                user.id in player_ids
                and user != msg.author
                and str(reaction.emoji) in ['🔁', '🗺️']
                and reaction.message.id == msg.id
            )

        
        while next_index < len(balanced_teams_list):
            reaction, user = await bot.wait_for('reaction_add', check=check)
            emoji = str(reaction.emoji)
            
            if emoji == '🔁' and reaction.count-1 >= MIN_REACTIONS:
                balanced_teams = balanced_teams_list[next_index]
                msg = await channel.send(embed=create_embed(balanced_teams, captains, player_ratings, title=f'Rebalanced Teams #{next_index}', map_name=selected_map, map_url=map_url))
                if next_index < len(balanced_teams_list) - 1:
                    await msg.add_reaction('🔁')
                await msg.add_reaction('🗺️')
                next_index += 1
            
            elif emoji == '🗺️':
                if reaction.count-1 >= MIN_REACTIONS_MAP:
                    available_maps = [map_ for map_ in maps if map_ != selected_map]  
                    available_weights = [weights[maps.index(map_)] for map_ in available_maps]

                    if not available_maps:  
                        available_maps = maps
                        available_weights = weights
                        
                    new_selected_map = random.choices(available_maps, available_weights, k=1)[0]
                    new_map_url = map_url_mapping.get(new_selected_map)

                    if next_index == 1:
                        title = 'Balanced Teams'
                    else:
                        title = f'Rebalanced Teams #{next_index - 1}'

                    await msg.edit(embed=create_embed(balanced_teams, captains, player_ratings, title=title, map_name=new_selected_map, map_url=new_map_url))
                    selected_map = new_selected_map  

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
        description="**Captains: <@140028568062263296> & <@162786167765336064>**\n"
                    "Evil, Jive",
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
    
    if ctx.author.id not in bot_admins:
        embed = Embed(title="Permission Denied", description="Admin permissions required to execute this command.", color=0xff0000)
        await ctx.send(embed=embed)
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
    
    try:
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
            
            read_time = time.time()  # Timer before reading the output
            print(f"SSH Output Read Time: {time.time() - read_time} seconds")
            
            if output:
                # Get the user's name from the mapping
                user_id = ctx.author.id
                user_name = player_name_mapping.get(user_id, str(ctx.author))  # Use the discord username as fallback
                
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

    end_time = time.time()  
    print(f"Total Execution Time: {end_time - start_time} seconds")

@bot.command()
async def help(ctx):
    embed = Embed(title="TA-Bot Commands", description="", color=0x00ff00)
    
    # For each command, you can add a custom description.
    commands_dict = {
        "!debug": {"desc": "Admin-only command for debugging ID matching results for previous game."},
        "!info": {"desc": "Shows map weighting for generated games, and voting thresholds."},
        "!kill": {"desc": "Elevated-admin command to shutdown docker containers.", "usage": "!kill [container]"},
        "!listadmins": {"desc": "Lists all the admins."},
        "!match": {"desc": "Restricted command for simulating a game starting."},
        "!servers": {"desc": "Shows server list for \'PUG Login\'."},
        "!serverstats": {"desc": "Gets some simple stats for the PUG queues."},
        "!setmap": {"desc": "Admin only command for setting the PUG/2v2 map. Returns list of map IDs if no parameter given.", "usage": "!setmap [map_id]"},
        "!status": {"desc": "Shows server status for PUGs with no param, or shows status for Mixers with a param of \'2\'.", "usage": "!status [optional_server_index]"}
    }

    for cmd, values in commands_dict.items():
        if "usage" in values:
            embed.add_field(name=cmd, value=f"{values['desc']} Usage: `{values['usage']}`", inline=False)
        else:
            embed.add_field(name=cmd, value=values['desc'], inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='debug')
async def debug(ctx):
    # Check if the author of the command is in the list of admins
    if ctx.author.id not in bot_admins:
        embed = Embed(title="Permission Denied", description="Admin permissions required to execute this command.", color=0xff0000)
        await ctx.send(embed=embed)
        return

    channel_id = ctx.channel.id
    
    # Send the substitution embed if available
    embed_description = substitution_store.get(channel_id)
    if embed_description:
        embed = discord.Embed(description=embed_description, color=discord.Color.blue())
        await ctx.channel.send(embed=embed)
    
    # Send the matched_results if available
    matched_results = matched_results_store.get(channel_id)
    if matched_results:
        await send_match_results(ctx.channel, matched_results)
    else:
        await ctx.channel.send("No matched results found for this channel.")

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




@tasks.loop(seconds=15)
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
        await ctx.send(f"Error executing command: {e.stderr}")
    except json.JSONDecodeError as e:
        await ctx.send(f"Error decoding JSON: {e}")
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")

countdown_task = None

async def countdown(message, time_remaining, server_with_id_3):
    try:
        while time_remaining > 0:
            if server_with_id_3['scores']['diamondSword'] >= 7:
                await message.edit(content="Diamond Sword wins!")
                return
            if server_with_id_3['scores']['bloodEagle'] >= 7:
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
        await ctx.send(f"Error executing command: {e.stderr}")
    except json.JSONDecodeError as e:
        await ctx.send(f"Error decoding JSON: {e}")
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")





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


async def send_player_info(channel, matched_ids, data, player_ratings, player_name_mapping):
    try:
        id_game_count = count_games_for_ids(data, [re.search(r'\((\d+)\)', item).group(1) for item in matched_ids])
        
        match_info = Embed(title='Player Info', colour=0xf59042)
        
        for user_id_str in matched_ids:
            user_id = int(re.search(r'\((\d+)\)', user_id_str).group(1))
            name = player_name_mapping.get(user_id)
            
            if not name:
                member = channel.guild.get_member(user_id)
                name = member.display_name if member else str(user_id)
            
            game_count = id_game_count.get(str(user_id), 0)
            rating = player_ratings.get(user_id)
            rating_str = f"Rating: {rating.mu:.2f}μ {rating.sigma:.2f}σ" if rating else "Rating: N/A"
            
            match_info.add_field(name=f'{name} ({user_id})', value=f'{game_count} games\n{rating_str}', inline=False)
        
        await channel.send(embed=match_info)

    except Exception as e:
        print(f"Error in sending player info: {e}")

bot.run(TOKEN)