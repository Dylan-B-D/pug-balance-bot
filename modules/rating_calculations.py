import itertools
import math
import trueskill
from math import erf, sqrt
from scipy.special import logit

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


def compute_logit_bonus(pick_order, total_picks=12, delta=0.015):
    normalized_order = (total_picks - pick_order) / (total_picks - 1)
    logit_value = logit(normalized_order * (1 - (2 * delta)) + delta)
    bonus = 20 * logit_value / abs(logit(delta))
    return bonus

def calculate_draw_rate(game_data):
    total_matches = len(game_data)
    draw_matches = sum(1 for game in game_data if game['winningTeam'] == 0)
    return draw_matches / total_matches if total_matches > 0 else 0

def initialize_player_data(game_data):
    player_ids = set(player['user']['id'] for match in game_data for player in match['players'])
    player_data = {
        'picks': {player_id: [] for player_id in player_ids},
        'wins': {player_id: 0 for player_id in player_ids},
        'names': {player['user']['id']: player['user']['name'] for match in game_data for player in match['players']},
        'games': {player_id: 0 for player_id in player_ids},
        'rating_history': {player_id: [] for player_id in player_ids}
    }
    return player_data

def process_matches(game_data, player_data):
    for match in game_data:
        team1_size = sum(1 for player in match['players'] if player['team'] == 1)
        team2_size = sum(1 for player in match['players'] if player['team'] == 2)

        # If teams are imbalanced, skip processing this match
        if team1_size != team2_size:
            continue
        
        for player in match['players']:
            player_id = player['user']['id']
            player_data['games'][player_id] += 1
            if player['pickOrder']:
                player_data['picks'][player_id].append(player['pickOrder'])
            if match['winningTeam'] == player['team']:
                player_data['wins'][player_id] += 1

def compute_avg_picks(player_picks):
    def recent_games_count(total_games):
        if total_games < 100:
            return total_games
        elif total_games < 150:
            return int(0.4 * total_games)
        else:
            return int(0.2 * total_games)

    avg_picks = {}
    for player_id, picks in player_picks.items():
        recent_games = recent_games_count(len(picks))
        avg_picks[player_id] = sum(picks[-recent_games:]) / recent_games if recent_games else 0
    return avg_picks

def adjust_ratings_based_on_pick_order(ts, player_ratings, player_games, avg_picks, queue, player_names):
    for player_id, avg_pick in avg_picks.items():
        if queue != '2v2':
            mu = ts.mu + compute_logit_bonus(avg_pick)
        else:
            mu = ts.mu
        sigma = ts.sigma

        # Only apply the adjustment for players with less than 100 games
        if player_games[player_id] < 100:    
                scaling_factor = 0.9 - 0.075 * (avg_pick - 8)
                mu = mu * scaling_factor

        sigma = min(sigma, (50 - mu) / 3, mu / 3)
        player_ratings[player_id] = trueskill.Rating(mu=mu, sigma=sigma)



def process_rating_adjustment(ts, game_data, player_ratings, player_data):
    for match in game_data:
        team1_size = sum(1 for player in match['players'] if player['team'] == 1)
        team2_size = sum(1 for player in match['players'] if player['team'] == 2)

        # If teams are imbalanced, skip processing this match
        if team1_size != team2_size:
            continue
        
        teams, team_player_ids = [], []
        for team_number in [1, 2]:
            team = [player_ratings[player['user']['id']] for player in match['players'] if player['team'] == team_number]
            player_ids = [player['user']['id'] for player in match['players'] if player['team'] == team_number]
            teams.append(team)
            team_player_ids.append(player_ids)

        if match['winningTeam'] == 2:
            teams.reverse()
            team_player_ids.reverse()

        try:
            new_ratings = ts.rate(teams)
        except FloatingPointError:
            continue
        
        for i, team in enumerate(teams):
            for j, player_rating in enumerate(team):
                player_id = team_player_ids[i][j]
                player_ratings[player_id] = new_ratings[i][j]
                rating_dict = {"mu": new_ratings[i][j].mu, "sigma": new_ratings[i][j].sigma}
                player_data['rating_history'][player_id].append(rating_dict)

def calculate_ratings(game_data, queue='NA'):
    draw_rate = calculate_draw_rate(game_data)
    custom_tau = 0.1
    ts = trueskill.TrueSkill(draw_probability=draw_rate, tau=custom_tau) if queue != 'NA' else trueskill.TrueSkill(tau=custom_tau)

    player_data = initialize_player_data(game_data)
    process_matches(game_data, player_data)
    avg_picks = compute_avg_picks(player_data['picks'])
    
    player_ratings = {player_id: trueskill.Rating(mu=9, sigma=3) for player_id in player_data['games'].keys()}
    adjust_ratings_based_on_pick_order(ts, player_ratings, player_data['games'], avg_picks, queue, player_data['names'])
    process_rating_adjustment(ts, game_data, player_ratings, player_data)

    return player_ratings, player_data['names'], player_data['games'], player_data['rating_history']
