import os
import bson
import requests
import json
from datetime import datetime
from filelock import FileLock

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))  # Go up one directory level

# Create a 'data' directory if it doesn't exist
CACHE_DIR = os.path.join(BASE_DIR, 'cache')
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# Set the cache file paths inside the 'data' directory
CACHE_FILE_PATH = os.path.join(CACHE_DIR, "cache.json")
TEMP_CACHE_FILE_PATH = os.path.join(CACHE_DIR, "temp_cache.json")

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

    # Define cache file path for the desired queue
    queue_cache_file_path = os.path.join(CACHE_DIR, f"{queue}_cache.json")

    # If cached data exists for the queue, load and return it
    if os.path.exists(queue_cache_file_path):
        try:
            with FileLock(queue_cache_file_path + ".lock"):
                with open(queue_cache_file_path, 'r') as f:
                    cached_data = json.load(f)
                print(f"Loaded {len(cached_data)} games from cache for {queue} queue.")
                return cached_data
        except json.JSONDecodeError:
            print(f"Error loading cached data for {queue} queue. The cache file might be corrupt or empty.")
            return []


    # If no cached data, fetch from the API
    combined_data = []
    for url, queue_filter in zip(queue_mapping[queue]['urls'], queue_mapping[queue]['queue_filters']):
        response = requests.get(url)
        game_data = json.loads(response.text)
        
        filtered_data = [game for game in game_data
                         if game['queue']['name'] == queue_filter
                         and start_date <= datetime.fromtimestamp(game['timestamp'] / 1000) <= end_date]
        combined_data.extend(filtered_data)

        # Save the fetched data to cache for future use
        with open(queue_cache_file_path, 'w') as f:
            json.dump(combined_data, f)
        print(f"Fetched and cached {len(combined_data)} games for {queue} queue.")
    
    return combined_data

def save_to_bson(data, filepath):
    """Save the data to a BSON file."""
    with open(filepath, 'wb') as f:
        f.write(bson.BSON.encode(data))

def load_from_bson(filepath):
    """Load data from a BSON file. If the file doesn't exist or is empty, return an empty dictionary."""
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return {}
    with open(filepath, 'rb') as f:
        return bson.BSON(f.read()).decode()

