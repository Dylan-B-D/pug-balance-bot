# Standard Libraries
import re
import os
import configparser

# Third-party Libraries
import discord
import trueskill
import random

# Bot Data
from data.player_mappings import player_name_mapping
from data.map_url_mapping import map_url_mapping
from data.user_roles import bot_admins
from data.map_commands import map_commands, map_commands_arena
from data.shared_data import (last_messages, most_recent_matched_ids, matched_results_store, substitution_store, game_history_cache)

# Bot Modules
from modules.data_managment import fetch_data
from modules.rating_calculations import (calculate_ratings, compute_avg_picks, initialize_player_data, process_matches)
from modules.team_logic import balance_teams
from modules.embeds_formatting import create_embed
from modules.data_managment import load_from_bson

# ==============================
# CONFIGURATION
# ==============================

# File Paths
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))  
CACHE_DIR = os.path.join(BASE_DIR, 'cache', 'gamequeue')
DATA_DIR = os.path.join(BASE_DIR, 'data', 'gamequeue')

# Command mapping
reverse_map_commands = {v: k for k, v in map_commands.items()}

# Load map_weights from BSON file
map_weights = load_from_bson(os.path.join(DATA_DIR, 'map_weights.bson'))

# Random map selection based on weights
maps, weights = zip(*map_weights.items())
selected_map = random.choices(maps, weights, k=1)[0]


# Configuration File Parsing
config = configparser.ConfigParser()
try:
    config.read('config.ini')
    MIN_REACTIONS = int(config['Constants']['MIN_REACTIONS'])
    MIN_REACTIONS_MAP = int(config['Constants']['MIN_REACTIONS_MAP'])
except (configparser.Error, KeyError) as e:
    raise SystemExit("Error reading configuration file.") from e

# ==============================
# FUNCTIONS
# ==============================

def get_user_id_by_name(guild, username):
    """
    Retrieves the user ID based on the provided username or display name.

    :param guild: Discord guild object.
    :param username: The name or display name of the user.
    :return: User ID if found, else None.
    """
    member = discord.utils.get(guild.members, name=username) or discord.utils.get(guild.members, display_name=username)
    if member:
        return member.id
    return None


async def check_bot_admin(ctx):
    """
    Checks if the context's author is a bot admin.
    Returns True if the author is a bot admin, else sends an error embed and returns False.
    """
    if ctx.author.id not in bot_admins:
        embed = discord.Embed(title="Permission Denied", description="Admin permissions required to execute this command.", color=0xff0000)
        await ctx.send(embed=embed)
        return False
    return True


async def check_substitution(bot, message, start_date, end_date):
    """
    Checks if a substitution message is detected
    """
    if message.content.startswith("`") and " has been substituted with " in message.content and message.content.endswith("`"):
        return await handle_substitution(bot, message, start_date, end_date)


async def check_2v2_game_start(message, embed):
    """
    Detects if a 2v2 game has started based on the embed description.
    """
    if embed.description and "Captains:" in embed.description:
        last_msg = last_messages.get(message.channel.id)
        if last_msg == "`Game '2v2' has begun`":
            print("2v2 game detected, skipping ID matching.")
            return True
    return False


async def check_pug_game_start(bot, message, embed, start_date, end_date):
    """
    Detects and handles PUG game start based on the embed description.
    """
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
        await send_balanced_teams(bot, message.channel, players, player_ratings, captains, data)


async def check_map_start(message, embed):
    """
    Checks if a map has started and initiates the map start procedure.
    """
    from cogs.server_management import start_map
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



async def match_ids(channel, embed):
    """
    Matches the IDs of users based on global/server display names, 
    or underlying username, from a embed containing comma seperated usernames and 2 captains.
    """
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

    # Create a dictionary mapping display names and usernames to their ids for all members of the guild
    members_names = {member.display_name: member.id for member in channel.guild.members}
    members_usernames = {member.name: member.id for member in channel.guild.members}
    
    # Merge the two dictionaries
    members_names.update(members_usernames)

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


async def initialize_data(data):
    game_data = data  # Replace with the actual game data
    player_data = initialize_player_data(game_data)
    process_matches(game_data, player_data)
    avg_picks = compute_avg_picks(player_data['picks'])
    return player_data, avg_picks

def get_balanced_teams_list(players, captains, player_data, avg_picks, player_ratings):
    balanced_teams_list = balance_teams(players, captains, player_data['games'], avg_picks, player_ratings)
    if not balanced_teams_list or len(balanced_teams_list) <= 1:
        print("Could not find balanced teams.")
        return None
    return balanced_teams_list

async def get_last_two_maps(channel):
    number_of_maps = 2
    maps = await parse_game_history_from_channel(channel, number_of_maps)
    if not maps:
        return None, None

    most_recent_map = maps[0]["name"]
    second_recent_map = maps[1]["name"] if len(maps) > 1 else None

    print(most_recent_map)
    print(second_recent_map)

    return most_recent_map, second_recent_map


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

async def select_maps(channel):
    most_recent, second_recent = await get_last_two_maps(channel)
    
    available_maps = []
    weights_adjusted = []

    for map_name, weight in map_weights.items():
        if map_name.lower().replace(" ", "") != most_recent:  # Exclude the most recent map
            available_maps.append(map_name)
            if map_name.lower().replace(" ", "") == second_recent:  # Adjust weight for the second most recent map
                weights_adjusted.append(weight * 0.5)
            else:
                weights_adjusted.append(weight)

    # Randomly select two maps
    selected_maps = random.choices(available_maps, weights_adjusted, k=2)
    while (selected_maps[0].lower().replace(" ", "") in [most_recent, second_recent]
           or selected_maps[1].lower().replace(" ", "") in [most_recent, second_recent]
           or selected_maps[0] == selected_maps[1]):
        selected_maps = random.choices(available_maps, weights_adjusted, k=2)

    current_map, next_map = selected_maps
    map_url = map_url_mapping.get(current_map, map_url_mapping["N/A"])
    return current_map, next_map, map_url



async def send_initial_embed(channel, balanced_teams, captains, player_ratings, current_map, map_url, next_map):
    msg = await channel.send(embed=create_embed(balanced_teams, captains, player_ratings, map_name=current_map, map_url=map_url, description=f"Vote 🗺️ to skip to {next_map}"))
    await msg.add_reaction('🔁')
    await msg.add_reaction('🗺️')
    return msg


async def send_balanced_teams(bot, channel, players, player_ratings, captains, data):
    try:
        player_data, avg_picks = await initialize_data(data)
        balanced_teams_list = get_balanced_teams_list(players, captains, player_data, avg_picks, player_ratings)
        if not balanced_teams_list:
            return

        current_map, next_map, map_url = await select_maps(channel)
        msg = await send_initial_embed(channel, balanced_teams_list[0], captains, player_ratings, current_map, map_url, next_map)
        
        await handle_reactions(bot, msg, players, captains, player_ratings, current_map, next_map, balanced_teams_list)

    except Exception as e:
        print(f"Error in sending balanced teams info: {e}")

async def handle_reactions(bot, msg, players, captains, player_ratings, current_map, next_map, balanced_teams_list):
    player_ids = set(player['id'] for player in players)
    map_voters = set()
    next_index = 1
    map_changed = False

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
            msg = await msg.channel.send(embed=create_embed(balanced_teams, captains, player_ratings, title=f'Rebalanced Teams #{next_index}', map_name=current_map, map_url=map_url, description=f"Vote 🗺️ to skip to {next_map}"))
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
                    title = 'Suggested Teams'
                else:
                    title = f'Rebalanced Teams #{next_index - 1}'

                await msg.edit(embed=create_embed(balanced_teams, captains, player_ratings, title=title, map_name=current_map, map_url=map_url))
                # Clear existing reactions since the map can't be changed again
                await msg.clear_reactions()
                await msg.add_reaction('🔁')

async def handle_substitution(bot, message, start_date, end_date):
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

        await send_balanced_teams(bot, message.channel, players, player_ratings, captains, data)
