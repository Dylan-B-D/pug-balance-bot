import discord
from discord.ext import commands, tasks
from discord import Embed, Colour
import re
import json
import requests
from datetime import datetime, timedelta
from data_mappings.player_mappings import player_name_mapping
from data_mappings.map_name_mapping import map_name_mapping
from data_mappings.map_url_mapping import map_url_mapping
import trueskill
from math import erf, sqrt
from scipy.special import logit
import itertools
import math
from itertools import combinations
from queue import PriorityQueue
import json
import random
import matplotlib.pyplot as plt
from collections import Counter
import subprocess
import configparser
import time
import asyncio
import asyncssh
import os


config = configparser.ConfigParser()
config.read('config.ini')

bot_token = config['Bot']['Token']

TOKEN = bot_token
MIN_REACTIONS = 5
MIN_REACTIONS_MAP = 7

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

most_recent_matched_ids = {}
matched_results_store = {}
substitution_store = {}
map_weights = {
    "Dangerous Crossing": 24.95,
    "Katabatic": 24.19,
    "Arx Novena": 23.00,
    "Oceanus": 8.00,
    "Tartarus": 6.56,
    "Sunstar": 4.30,
    "Drydock": 3.00,
    "Blues": 3.00,
    "Perma": 3.00,
}

bot_admins = [
    140028568062263296,
    162786167765336064,
    266832474061930497,
    331995692727795715,
    252190261734670336,
    506694143691587604,
    256227747129458690,
    170521741578338307,
    316614348883755009,
    416200961896349696,
    257280863010684930,
]

map_commands = {
    'kb': 'Katabatic',
    'an': 'ArxNovena',
    'dx': 'DangerousCrossing',
    'cf': 'Crossfire',
    'dd': 'Drydock',
    'tm': 'Terminus',
    'st': 'Sunstar',
    'bo': 'BellaOmegaNS',
    'bs': 'Blueshift',
    'cc': 'CanyonCrusade',
    'hf': 'Hellfire',
    'ic': 'IceCoaster',
    'pd': 'Perdition',
    'pm': 'Permafrost',
    'rd': 'Raindance',
    'sh': 'Stonehenge',
    'tt': 'Tartarus',
    'tr': 'TempleRuins',
    'blu': 'Blues',
    'inc': 'Incidamus',
    'per': 'Periculo',
    'fra': 'Fracture',
    'phl': 'Phlegethon',
    'des': 'DesertedValley',
    'ach': 'Acheron',
    'sty': 'Styx',
    'ecl': 'Eclipse',
    'pol': 'Polaris',
    'mer': 'Meridian',
    'asc': 'Ascent',
    'cra': 'Crash',
    'tre': 'TreacherousPass'
}
map_commands_arena = {
    'wi': 'WalledIn',
    'la': 'LavaArena',
    'aa': 'AirArena',
    'hl': 'Hinterland',
    'ft': 'FrayTown',
    'uc': 'Undercroft',
    'wo': 'Whiteout',
    'eb': 'ElysianBattleground',
}


reverse_map_commands = {v: k for k, v in map_commands.items()}

maps, weights = zip(*map_weights.items())
selected_map = random.choices(maps, weights, k=1)[0]

cache = None


def fetch_data(start_date, end_date, queue):
    if queue == '2v2':
        urls = ['https://sh4z.se/pugstats/naTA.json']
        json_replaces = ['datanaTA = ']
        queue_filters = ['2v2']
    elif queue == 'NA':
        urls = ['https://sh4z.se/pugstats/naTA.json']
        json_replaces = ['datanaTA = ']
        queue_filters = ['PUGz']
    else:
        raise ValueError(f"Invalid queue: {queue}")

    combined_data = []
    for url, json_replace, queue_filter in zip(urls, json_replaces, queue_filters):
        response = requests.get(url)
        json_content = response.text.replace(json_replace, '')
        match = re.search(r'\[.*\]', json_content)
        
        if match:
            json_content = match.group(0)
            game_data = json.loads(json_content)
            
            filtered_data = [game for game in game_data
                             if game['queue']['name'] == queue_filter
                             and start_date <= datetime.fromtimestamp(game['timestamp'] / 1000) <= end_date]
            combined_data.extend(filtered_data)
        else:
            raise ValueError("No valid JSON data found in response.")
    
    print(f"Fetched {len(combined_data)} games for {queue} queue.")
    return combined_data

def win_probability(team1, team2):
    delta_mu = sum(r.mu for r in team1) - sum(r.mu for r in team2)
    sum_sigma = sum(r.sigma ** 2 for r in itertools.chain(team1, team2))
    size = len(team1) + len(team2)
    denom = math.sqrt(size * (trueskill.BETA * trueskill.BETA) + sum_sigma)
    ts = trueskill.global_env()
    return ts.cdf(delta_mu / denom)

def calculate_win_probability_for_match(match, player_ratings):
    ts = trueskill.TrueSkill()
    
    team1_ratings = [player_ratings.get(player['user']['id'], ts.create_rating()) for player in match['players'] if player['team'] == 1]
    team2_ratings = [player_ratings.get(player['user']['id'], ts.create_rating()) for player in match['players'] if player['team'] == 2]

    win_prob_team1 = win_probability(team1_ratings, team2_ratings)
    
    return win_prob_team1

def pick_order_sigma_adjustment(pick_order):
    sigma_min = 0.9 
    sigma_max = 1.1 
    
    # Linear adjustment based on pick order
    return sigma_min + (sigma_max - sigma_min) * (pick_order - 1) / 11



def compute_logit_bonus(pick_order, total_picks=12, delta=0.015):
    """
    Computes the bonus to be added to mu based on the pick order using the logit function.
    """
    # Normalize the pick order so that 1 becomes almost 1 and 12 becomes almost 0
    normalized_order = (total_picks - pick_order) / (total_picks -1)
    logit_value = logit(normalized_order * (1-(2*delta)) + delta)
    bonus = 20 * logit_value / abs(logit(delta))
    
    return bonus

def calculate_draw_rate(game_data):
    total_matches = len(game_data)
    draw_matches = sum(1 for game in game_data if game['winningTeam'] == 0)
    return draw_matches / total_matches if total_matches > 0 else 0


def calculate_ratings(game_data, queue='NA'):
    global player_names, player_picks 

    draw_rate = calculate_draw_rate(game_data)
    custom_tau = 0.1
    ts = trueskill.TrueSkill(draw_probability=draw_rate, tau=custom_tau) if queue != 'NA' else trueskill.TrueSkill(tau=custom_tau)

    # Initialize tracking variables
    all_player_ids = set(player['user']['id'] for match in game_data for player in match['players'])
    player_picks = {player_id: [] for player_id in all_player_ids}
    player_wins = {player_id: 0 for player_id in all_player_ids}
    player_names = {player['user']['id']: player['user']['name'] for match in game_data for player in match['players']}
    player_games = {player_id: 0 for player_id in all_player_ids}
    player_rating_history = {player_id: [] for player_id in all_player_ids}

    # Process each match
    for match in game_data:
        for player in match['players']:
            player_id = player['user']['id']
            player_games[player_id] += 1
            if player['pickOrder'] is not None and player['pickOrder'] > 0:
                player_picks[player_id].append(player['pickOrder'])
            if match['winningTeam'] == player['team']:
                player_wins[player_id] += 1

    # Compute average pick rates and win rates
    def recent_games_count(total_games):
        if total_games < 100:
            return total_games
        elif total_games < 150:
            return int(0.4 * total_games)
        else:
            return int(0.2 * total_games)

    player_avg_picks = {}
    for player_id, picks in player_picks.items():
        recent_games = recent_games_count(len(picks))
        if recent_games == 0:
            player_avg_picks[player_id] = 0
        else:
            player_avg_picks[player_id] = sum(picks[-recent_games:]) / recent_games

    # Initialize the mu based on average pick and win rate
    def mu_bonus(pick_order):
        return compute_logit_bonus(pick_order)

    # Adjust the win rate for players with fewer games
    def adjusted_win_rate(win_rate, games_played):
        MIN_GAMES_FOR_FULL_IMPACT = 15
        if games_played < MIN_GAMES_FOR_FULL_IMPACT:
            adjusted_win_rate = win_rate * (games_played / MIN_GAMES_FOR_FULL_IMPACT) + 0.5 * (1 - games_played / MIN_GAMES_FOR_FULL_IMPACT)
            return adjusted_win_rate
        return win_rate

    # Adjust sigma for newer players and pick order
    def sigma_adjustment(games_played, pick_order):
        games_played_factor = 1.0
        if games_played < 10:
            games_played_factor = 1.5
        elif games_played < 30:
            games_played_factor = 1.25

        pick_order_factor = pick_order_sigma_adjustment(pick_order)
        return games_played_factor * pick_order_factor

    # Initialize ratings
    player_ratings = {}
    
    # Initialize default ratings for all players
    for player_id in all_player_ids:
        player_ratings[player_id] = default_rating = trueskill.Rating(mu=15, sigma=5)
    
    # Now adjust ratings for players who have played matches
    for player_id, avg_pick in player_avg_picks.items():
        if queue != '2v2':
            mu = ts.mu + mu_bonus(avg_pick)
        else:
            mu = ts.mu
        sigma = ts.sigma * sigma_adjustment(player_games[player_id], avg_pick)
        
        # Ensure mu ± 3*sigma lies within [0, 50]
        sigma = min(sigma, (50 - mu) / 3, mu / 3)
        
        player_ratings[player_id] = trueskill.Rating(mu=mu, sigma=sigma)

    # Process each match to adjust ratings
    for match in game_data:
        # Create a list of teams and corresponding player ID lists for the TrueSkill rate function
        teams = []
        team_player_ids = []
        for team_number in [1, 2]:
            team = [player_ratings[player['user']['id']] for player in match['players'] if player['team'] == team_number]
            player_ids = [player['user']['id'] for player in match['players'] if player['team'] == team_number]
            teams.append(team)
            team_player_ids.append(player_ids)

        # Adjust the team order if team 2 won the match
        if match['winningTeam'] == 2:
            teams.reverse()
            team_player_ids.reverse()

        # Update ratings based on match outcome
        try:
            # Update ratings based on match outcome
            new_ratings = ts.rate(teams)
        except FloatingPointError:
            # Handle numerical instability error
            continue
        
        # Store the updated skill ratings
        for i, team in enumerate(teams):
            for j, player_rating in enumerate(team):
                player_ratings[team_player_ids[i][j]] = new_ratings[i][j]

        for i, team in enumerate(teams):
            for j, player_rating in enumerate(team):
                player_id = team_player_ids[i][j]
                player_ratings[player_id] = new_ratings[i][j]
                
                # Convert Rating object to a dictionary
                rating_dict = {"mu": new_ratings[i][j].mu, "sigma": new_ratings[i][j].sigma}
                
                # Append the dictionary to player's rating history
                player_rating_history[player_id].append(rating_dict)

    return player_ratings, player_names, player_games, player_rating_history


def generate_combinations(players, team_size):
    if team_size <= 0:
        return [[]]
    return list(combinations(players, team_size))

def balance_teams(players, captains):
    best_teams = PriorityQueue()
    
    locked_team1_players = [player for player in players if player['id'] == captains[0]]
    locked_team2_players = [player for player in players if player['id'] == captains[1]]
    unlocked_players = [player for player in players if player not in locked_team1_players + locked_team2_players]
    
    max_team_size = min(7, len(players) // 2)
    
    for team_size in range(len(locked_team1_players), max_team_size + 1):
        team1_combinations = generate_combinations(unlocked_players, team_size - len(locked_team1_players))
        
        for team1_unlocked in team1_combinations:
            full_team1 = locked_team1_players + list(team1_unlocked)
            team2 = locked_team2_players + [player for player in unlocked_players if player not in team1_unlocked]
            
            if abs(len(full_team1) - len(team2)) > 1:
                continue
            
            difference = abs(sum(player['mu'] for player in full_team1) - sum(player['mu'] for player in team2))
            
            # Negative difference because PriorityQueue is a min-heap and we want max difference at the top
            if best_teams.qsize() < 5 or -difference > best_teams.queue[0][0]:
                best_teams.put((-difference, {'team1': full_team1, 'team2': team2}))
                
                # If we have more than 5 items in the queue, remove the smallest one
                if best_teams.qsize() > 5:
                    best_teams.get()
                    
    return [best_teams.get()[1] for _ in range(min(5, best_teams.qsize()))][::-1]

def count_games_for_ids(data, matched_ids):
    id_game_count = {id_: 0 for id_ in matched_ids}
    for game in data:
        for player in game['players']:
            user_id = str(player['user']['id'])
            if user_id in id_game_count:
                id_game_count[user_id] += 1
    return id_game_count


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

    end_date = datetime.now()
    start_date = datetime(2018, 1, 1)

    if message.content.startswith("`") and " has been substituted with " in message.content and message.content.endswith("`"):
        await handle_substitution(message, start_date, end_date)


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
        
        def format_names(team):
            names = []
            # Sort team members by their ratings (mu value), highest first, but keep captain first.
            team = sorted(
                team,
                key=lambda player: (
                    player['id'] not in captains,  
                    -(player_ratings.get(player['id']).mu if player_ratings.get(player['id']) else 0) 
                )
            )
            for player in team:
                name = player['name']
                if player['id'] in captains:
                    name = f"**(c)** {name}"  
                names.append(name)
            return names

        
        def create_embed(balanced_teams, title='Balanced Teams', map_name='Unknown Map', map_url='https://i.ibb.co/QM564R3/arx.jpg', rebalance_reactions=0, skip_map_reactions=0):
            team1_names = format_names(balanced_teams['team1'])
            team2_names = format_names(balanced_teams['team2'])

            default_rating = trueskill.Rating(mu=15, sigma=5)
            team1_ratings = [player_ratings.get(player['id'], default_rating) for player in balanced_teams['team1']]
            team2_ratings = [player_ratings.get(player['id'], default_rating) for player in balanced_teams['team2']]
            
            win_prob_team1 = win_probability(team1_ratings, team2_ratings)
            
            balanced_teams_embed = Embed(title=title, colour=Colour.green())
            balanced_teams_embed.add_field(name='Team 1', value='\n'.join(team1_names) or '[Empty]', inline=True)
            balanced_teams_embed.add_field(name='\u200B', value='\u200B', inline=True)
            balanced_teams_embed.add_field(name='Team 2', value='\n'.join(team2_names) or '[Empty]', inline=True)
            balanced_teams_embed.add_field(name='Win Chance for Team 1', value=f'{win_prob_team1*100:.2f}%', inline=False)

            if map_url:
                balanced_teams_embed.set_thumbnail(url=map_url) 
            balanced_teams_embed.add_field(name='Map', value=map_name, inline=False) 

            return balanced_teams_embed
        
        selected_map = random.choices(maps, weights, k=1)[0]
        map_url = map_url_mapping.get(selected_map) 

        msg = await channel.send(embed=create_embed(balanced_teams_list[0], map_name=selected_map, map_url=map_url))
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
                msg = await channel.send(embed=create_embed(balanced_teams, title=f'Rebalanced Teams #{next_index}', map_name=selected_map, map_url=map_url))
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

                    await msg.edit(embed=create_embed(balanced_teams, title=title, map_name=new_selected_map, map_url=new_map_url))
                    selected_map = new_selected_map  

    except Exception as e:
        print(f"Error in sending balanced teams info: {e}")


def create_map_weights_chart(map_weights):
    map_names = list(map_weights.keys())
    weights = list(map_weights.values())
    
    plt.figure(figsize=(10,6))
    
    plt.gca().set_facecolor('none')
    
    plt.barh(map_names, weights, color='skyblue')
    
    # Setting the color of the labels and title to white
    plt.xlabel('Weights', color='white')
    plt.title('Map Weights', color='white')
    
    # Changing the color of the ticks to white
    plt.tick_params(axis='x', colors='white')
    plt.tick_params(axis='y', colors='white')
    
    plt.gca().invert_yaxis()
    
    plt.tight_layout()
    subfolder = "charts"
    os.makedirs(subfolder, exist_ok=True)  
    
    filename = f"{subfolder}/map_weights_chart.png"
    plt.savefig(filename, facecolor='none')
    plt.close()
    return filename

def get_user_id_by_name(guild, username):
    member = discord.utils.get(guild.members, name=username) or discord.utils.get(guild.members, display_name=username)
    if member:
        return member.id
    return None


@bot.command()
async def match(ctx):
    # Specified user ID
    allowed_user_id = 252190261734670336
    
    # Check the user ID of the command invoker
    if ctx.author.id != allowed_user_id:
        # If user ID does not match, send a message or simply return
        await ctx.send("Oops... looks like you have run into a skill issue.")
        return
    
    # If user ID matches, execute the command
    embed = discord.Embed(
        title="",
        description="**Captains: <@140028568062263296> & <@162786167765336064>**\n"
                    "crodog5, karucieldemonio, mikesters17, Nerve, cjplayz_, rockstaruniverse, "
                    "Evil, gratismatt, grethsc, frogkabobs, jacktheblack, vorpalkitty",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

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
async def setmap(ctx, map_shortcut: str = None):
    start_time = time.time()  # Start timer when the command is triggered
    print(f"Start Time: {start_time}")
    
    # Check if the user is a bot admin
    if ctx.author.id not in bot_admins:
        await ctx.send("You do not have permission to execute this command.")
        return
    
    # Convert map_shortcut to lowercase if it is not None
    map_shortcut = map_shortcut.lower() if map_shortcut is not None else None
    
    # If map_shortcut is None or not in map_commands and map_commands_arena, send the list of valid commands
    if map_shortcut is None or (map_shortcut not in map_commands and map_shortcut not in map_commands_arena):
        embed_creation_time = time.time()  # Timer before creating the embed
        embed = Embed(title="Map Commands", description="Use '!setmap `id`' to start maps.", color=0x00ff00)
        
        def add_maps_to_embed(embed, maps_dict, title, num_cols):
            sorted_items = sorted(maps_dict.items(), key=lambda item: item[1])
            num_rows = -(-len(sorted_items) // num_cols)
            
            for col in range(num_cols):
                fields = []
                for row in range(num_rows):
                    idx = col * num_rows + row
                    if idx < len(sorted_items):
                        short, long = sorted_items[idx]
                        fields.append(f"{long} - `{short}`")
                field_name = title if col == 0 else '\u200b'
                embed.add_field(name=field_name, value="\n".join(fields), inline=True)
        
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
                embed = Embed(title="Map changed successfully", description=f"```{output}```", color=0x00ff00)
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

    end_time = time.time()  # End timer when the command has been fully processed
    print(f"Total Execution Time: {end_time - start_time} seconds")



@bot.command(name='debug')
async def debug(ctx):
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

def format_time(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"

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
            result = subprocess.run(['node', 'index.js'], capture_output=True, text=True, check=True)
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
                        team_name = player['team']
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