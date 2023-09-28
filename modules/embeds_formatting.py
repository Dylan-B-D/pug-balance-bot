import trueskill
from discord import Embed, Colour
from modules.rating_calculations import (win_probability)

def format_names(team, captains, player_ratings):
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

def create_embed(balanced_teams, captains, player_ratings, title='Balanced Teams', map_name='Unknown Map', map_url='https://i.ibb.co/QM564R3/arx.jpg', description=None, rebalance_reactions=0, skip_map_reactions=0):
    team1_names = format_names(balanced_teams['team1'], captains, player_ratings)
    team2_names = format_names(balanced_teams['team2'], captains, player_ratings)

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

    # If a description is provided, add it to the embed.
    if description:
        balanced_teams_embed.description = description

    return balanced_teams_embed



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


def format_time(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"