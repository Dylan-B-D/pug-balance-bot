import discord
from discord.ext import commands
from discord import Embed, Colour
import re
import json
import requests
from datetime import datetime
from player_mappings import player_name_mapping
import trueskill
from math import erf, sqrt
from scipy.special import logit
import itertools
import math
from itertools import combinations

TOKEN = 'BOT_TOKEN'

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

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
    best_difference = float('inf')
    best_team1 = []
    best_team2 = []
    
    # Separate players into locked and unlocked, and assign captains to their respective locked teams
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
            
            if difference < best_difference:
                best_difference = difference
                best_team1 = full_team1
                best_team2 = team2
                
    return {'team1': best_team1, 'team2': best_team2}

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

@bot.event
async def on_message(message):
    await bot.process_commands(message)

    if message.embeds:
        embed = message.embeds[0]
        # Check if 'Captains:' is in the embed description instead of the title
        if embed.description and "Captains:" in embed.description:
            end_date = datetime.now()
            start_date = datetime(2018, 1, 1)
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


            await send_match_results(message.channel, matched_results)
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
    return matched_results



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



async def send_balanced_teams(channel, players, player_ratings, captains):
    try:
        balanced_teams = balance_teams(players, captains)
        
        team1 = balanced_teams['team1']
        team2 = balanced_teams['team2']
        
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

        
        team1_names = format_names(team1)
        team2_names = format_names(team2)
        
        default_rating = trueskill.Rating(mu=15, sigma=5)
        team1_ratings = [player_ratings.get(player['id'], default_rating) for player in team1]
        team2_ratings = [player_ratings.get(player['id'], default_rating) for player in team2]

        
        win_prob_team1 = win_probability(team1_ratings, team2_ratings)
        
        balanced_teams_embed = Embed(title='Balanced Teams', colour=Colour.purple())
        
        # Set inline to True to make fields appear side by side
        balanced_teams_embed.add_field(name='Team 1', value='\n'.join(team1_names) or '[Empty]', inline=True)
        balanced_teams_embed.add_field(name='\u200B', value='\u200B', inline=True)  # This acts as a spacer.
        balanced_teams_embed.add_field(name='Team 2', value='\n'.join(team2_names) or '[Empty]', inline=True)

        balanced_teams_embed.add_field(name='Win Chance for Team 1', value=f'{win_prob_team1*100:.2f}%', inline=False)
        
        await channel.send(embed=balanced_teams_embed)

    
    except Exception as e:
        print(f"Error in sending balanced teams info: {e}")




def get_user_id_by_name(guild, username):
    member = discord.utils.get(guild.members, name=username) or discord.utils.get(guild.members, display_name=username)
    if member:
        return member.id
    return None


@bot.command()
async def match(ctx):
    embed = discord.Embed(
        title="",
        description="**Captains: <@140028568062263296> & <@252190261734670336>**\n"
                    "crodog5, karucieldemonio, mikesters17, Nerve, cjplayz_, rockstaruniverse, "
                    "Dodge, gratismatt, grethsc, frogkabobs, jacktheblack, vorpalkitty",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)


bot.run(TOKEN)