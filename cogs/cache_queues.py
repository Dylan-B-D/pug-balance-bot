import aiohttp
from filelock import FileLock
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import os
import json

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CACHE_DIR = os.path.join(BASE_DIR, 'cache')

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

CACHE_FILE_PATH = os.path.join(CACHE_DIR, "cache.json")
TEMP_CACHE_FILE_PATH = os.path.join(CACHE_DIR, "temp_cache.json")


class QueueCacheCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_queues_cache.start()

    @tasks.loop(seconds=300)
    async def update_queues_cache(self):
        try:
            for queue in ['2v2', 'NA', 'ALL']:
                data = await fetch_data(datetime(year=2018, month=1, day=1), datetime.now(), queue)
                self.save_queue_to_cache(queue, data)
        except Exception as e:
            print(f"An error occurred while updating the queues cache: {e}")

    def save_queue_to_cache(self, queue, data):
        queue_cache_file_path = os.path.join(CACHE_DIR, f"{queue}_cache.json")
        with FileLock(queue_cache_file_path + ".lock"):
            with open(queue_cache_file_path, 'w') as f:
                json.dump(data, f)


async def fetch_data(start_date, end_date, queue):
    base_url = 'http://50.116.36.119/api/server/'

    queue_mapping = {
        '2v2': {
            'urls': [base_url + '631438713183797258/games'],
            'queue_filters': ['2v2']
        },
        'NA': {
            'urls': [base_url + '631438713183797258/games'],
            'queue_filters': ['PUGz']
        },
        'ALL': {
            'urls': [base_url + '631438713183797258/games', base_url + '631438713183797258/games'],
            'queue_filters': ['2v2', 'PUGz']
        }
    }

    if queue not in queue_mapping:
        raise ValueError(f"Invalid queue: {queue}")

    combined_data = []
    
    async with aiohttp.ClientSession() as session:
        for url, queue_filter in zip(queue_mapping[queue]['urls'], queue_mapping[queue]['queue_filters']):
            async with session.get(url) as response:
                game_data = await response.json()

                filtered_data = [game for game in game_data
                                 if game['queue']['name'] == queue_filter
                                 and start_date <= datetime.fromtimestamp(game['timestamp'] / 1000) <= end_date]
                combined_data.extend(filtered_data)

    return combined_data