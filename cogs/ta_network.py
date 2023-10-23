import json
import subprocess
import asyncio
import os
import time
import discord
import traceback
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
                if timeRemaining == 0:
                    if server["id"] not in self.finished_arena_games:
                        self.save_game_to_history(server)
                        self.finished_arena_games.add(server["id"])
                        continue
                else:
                    # If the time resets, assume a new game has started
                    if old_timeRemaining == 0 and timeRemaining > old_timeRemaining:
                        self.finished_arena_games.remove(server["id"])
                    # If the score increases, save the game to history
                    elif (bloodEagle_score > old_bloodEagle_score) or (diamondSword_score > old_diamondSword_score):
                        self.save_game_to_history(server)
                        continue
            # Existing CTF logic
            elif gamemode == "CTF" and timeRemaining == 0:
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
            elif gamemode == "Arena" and timeRemaining == 0:
                self.save_game_to_history(server)
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
        
        # Load previous completed games
        if os.path.exists(completed_games_file):
            with open(completed_games_file, 'r') as f:
                completed_games = json.load(f)
        else:
            completed_games = []

        # Correct the timeRemaining value using the cache history
        if server["id"] in self.cache_history:
            for past_server in reversed(self.cache_history[server["id"]]):
                if past_server["timeRemaining"] > 60:  # More than 1 minute
                    server["timeRemaining"] = past_server["timeRemaining"]
                    break
                elif past_server["timeRemaining"] > 0:  # Between 1 second and 1 minute
                    server["timeRemaining"] = 0
                    break

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

        # remove_duplicate_games()
    


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
    

def remove_duplicate_games():
    completed_games_file = os.path.join(DATA_DIR, "completed_games.json")
    
    # Step 1: Load the completed_games.json file into memory.
    with open(completed_games_file, 'r') as file:
        games = json.load(file)

    unique_games = {}
    
    # Step 2 and 3: Process each game and store in unique_games.
    for game in games:
        # Create a unique key for the game excluding the completionTimestamp.
        key = json.dumps({k: v for k, v in game.items() if k != "completionTimestamp"})
        if key not in unique_games:
            unique_games[key] = game

    # Step 5: Write the cleaned data back to completed_games.json.
    with open(completed_games_file, 'w') as file:
        json.dump(list(unique_games.values()), file)

    print(f"Original number of games: {len(games)}")
    print(f"Number of games after removing duplicates: {len(unique_games)}")



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