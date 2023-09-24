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