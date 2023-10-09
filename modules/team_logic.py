import statistics
import trueskill
from itertools import combinations
from queue import PriorityQueue
from data.capper_data import capper_value_mapping

CAPPER_PENALTY = 5
DEVIATION_EXPONENT = 0 
NEW_PLAYER_PENALTY = 20

def generate_combinations(players, team_size):
    if team_size <= 0:
        return [[]]
    return list(combinations(players, team_size))

def balance_teams(players, captains, player_games, avg_picks, player_ratings):
    best_teams = PriorityQueue()
    
    locked_team1_players = [player for player in players if player['id'] == captains[0]]
    locked_team2_players = [player for player in players if player['id'] == captains[1]]
    unlocked_players = [player for player in players if player not in locked_team1_players + locked_team2_players]
    
    max_team_size = min(7, len(players) // 2)
    
    counter = 0  # Initialize a counter for unique differences
    mean_rating = sum(player['mu'] for player in players) / len(players)
    
    for team_size in range(len(locked_team1_players), max_team_size + 1):
        team1_combinations = generate_combinations(unlocked_players, team_size - len(locked_team1_players))
        
        for team1_unlocked in team1_combinations:
            full_team1 = locked_team1_players + list(team1_unlocked)
            team2 = locked_team2_players + [player for player in unlocked_players if player not in team1_unlocked]
            
            if abs(len(full_team1) - len(team2)) > 1:
                continue
            
            # Compute the standard deviation for each team's ratings
            std_dev_team1 = statistics.stdev(player_ratings.get(player['id'], trueskill.Rating(mu=9, sigma=3)).mu for player in full_team1)
            std_dev_team2 = statistics.stdev(player_ratings.get(player['id'], trueskill.Rating(mu=9, sigma=3)).mu for player in team2)
            
            # Compute a penalty based on the deviation from the mean rating
            penalty_team1 = (std_dev_team1 - mean_rating) ** DEVIATION_EXPONENT
            penalty_team2 = (std_dev_team2 - mean_rating) ** DEVIATION_EXPONENT
            
            # Compute new player imbalance
            new_players_team1 = sum(1 for player in full_team1 if player_games.get(player['id'], 0) < 100 and avg_picks.get(player['id'], 0) > 8)
            new_players_team2 = sum(1 for player in team2 if player_games.get(player['id'], 0) < 100 and avg_picks.get(player['id'], 0) > 8)
            
            new_player_imbalance = NEW_PLAYER_PENALTY * abs(new_players_team1 - new_players_team2)
            
            # Calculate the total capper value for each team
            total_capper_value_team1 = sum(capper_value_mapping.get(player['id'], 0) for player in full_team1)
            total_capper_value_team2 = sum(capper_value_mapping.get(player['id'], 0) for player in team2)

            # Introduce a penalty if the total capper value for a team exceeds a threshold
            if total_capper_value_team1 > 1.5:
                new_player_imbalance += CAPPER_PENALTY
            if total_capper_value_team2 > 1.5:
                new_player_imbalance += CAPPER_PENALTY

            difference = (abs(sum(player['mu'] for player in full_team1) - sum(player['mu'] for player in team2)) + 
                          penalty_team1 + penalty_team2 + new_player_imbalance)
            
            # Check if teams are within 4% of a 50/50 balance
            total_rating = sum(player['mu'] for player in players)
            team1_rating_percentage = sum(player['mu'] for player in full_team1) / total_rating
            if 0.47 <= team1_rating_percentage <= 0.53:
                # Reduce the penalties
                penalty_team1 /= 2
                penalty_team2 /= 2
                new_player_imbalance /= 2
            
            # Add a tiny unique value to the difference
            difference += counter * 1e-10
            counter += 1

            # Negative difference because PriorityQueue is a min-heap and we want max difference at the top
            if best_teams.qsize() < 5 or -difference > best_teams.queue[0][0]:
                best_teams.put((-difference, {'team1': full_team1, 'team2': team2}))
                
                # If we have more than 5 items in the queue, remove the smallest one
                if best_teams.qsize() > 3:
                    best_teams.get()
                    
    return [best_teams.get()[1] for _ in range(min(5, best_teams.qsize()))][::-1]