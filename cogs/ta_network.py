import json
import subprocess
import asyncio
import os
import time
import discord
import traceback
from collections import defaultdict
from discord.ext import commands, tasks
from filelock import FileLock
from discord import Embed
from modules.embeds_formatting import format_time

# Countdown
countdown_task = None

# Node path variable
NODE_PATH = os.environ.get('NODE_PATH', 'node')

# Cache for fetched servers list
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))  # Go up one directory level

# Create a 'data' directory if it doesn't exist
CACHE_DIR = os.path.join(BASE_DIR, 'cache')
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

DATA_DIR = os.path.join(BASE_DIR, 'data')
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# Set the cache file paths inside the 'data' directory
CACHE_FILE_PATH = os.path.join(CACHE_DIR, "cache.json")
TEMP_CACHE_FILE_PATH = os.path.join(CACHE_DIR, "temp_cache.json")



class TANetworkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache = None
        self.finished_arena_games = set()
        self.previous_cache = []
        self.last_status_message = None
        self.cache = self.load_current_cache()
        self.active_games = {}
        self.update_cache.start()
        self.cache_history = {}

    @commands.Cog.listener()
    async def on_ready(self):
        await self.update_cache()

    def load_current_cache(self):
        return load_cache_from_file()
    
    @tasks.loop(seconds=30)
    async def update_cache(self):
        try:
            # Create subprocess.
            process = await asyncio.create_subprocess_exec(
                NODE_PATH, 'ta-network-api/index.js',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Wait for the subprocess to finish and get the stdout and stderr.
            stdout, stderr = await process.communicate()

            decoded_stderr = stderr.decode().strip()
            decoded_stdout = stdout.decode().strip()

            # Log the content of stdout and stderr for debugging purposes.
            # print(f"Subprocess stdout: {decoded_stdout}")
            # print(f"Subprocess stderr: {decoded_stderr}")

            # Check the returncode to see if subprocess exited with an error.
            if process.returncode != 0:
                print(f"Subprocess returned a non-zero exit code: {process.returncode}")
                raise Exception(f"Subprocess exited with error: {decoded_stderr}")

            if not decoded_stdout:
                print("Subprocess stdout is empty. Skipping JSON parsing.")
                return

            new_cache = json.loads(decoded_stdout)
            self.process_active_games(new_cache)

            with open(CACHE_FILE_PATH, 'w') as f:
                json.dump(new_cache, f)

            # Construct the new embed using the updated cache
            embed = discord.Embed(title="Server Status", color=0x00ff00)
            for server in new_cache:
                number_of_players = server.get('numberOfPlayers', 0)
                if number_of_players > 0:
                    raw_server_name = server.get('name', 'Unknown Server')
                    server_name = process_server_name(raw_server_name)

                    scores = "N/A"
                    if 'scores' in server:
                        scores = f"DS: {server['scores']['diamondSword']} - BE: {server['scores']['bloodEagle']}"

                    time_remaining = "N/A"
                    if 'timeRemaining' in server:
                        time_remaining = format_time(server['timeRemaining'])

                    embed.add_field(name=server_name, value=f"Scores: {scores}\nTime Remaining: {time_remaining}", inline=True)

            # Update the bot's presence and the last status message embed
            max_players_server = max(new_cache, key=lambda server: server.get('numberOfPlayers', 0))
            if max_players_server.get('numberOfPlayers', 0) > 0:
                server_name = max_players_server.get('name', 'Unknown Server')
                max_players = max_players_server.get('maxNumberOfPlayers', 0)
                current_players = max_players_server.get('numberOfPlayers', 0)
                scores = "N/A"
                time_remaining = "N/A"

                if 'scores' in max_players_server:
                    scores = f"{max_players_server['scores'].get('bloodEagle', 'N/A')} - {max_players_server['scores'].get('diamondSword', 'N/A')}"

                if 'timeRemaining' in max_players_server:
                    time_remaining = format_time(max_players_server['timeRemaining'])

                activity_message = f"Players in {server_name}: {current_players}/{max_players}, Score: {scores}, Time Remaining: {time_remaining}"
            else:
                activity_message = f"All Servers on 'PUG Login' Empty"

            await self.bot.change_presence(activity=discord.Game(name=activity_message))

            # Edit the last status message
            if hasattr(self, 'last_status_message') and self.last_status_message:  # Check if last_status_message exists and is not None
                await self.last_status_message.edit(embed=embed)

            # Before updating the main cache, update the history.
            for server in new_cache:
                if server["id"] not in self.cache_history:
                    self.cache_history[server["id"]] = []
                
                # Append the new cache to history, but limit history to the last 3 caches.
                self.cache_history[server["id"]].append(server)
                if len(self.cache_history[server["id"]]) > 3:
                    self.cache_history[server["id"]] = self.cache_history[server["id"]][-3:]
            
            # Update the current cache
            self.cache = new_cache

        except FileNotFoundError:
            print("Error: JSON file not found.")
        except json.JSONDecodeError:
            print("Error: Failed to decode JSON from subprocess output.")
        except ValueError as ve:
            print(f"ValueError: {ve}")
        except Exception as e:
            print(f"An unexpected error occurred while updating the cache: {e}")
            print("Traceback (most recent call last):")
            traceback.print_exc()

    

    def process_active_games(self, new_cache):
        if not hasattr(self, 'finished_games'):
            self.finished_games = set()

        # Create a set of server IDs in the new cache
        new_server_ids = {server["id"] for server in new_cache}

        for server in new_cache:
            old_server = next((s for s in self.cache if s["id"] == server["id"]), None)
            
            # Check if old_server is None before using it
            if old_server is None:
                # print("Warning: old_server is None!")
                continue

            # Ensure necessary fields are present before processing
            if not all(key in server for key in ["id", "map", "scores", "timeRemaining"]):
                continue

            gamemode = server["map"]["gamemode"]
            bloodEagle_score = server["scores"]["bloodEagle"]
            diamondSword_score = server["scores"]["diamondSword"]
            timeRemaining = server["timeRemaining"]

            if old_server:
                # Ensure old server has the necessary fields
                if not all(key in old_server for key in ["scores", "timeRemaining"]):
                    continue

                old_bloodEagle_score = old_server["scores"]["bloodEagle"]
                old_diamondSword_score = old_server["scores"]["diamondSword"]
                old_timeRemaining = old_server["timeRemaining"]
            else:
                old_bloodEagle_score, old_diamondSword_score, old_timeRemaining = 0, 0, None

            # Check for Arena game completion
            if gamemode == "Arena":
                # If there's a significant time jump, consider it an end condition
                time_jump = old_timeRemaining and timeRemaining > old_timeRemaining and (old_timeRemaining - timeRemaining) > 90

                # If the game has ended (either by time running out, one team's score reaching 0, or a significant time jump)
                game_ended = timeRemaining == 0 or bloodEagle_score == 0 or diamondSword_score == 0 or time_jump

                # If the game has ended and it's not already marked as finished, save it
                if game_ended and server["id"] not in self.finished_arena_games:
                    self.save_game_to_history(server)
                    self.finished_arena_games.add(server["id"])
                    continue

                # If there's a score increase from the last saved state, indicating a new game, remove the server ID from finished_arena_games
                if (bloodEagle_score > old_bloodEagle_score or diamondSword_score > old_diamondSword_score):
                    self.finished_arena_games.discard(server["id"])  # Use discard to avoid KeyError


            # CTF logic
            if gamemode == "CTF" and timeRemaining == 0:
                if old_timeRemaining and old_timeRemaining == 0 and timeRemaining > old_timeRemaining and timeRemaining < 600:
                    self.save_game_to_history(server)
                    continue
                elif not old_timeRemaining or old_timeRemaining != 0:
                    continue


            # Check 2:
            if server["id"] in self.active_games and server["id"] not in new_server_ids:
                self.save_game_to_history(old_server)
                continue

            # Check 3:
            if gamemode == "CTF" and timeRemaining == 0:
                if old_timeRemaining and old_timeRemaining == 0 and timeRemaining > old_timeRemaining and timeRemaining < 600:
                    self.save_game_to_history(server)
                    continue
                elif not old_timeRemaining or old_timeRemaining != 0:
                    continue

            # Logic to update finished and active games lists
            if server["id"] in self.finished_games and (bloodEagle_score < old_bloodEagle_score or diamondSword_score < old_diamondSword_score):
                self.finished_games.remove(server["id"])
            elif old_timeRemaining and old_timeRemaining > timeRemaining and server["id"] not in self.active_games and server["id"] not in self.finished_games:
                server["startTimestamp"] = int(time.time())
                self.active_games[server["id"]] = server
            elif server["id"] in self.active_games:
                if bloodEagle_score == 1 or diamondSword_score == 1:
                    self.finished_games.add(server["id"])


    def save_game_to_history(self, server):
        # Check if server is None before using it
        if server is None:
            print("Warning: server is None in save_game_to_history!")
            return

        completed_games_file = os.path.join(DATA_DIR, "completed_games.json")

        # Correct the timeRemaining value and player list using the cache history
        if server["id"] in self.cache_history:
            # Prioritize the most recent cache with more players
            prior_cache_with_more_players = None

            for past_server in reversed(self.cache_history[server["id"]]):
                if past_server["timeRemaining"] > 60:  # More than 1 minute
                    server["timeRemaining"] = past_server["timeRemaining"]
                    break
                elif past_server["timeRemaining"] > 0:  # Between 1 second and 1 minute
                    server["timeRemaining"] = 0
                    break

                # Check if the past server has more players
                if len(past_server.get("players", [])) > len(server.get("players", [])):
                    prior_cache_with_more_players = past_server

            # If we found a past server with more players, update the current server's player data
            if prior_cache_with_more_players:
                server["players"] = prior_cache_with_more_players["players"]

        # Cleaning conditions (from remove_duplicate_games)
        server_name = server.get('name', 'Unknown Server').lower()  # Convert to lowercase for case-insensitive checks
        gamemode = server.get("map", {}).get("gamemode")
        timeRemaining = server.get("timeRemaining", 0)
        scores = server.get("scores", {})
        BE_score = scores.get("bloodEagle", 0)
        DS_score = scores.get("diamondSword", 0)

        if len(server.get('players', [])) < 2:
            print(f"Game from {server_name} not saved: Less than 2 players.")
            return
        if timeRemaining > 1080 and "cap" not in server_name:
            print(f"Game from {server_name} not saved: Time remaining over 18 mins without 'cap'.")
            return
        if gamemode == "CTF" and (BE_score > 10 or DS_score > 10) and "cap" not in server_name:
            print(f"Game from {server_name} not saved: CTF game score exceeds 10 without 'cap'.")
            return

        # Load previous completed games
        if os.path.exists(completed_games_file):
            with open(completed_games_file, 'r') as f:
                completed_games = json.load(f)
        else:
            completed_games = []

        # Clean up the server data before saving
        if "specificServerInfo" in server:
            # Retain the player info from specificServerInfo
            if "players" in server["specificServerInfo"]:
                server["players"] = server["specificServerInfo"]["players"]

            # Remove the specificServerInfo field
            del server["specificServerInfo"]

        # Add the current epoch timestamp to the server data
        server["completionTimestamp"] = int(time.time())

        # Append the cleaned-up game to the list
        completed_games.append(server)

        # Save the updated list
        with open(completed_games_file, 'w') as f:
            json.dump(completed_games, f)



    @commands.command(name='servers')
    async def servers(self, ctx):
        cache = load_cache_from_file()
        try:
            if cache:
                print("Serving from cache")
                servers = cache
            else:
                print("Cache is not available, fetching...")
                result = subprocess.run([NODE_PATH, 'ta-network-api/index.js',], capture_output=True, text=True, check=True)
                servers = json.loads(result.stdout)

            embed = Embed(title='\'PUG Login\' Game Servers', description='List of available game servers', color=0x00ff00)
            
            for server in servers:
                server_name = server.get('name', 'Unknown Server')
                
                if server.get('numberOfPlayers', 0) == 0:
                    server_info = (
                        f"Players: {server.get('numberOfPlayers', 0)}/{server.get('maxNumberOfPlayers', 0)}\n"
                    )
                    if 'map' in server:
                        server_info += f"Map: {server['map'].get('name', 'N/A')} ({server['map'].get('gamemode', 'N/A')})\n"
                else:
                    region_name = 'N/A'
                    if isinstance(server.get('region'), dict):
                        region_name = server['region'].get('name', 'N/A')
                    server_info = (
                        f"Region: {region_name}\n"
                        f"Players: {server.get('numberOfPlayers', 0)}/{server.get('maxNumberOfPlayers', 0)}\n"
                    )
                    if 'scores' in server:
                        server_info += f"Scores: BE: {server['scores'].get('bloodEagle', 'N/A')} - {server['scores'].get('diamondSword', 'N/A')} :DS\n"
                        
                    if 'map' in server:
                        server_info += f"Map: {server['map'].get('name', 'N/A')} ({server['map'].get('gamemode', 'N/A')})\n"
                        
                    if 'timeRemaining' in server:
                        server_info += f"Time Remaining: {format_time(server.get('timeRemaining', 'N/A'))}\n"

                    if 'specificServerInfo' in server and 'players' in server['specificServerInfo']:
                        player_info = {'Blood Eagle': [], 'Diamond Sword': [], 'Unknown Team': []}  # Add 'Unknown Team' key
                        for player in server['specificServerInfo']['players']:
                            team_name = player.get('team', 'Unknown Team')
                            
                            # Check if the team name is valid
                            if team_name not in player_info:
                                team_name = 'Unknown Team'
                            
                            player_info[team_name].append(player.get('name', 'Unknown Player'))
                        
                        for team, players in player_info.items():
                            if players:  # Only display teams with players
                                server_info += f"{team}: {', '.join(players)}\n"

                embed.add_field(name=server.get('name', 'Unknown Server'), value=server_info, inline=False)


            await ctx.send(embed=embed)
        except subprocess.CalledProcessError as e:
            error_embed = Embed(title="Error", description="An error occurred while fetching the server information. Please try again later.", color=0xFF0000)
            await ctx.send(embed=error_embed)
        except json.JSONDecodeError as e:
            error_embed = Embed(title="JSON Error", description="There was an issue decoding the server information.", color=0xFF0000)
            await ctx.send(embed=error_embed)
        except Exception as e:
            print(f"Unexpected Error: {e}")
            import traceback
            traceback.print_exc()
            error_embed = Embed(title="Unexpected Error", description="An unexpected error occurred. Please try again later.", color=0xFF0000)
            await ctx.send(embed=error_embed)



    @commands.command(name='status')
    async def status(self, ctx): 
        cache = load_cache_from_file()

        try:
            if cache:
                print("Serving from cache")
                servers = cache
            else:
                print("Cache is not available, fetching...")
                result = subprocess.run([NODE_PATH, 'ta-network-api/index.js',], capture_output=True, text=True, check=True)
                servers = json.loads(result.stdout)
                
            # Create an embed
            embed = discord.Embed(color=0x00ff00)
            has_active_servers = False  # Flag to check if there are servers with players

            # Iterate over each server and add its details to the embed
            for server in servers:
                number_of_players = server.get('numberOfPlayers', 0)

                # Only process servers with at least 1 player
                if number_of_players > 0:
                    has_active_servers = True
                    raw_server_name = server.get('name', 'Unknown Server')
                    server_name = process_server_name(raw_server_name)

                    # Scores
                    scores = "N/A"
                    if 'scores' in server:
                        scores = f"DS: {server['scores']['diamondSword']} - BE: {server['scores']['bloodEagle']}"

                    # Time Remaining
                    time_remaining = "N/A"
                    if 'timeRemaining' in server:
                        time_remaining = format_time(server['timeRemaining'])

                    # Add the server details to the embed as a field
                    embed.add_field(name=server_name, value=f"Scores: {scores}\nTime Remaining: {time_remaining}", inline=True)

            # If there are servers with players, store the message reference and send the embed
            if has_active_servers:
                self.last_status_message = await ctx.send(embed=embed)

        except subprocess.CalledProcessError as e:
            error_embed = discord.Embed(title="Error", description="An error occurred while fetching the server information. Please try again later.", color=0xFF0000)
            await ctx.send(embed=error_embed)
        except json.JSONDecodeError as e:
            error_embed = discord.Embed(title="JSON Error", description="There was an issue decoding the server information.", color=0xFF0000)
            await ctx.send(embed=error_embed)
        except Exception as e:
            error_embed = discord.Embed(title="Unexpected Error", description="An unexpected error occurred. Please try again later.", color=0xFF0000)
            await ctx.send(embed=error_embed)
    

    @commands.command(name='cleancache')
    async def clean_cache(self, ctx):
        result = remove_duplicate_games()
        embed = discord.Embed(title="Cache Cleaned", color=0x00ff00)

        embed.add_field(name="Original Number of Games", value=result["original_count"], inline=True)
        embed.add_field(name="Unique Games After Cleanup", value=result["unique_count"], inline=True)
        embed.add_field(name="File Size Before", value=f"{result['file_size_before'] / 1024:.2f} KB", inline=True)
        embed.add_field(name="File Size After", value=f"{result['file_size_after'] / 1024:.2f} KB", inline=True)

        server_counts_str = '\n'.join([f"{server}: {count}" for server, count in result["server_counts"].items()])
        embed.add_field(name="Server Counts", value=server_counts_str, inline=False)

        cleaned_counts_str = '\n'.join([f"{server}: {count} cleaned" for server, count in result["cleaned_server_counts"].items() if count > 0])
        embed.add_field(name="Cleaned Server Counts", value=cleaned_counts_str, inline=False)

        await ctx.send(embed=embed)



def remove_duplicate_games():
    completed_games_file = os.path.join(DATA_DIR, "completed_games.json")

    # Step 1: Load the completed_games.json file into memory.
    with open(completed_games_file, 'r') as file:
        games = json.load(file)

    unique_games = {}
    server_counts = defaultdict(int)
    cleaned_server_counts = defaultdict(int)

    # Step 2: Count all games before processing
    for game in games:
        server_name = game.get('name', 'Unknown Server')
        server_counts[server_name] += 1

    # Step 3: Process each game and store in unique_games.
    for game in games:
        server_name = game.get('name', 'Unknown Server').lower()  # Convert to lowercase for case-insensitive checks
        gamemode = game.get("map", {}).get("gamemode")
        timeRemaining = game.get("timeRemaining", 0)
        scores = game.get("scores", {})
        BE_score = scores.get("bloodEagle", 0)
        DS_score = scores.get("diamondSword", 0)

        # Cleaning conditions
        if len(game.get('players', [])) < 2:
            cleaned_server_counts[game.get('name', 'Unknown Server')] += 1
            continue
        if timeRemaining > 1080 and "cap" not in server_name:
            cleaned_server_counts[game.get('name', 'Unknown Server')] += 1
            continue
        if gamemode == "CTF" and (BE_score > 10 or DS_score > 10) and "cap" not in server_name:
            cleaned_server_counts[game.get('name', 'Unknown Server')] += 1
            continue

        # Create a unique key for the game excluding the completionTimestamp.
        key = json.dumps({k: v for k, v in game.items() if k != "completionTimestamp"})

        if key not in unique_games:
            unique_games[key] = game

    # File size before
    file_size_before = os.path.getsize(completed_games_file)

    # Step 4: Write the cleaned data back to completed_games.json.
    with open(completed_games_file, 'w') as file:
        json.dump(list(unique_games.values()), file)

    # File size after
    file_size_after = os.path.getsize(completed_games_file)

    return {
        "original_count": len(games),
        "unique_count": len(unique_games),
        "file_size_before": file_size_before,
        "file_size_after": file_size_after,
        "server_counts": server_counts,
        "cleaned_server_counts": cleaned_server_counts
    }



def load_cache_from_file():
    # Check if the file doesn't exist, and create an empty JSON object if it doesn't
    if not os.path.exists(CACHE_FILE_PATH):
        with open(CACHE_FILE_PATH, 'w') as f:
            json.dump({}, f)
    
    with FileLock(CACHE_FILE_PATH + ".lock"):
        with open(CACHE_FILE_PATH, 'r') as f:
            data = json.load(f)
        return data

def save_cache_to_file(data):
    with FileLock(TEMP_CACHE_FILE_PATH + ".lock"):
        with open(TEMP_CACHE_FILE_PATH, 'w') as f:
            json.dump(data, f)
    # Rename the temporary file to the actual cache file
    os.rename(TEMP_CACHE_FILE_PATH, CACHE_FILE_PATH)

def process_server_name(raw_name: str) -> str:
    # Remove the mentioned prefixes
    if raw_name.startswith("GOTY |"):
        raw_name = raw_name[7:]
    elif raw_name.startswith("OOTB |"):
        raw_name = raw_name[7:]

    # Truncate the name and add "..." if it's longer than 15 characters
    if len(raw_name) > 19:
        raw_name = raw_name[:19] + ".."
    
    return raw_name

async def countdown(message, time_remaining, server_with_id_3):
    try:
        while time_remaining > 0:
            if server_with_id_3['scores']['diamondSword'] == 7:
                await message.edit(content="Diamond Sword wins!")
                return
            if server_with_id_3['scores']['bloodEagle'] == 7:
                await message.edit(content="Blood Eagle wins!")
                return
            
            embed = message.embeds[0]  # Get the existing embed from the message
            mins, secs = divmod(time_remaining, 60)
            embed.set_field_at(2, name='Time Remaining', value=f"{mins}m {secs}s", inline=True)  # Update the time_remaining field in the embed
            await message.edit(embed=embed)
            
            await asyncio.sleep(1)
            time_remaining -= 1
        
        await message.edit(content="Game Over!")
    except asyncio.CancelledError:
        pass  # Task has been cancelled, do nothing
    except Exception as e:
        await message.edit(content=f"An unexpected error occurred: {e}")