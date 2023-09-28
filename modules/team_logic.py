from itertools import combinations
from queue import PriorityQueue

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
    
    counter = 0  # Initialize a counter for unique differences
    for team_size in range(len(locked_team1_players), max_team_size + 1):
        team1_combinations = generate_combinations(unlocked_players, team_size - len(locked_team1_players))
        
        for team1_unlocked in team1_combinations:
            full_team1 = locked_team1_players + list(team1_unlocked)
            team2 = locked_team2_players + [player for player in unlocked_players if player not in team1_unlocked]
            
            if abs(len(full_team1) - len(team2)) > 1:
                continue
            
            difference = abs(sum(player['mu'] for player in full_team1) - sum(player['mu'] for player in team2))
            
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