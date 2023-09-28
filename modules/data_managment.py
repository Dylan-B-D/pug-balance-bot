import requests
import re
import json
from datetime import datetime

def fetch_data(start_date, end_date, queue):
    # Define a mapping for the URLs, replacements, and filters
    queue_mapping = {
        '2v2': {
            'urls': ['https://sh4z.se/pugstats/naTA.json'],
            'json_replaces': ['datanaTA = '],
            'queue_filters': ['2v2']
        },
        'NA': {
            'urls': ['https://sh4z.se/pugstats/naTA.json'],
            'json_replaces': ['datanaTA = '],
            'queue_filters': ['PUGz']
        },
        'ALL': {  # Combined data fetching for both 2v2 and PUG
            'urls': ['https://sh4z.se/pugstats/naTA.json', 'https://sh4z.se/pugstats/naTA.json'],
            'json_replaces': ['datanaTA = ', 'datanaTA = '],
            'queue_filters': ['2v2', 'PUGz']
        }
    }

    if queue not in queue_mapping:
        raise ValueError(f"Invalid queue: {queue}")

    combined_data = []
    for url, json_replace, queue_filter in zip(queue_mapping[queue]['urls'], queue_mapping[queue]['json_replaces'], queue_mapping[queue]['queue_filters']):
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

def count_games_for_ids(data, matched_ids):
    id_game_count = {id_: 0 for id_ in matched_ids}
    for game in data:
        for player in game['players']:
            user_id = str(player['user']['id'])
            if user_id in id_game_count:
                id_game_count[user_id] += 1
    return id_game_count