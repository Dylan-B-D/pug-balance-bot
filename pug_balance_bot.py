import discord
import re
import trueskill
import random
import configparser
import os
from discord.ext import commands
from discord import Embed
from datetime import datetime
from data.player_mappings import player_name_mapping
from data.map_url_mapping import map_url_mapping
from data.map_weights import map_weights
from data.map_commands import map_commands, map_commands_arena
from modules.data_managment import fetch_data
from modules.rating_calculations import (calculate_ratings, compute_avg_picks, initialize_player_data, process_matches)
from modules.team_logic import balance_teams
from modules.embeds_formatting import create_embed
from modules.utilities import get_user_id_by_name
from cogs.utilities import UtilitiesCog
from cogs.debug import DebugsCog
from cogs.admin import AdminCog
from cogs.server_management import ServerManagementCog, start_map
from cogs.stats import StatsCog
from cogs.ta_network import TANetworkCog
from modules.shared_data import *


# ==============================
# CONFIGURATION
# ==============================

# Config file parsing
config = configparser.ConfigParser()
config.read('config.ini')
TOKEN = config['Bot']['Token']

# Bot command-related constants
MIN_REACTIONS = int(config['Constants']['MIN_REACTIONS'])
MIN_REACTIONS_MAP = int(config['Constants']['MIN_REACTIONS_MAP'])

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



# Command mapping
reverse_map_commands = {v: k for k, v in map_commands.items()}

# Random map selection based on weights
maps, weights = zip(*map_weights.items())
selected_map = random.choices(maps, weights, k=1)[0]


# Node path variable
NODE_PATH = os.environ.get('NODE_PATH', 'node')

# Cache for fetched servers list
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Create a 'data' directory if it doesn't exist
DATA_DIR = os.path.join(BASE_DIR, 'data')
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# Set the cache file paths inside the 'data' directory
CACHE_FILE_PATH = os.path.join(DATA_DIR, "cache.json")
TEMP_CACHE_FILE_PATH = os.path.join(DATA_DIR, "temp_cache.json")



# ==============================
# BOT COMMANDS
# ==============================

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

    await bot.add_cog(UtilitiesCog(bot))
    await bot.add_cog(DebugsCog(bot))
    await bot.add_cog(AdminCog(bot))
    await bot.add_cog(ServerManagementCog(bot))
    await bot.add_cog(StatsCog(bot))
    await bot.add_cog(TANetworkCog(bot))



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
            await send_balanced_teams(message.channel, players, player_ratings, captains, data)
    
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


# ==============================
# FUNCTIONS
# ==============================



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




def pick_two_maps():
    selected_maps = random.choices(maps, weights, k=2)
    while selected_maps[0] == selected_maps[1]:
        selected_maps = random.choices(maps, weights, k=2)
    return selected_maps

async def send_balanced_teams(channel, players, player_ratings, captains, data):
    try:
        # Calculate player_games and avg_picks
        game_data = data  # Replace with the actual game data
        player_data = initialize_player_data(game_data)
        process_matches(game_data, player_data)
        avg_picks = compute_avg_picks(player_data['picks'])
        
        balanced_teams_list = balance_teams(players, captains, player_data['games'], avg_picks, player_ratings)
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
                        title = 'Suggested Teams'
                    else:
                        title = f'Rebalanced Teams #{next_index - 1}'

                    await msg.edit(embed=create_embed(balanced_teams, captains, player_ratings, title=title, map_name=current_map, map_url=map_url))
                    # Clear existing reactions since the map can't be changed again
                    await msg.clear_reactions()
                    await msg.add_reaction('🔁')

    except Exception as e:
        print(f"Error in sending balanced teams info: {e}")


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

        await send_balanced_teams(message.channel, players, player_ratings, captains, data)




bot.run(TOKEN)