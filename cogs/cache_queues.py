from filelock import FileLock
import requests
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import os
import json

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))  # Go up one directory level

# Create a 'data' directory if it doesn't exist
CACHE_DIR = os.path.join(BASE_DIR, 'cache')
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# Set the cache file paths inside the 'data' directory
CACHE_FILE_PATH = os.path.join(CACHE_DIR, "cache.json")
TEMP_CACHE_FILE_PATH = os.path.join(CACHE_DIR, "temp_cache.json")


class QueueCacheCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_queues_cache.start()

    @tasks.loop(seconds=60) 
    async def update_queues_cache(self):
        try:
            for queue in ['2v2', 'NA', 'ALL']:
                # print(f"Fetching data for queue: {queue}")
                data = fetch_data(datetime.now() - timedelta(days=9999), datetime.now(), queue) 
                self.save_queue_to_cache(queue, data)
        except Exception as e:
            print(f"An error occurred while updating the queues cache: {e}")

    def save_queue_to_cache(self, queue, data):
        # Create a unique cache file for each queue
        queue_cache_file_path = os.path.join(CACHE_DIR, f"{queue}_cache.json")
        with FileLock(queue_cache_file_path + ".lock"):
            with open(queue_cache_file_path, 'w') as f:
                json.dump(data, f)


def fetch_data(start_date, end_date, queue):
    base_url = 'http://50.116.36.119/api/server/'
    
    # Define the mapping for URLs, JSON replaces, and queue filters
    queue_mapping = {
        '2v2': {
            'urls': [base_url + '631438713183797258/games'],
            'queue_filters': ['2v2']
        },
        'NA': {
            'urls': [base_url + '631438713183797258/games'],
            'queue_filters': ['PUGz']
        },
        'ALL': {  # Combined data fetching for both 2v2 and PUG
            'urls': [base_url + '631438713183797258/games', base_url + '631438713183797258/games'],
            'queue_filters': ['2v2', 'PUGz']
        }
    }

    if queue not in queue_mapping:
        raise ValueError(f"Invalid queue: {queue}")

    combined_data = []
    for url, queue_filter in zip(queue_mapping[queue]['urls'], queue_mapping[queue]['queue_filters']):
        response = requests.get(url)
        game_data = json.loads(response.text)
        
        filtered_data = [game for game in game_data
                         if game['queue']['name'] == queue_filter
                         and start_date <= datetime.fromtimestamp(game['timestamp'] / 1000) <= end_date]
        combined_data.extend(filtered_data)

    # print(f"Fetched {len(combined_data)} games for {queue} queue.")
    return combined_data