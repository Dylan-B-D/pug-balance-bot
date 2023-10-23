# ---- STANDARD LIBRARY IMPORTS ---- #
import asyncio
import bson
import os
import random
import time
from datetime import datetime

# ---- THIRD-PARTY IMPORTS ---- #
import discord
import trueskill
from discord import Colour
from discord.ext import commands, tasks

# Custom modules
from data.player_mappings import player_name_mapping
from modules.data_managment import fetch_data
from modules.utilities import (send_balanced_teams, check_bot_admin)
from modules.rating_calculations import calculate_ratings

# ---- CONSTANTS ---- #
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))  
CACHE_DIR = os.path.join(BASE_DIR, 'cache', 'gamequeue')
DATA_DIR = os.path.join(BASE_DIR, 'data', 'gamequeue')

AFK_TIME_LIMIT_MINUTES = 90  # Default AFK time limit
AFK_TIMES_FILE = os.path.join(DATA_DIR, 'afk_times.bson')

CURRENT_SAVE_VERSION = "1.0" # Formatting version for saving games to BSON

QUEUE_SIZE_TO_COLOR = {
    2: discord.ButtonStyle.secondary,
    4: discord.ButtonStyle.secondary,
    6: discord.ButtonStyle.secondary, 
    8: discord.ButtonStyle.secondary,
    10: discord.ButtonStyle.secondary,
    12: discord.ButtonStyle.secondary,
    14: discord.ButtonStyle.primary,
    16: discord.ButtonStyle.secondary,
    18: discord.ButtonStyle.secondary,
    20: discord.ButtonStyle.secondary,
    22: discord.ButtonStyle.secondary,
    24: discord.ButtonStyle.secondary,
    26: discord.ButtonStyle.secondary,
    28: discord.ButtonStyle.secondary,
}

# ---- LOG OF RECENT BOT INTERACTIONS ---- #
QUEUE_LOG = {}

# ---- DIRECTORY SETUP ---- #
# Create directories if they don't exist
for directory in [CACHE_DIR, DATA_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

# TODO Substitution
# TODO Add offline limit and kick
# TODO Add min games to captain, add weighting for equal skill, add reduced weight if captained recently
# TODO Add subcaptaining

class QueueButton(discord.ui.Button):
    def __init__(self, label, queue_view, style=discord.ButtonStyle.primary):
        super().__init__(label=label, style=style)
        self.queue_view = queue_view

    async def callback(self, interaction: discord.Interaction):
            queue_name = self.label
            response = add_player_to_queue(self.queue_view.guild_id, self.queue_view.channel_id, queue_name, interaction.user.id, self.queue_view.bot)

            if response == "Player already in the queue.":
                response = remove_player_from_queue(self.queue_view.guild_id, self.queue_view.channel_id, queue_name, interaction.user.id)
                embed = discord.Embed(description=response, color=discord.Color.red())
                await interaction.response.send_message(embed=embed, ephemeral=True)
                updated_embed = self.queue_view.generate_updated_embed()
                await interaction.message.edit(embed=updated_embed)
                return

            if "Added to" in response:
                embed = discord.Embed(description=response, color=discord.Color.green())
                await interaction.response.send_message(embed=embed, ephemeral=True)
                updated_embed = self.queue_view.generate_updated_embed()
                await interaction.message.edit(embed=updated_embed)
            else:
                await interaction.response.send_message(response, ephemeral=True)


class QueueView(discord.ui.View): 
    def __init__(self, queues, guild_id, channel_id, user, bot):
            super().__init__()
            self.guild_id = guild_id
            self.channel_id = channel_id
            self.user = user
            self.bot = bot

            # Sort queues based on size, largest first
            sorted_queues = sorted(queues.items(), key=lambda x: x[1]['size'], reverse=True)

            # Dynamically create buttons based on sorted queue names
            for queue_name, queue_info in sorted_queues:
                queue_size = queue_info['size']
                button_style = QUEUE_SIZE_TO_COLOR.get(queue_size, discord.ButtonStyle.secondary) # Default to grey if size not mapped
                self.add_item(QueueButton(label=queue_name, queue_view=self, style=button_style))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only allow the original user to interact with the buttons
        # return self.user == interaction.user
        return True

    async def queue_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        queue_name = button.label
        response = add_player_to_queue(self.guild_id, self.channel_id, queue_name, interaction.user.id, self.queue_view.bot)

        if response == "Player added to queue.":
            updated_embed = self.generate_updated_embed()
            await interaction.message.edit(embed=updated_embed)
        else:
            await interaction.response.send_message(response, ephemeral=True)

    def generate_updated_embed(self):
        return generate_queue_embed(self.guild_id, self.channel_id, self.bot)

class PugQueueCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.check_stale_users.start()    

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        if not isinstance(interaction, discord.Interaction):  # Check if it's a button interaction
            return

        if interaction.user.bot:
            return

        # print(f"Interaction received from {interaction.user.name}. Checking for update...")

        guild_id = interaction.guild.id
        current_queues = get_current_queues()
        player_updated = False

        for channel_id, queues in current_queues.get(str(guild_id), {}).items():
            for queue_name, queue_data in queues.items():
                for idx, (player_id, timestamp) in enumerate(queue_data["members"]):
                    if player_id == interaction.user.id:
                        queue_data["members"][idx] = (player_id, datetime.utcnow().timestamp())
                        player_updated = True
                        # print(f"Updated timestamp for {interaction.user.name} in queue {queue_name} due to interaction.")
                        break

        # Save the updated queues to the BSON file
        if player_updated:  # Only save if we made an update
            save_to_bson(current_queues, os.path.join(CACHE_DIR, 'queues.bson'))


    @tasks.loop(minutes=0.1)
    async def check_stale_users(self):
        current_queues = get_current_queues()
        now = datetime.utcnow().timestamp()
        
        removals = {}  # Dictionary to track which users are removed from which queues

        for guild_id, channels in current_queues.items():
            for channel_id, queues in channels.items():
                # Fetch the AFK time for the specific server and channel
                afk_time_for_channel = get_afk_time(guild_id, channel_id)
                for queue_name, queue_data in queues.items():
                    for player_id, timestamp in list(queue_data["members"]):  # Use list to avoid runtime errors due to size changes
                        time_difference = now - timestamp
                        # print(f"Checking {player_name_mapping.get(int(player_id))}. Time since last message: {time_difference} seconds.")
                        if time_difference > afk_time_for_channel*60:  # Using the specific AFK time here
                            response = remove_player_from_queue(guild_id, channel_id, queue_name, player_id, reason="afk")
                            if response:
                                removals.setdefault((player_id, channel_id), {}).setdefault('queues', []).append(queue_name)
                                removals[(player_id, channel_id)]['afk_time'] = afk_time_for_channel

        for (player_id, channel_id), removal_data in removals.items():
            user = self.bot.get_user(int(player_id))
            
            # Send an embed PM to the user
            queues_str = ', '.join(f"`{queue_name}`" for queue_name in removal_data['queues'])
            embed_pm = discord.Embed(description=f"You were removed from {queues_str} due to being AFK for more than {removal_data['afk_time']} minutes.", color=0xff0000)
            await user.send(embed=embed_pm)
            
            # print(f"Removed {player_name_mapping.get(int(player_id))} from {queues_str}.")
            
            channel = self.bot.get_channel(int(channel_id))
            # Send an embed indicating the user was removed due to being AFK
            embed_msg = discord.Embed(description=f"{player_name_mapping.get(int(player_id))} was removed from {queues_str} for being AFK for more than {removal_data['afk_time']} minutes.", color=0xff0000)
            await channel.send(embed=embed_msg)


    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # print(f"Message received from {message.author.name}. Checking for update...")

        guild_id = message.guild.id
        current_queues = get_current_queues()
        player_updated = False

        for channel_id, queues in current_queues.get(str(guild_id), {}).items():
            for queue_name, queue_data in queues.items():
                for idx, (player_id, timestamp) in enumerate(queue_data["members"]):
                    if player_id == message.author.id:
                        queue_data["members"][idx] = (player_id, datetime.utcnow().timestamp())
                        player_updated = True
                        # print(f"Updated timestamp for {message.author.name} in queue {queue_name}.")
                        break

        # Save the updated queues to the BSON file
        if player_updated:  # Only save if we made an update
            save_to_bson(current_queues, os.path.join(CACHE_DIR, 'queues.bson'))


    @commands.command()
    async def menu(self, ctx):
        embed = generate_queue_embed(ctx.guild.id, ctx.channel.id, self.bot)
        view = QueueView(get_current_queues().get(str(ctx.guild.id), {}).get(str(ctx.channel.id), {}), ctx.guild.id, ctx.channel.id, ctx.author, self.bot)
        await ctx.send(embed=embed, view=view)


    @commands.command()
    async def log(self, ctx):
        header = "================= [ QUEUE LOG ] ================="
        footer = "=" * len(header)
        log_entries = [header]
        max_line_length = len(header) - 2  # -2 to account for the pipes at the start and end of each line

        # Fetch the log for the specific server-channel
        channel_log = QUEUE_LOG.get((ctx.guild.id, ctx.channel.id), [])

        for idx, entry in enumerate(reversed(channel_log), start=1):
            player_name, action, queue_name = entry
            if action == "added":
                action_text = "added to"
            elif action == "removed":
                action_text = "removed from"
            else:  # "removed-afk"
                action_text = "removed (AFK) from"
            log_entry = f"| [{idx:02}] {player_name} {action_text} `{queue_name}` "
            log_entries.append(log_entry.ljust(max_line_length, ' ') + "|")


        log_entries.append(footer)

        log_message = "\n".join(log_entries)
        
        await ctx.send(f"```\n{log_message}\n```")

    @commands.command()
    async def setafktime(self, ctx, minutes: float):
        """Set the AFK time for the current channel"""
        save_afk_time(ctx.guild.id, ctx.channel.id, minutes)
        embed = discord.Embed(description=f"AFK time has been set to {minutes} minutes for {ctx.channel.name}.", color=0x00ff00)
        await ctx.send(embed=embed)

    @commands.command()
    async def setpugchannel(self, ctx):
            
        if not await check_bot_admin(ctx):
            return

        current_channels = get_current_pug_channels()

        # Check if the channel is already set
        if str(ctx.channel.id) in current_channels.values():
            # Create an embed for the already set message
            embed = discord.Embed(title="", 
                                description=f"This channel ({ctx.channel.name}) is already set as a pug channel.", 
                                color=discord.Color.red())
            await ctx.send(embed=embed)
            return

        # Create an embed for the confirmation message
        embed = discord.Embed(title="", 
                            description=f"This channel ({ctx.channel.name}) has been set as the pug channel.", 
                            color=discord.Color.green())
        message = await ctx.send(embed=embed)

        # Wait for 10 seconds and then delete the message
        await asyncio.sleep(10)
        await message.delete()

        current_channels[str(ctx.guild.id)] = str(ctx.channel.id)
        save_to_bson(current_channels, os.path.join(DATA_DIR, 'pug_channel.bson'))




    @commands.command()
    async def removepugchannel(self, ctx):

        if not await check_bot_admin(ctx):
            return

        current_channels = get_current_pug_channels()

        # Check if the channel is not set
        if str(ctx.channel.id) not in current_channels.values():
            await ctx.send("This channel is not set as a pug channel.")
            return

        # Create and send the embed
        embed = discord.Embed(title="Pug Channel Removed", description=f"This channel ({ctx.channel.name}) has been removed from pug channels.", color=0xff0000)
        message = await ctx.send(embed=embed)

        # Wait for 10 seconds and then delete the message
        await asyncio.sleep(10)
        await message.delete()

        # Remove the channel from the BSON file
        del current_channels[str(ctx.guild.id)]
        save_to_bson(current_channels, os.path.join(DATA_DIR, 'pug_channel.bson'))


    @commands.command()
    async def createqueue(self, ctx, queuename: str = None, queuesize: int = None):

        if not await check_bot_admin(ctx):
            return

        current_channels = get_current_pug_channels()

        # Check if the command is being executed in a pug channel
        if str(ctx.guild.id) not in current_channels or str(ctx.channel.id) != current_channels[str(ctx.guild.id)]:
            embed = discord.Embed(title="", 
                                description=f"This is not a pug channel. Please use `!setpugchannel` to set the pug channel.", 
                                color=discord.Color.red())
            await ctx.send(embed=embed)
            return

        # Check for missing parameters
        if not queuename or not queuesize:
            embed = discord.Embed(title="", 
                                description=f"Incorrect usage. Use the command as:\n`!createqueue <queuename> <queuesize>` where `<queuename>` is the name of the queue and `<queuesize>` is the size of the queue.", 
                                color=discord.Color.red())
            await ctx.send(embed=embed)
            return

        # Load current queues
        current_queues = get_current_queues()

        # Check if queue already exists in this channel
        if queuename in current_queues.get(str(ctx.guild.id), {}).get(str(ctx.channel.id), {}):
            embed = discord.Embed(title="", 
                                description=f"A queue named '{queuename}' already exists in this channel.", 
                                color=discord.Color.red())
            await ctx.send(embed=embed)
            return

        # Add the new queue
        if str(ctx.guild.id) not in current_queues:
            current_queues[str(ctx.guild.id)] = {}
        if str(ctx.channel.id) not in current_queues[str(ctx.guild.id)]:
            current_queues[str(ctx.guild.id)][str(ctx.channel.id)] = {}

        current_queues[str(ctx.guild.id)][str(ctx.channel.id)][queuename] = {
            "size": queuesize,
            "members": []
        }

        # Save the updated queues
        save_to_bson(current_queues, os.path.join(CACHE_DIR, 'queues.bson'))

        embed = discord.Embed(title="", 
                                description=f"Queue '{queuename}' with size {queuesize} has been created.", 
                                color=discord.Color.green())
        await ctx.send(embed=embed)


    @commands.command()
    async def removequeue(self, ctx, queuename: str):

        if not await check_bot_admin(ctx):
            return

        current_channels = get_current_pug_channels()

        # Check if the command is being executed in a pug channel
        if str(ctx.guild.id) not in current_channels or str(ctx.channel.id) != current_channels[str(ctx.guild.id)]:
            return

        # Load current queues
        current_queues = get_current_queues()

        # Check if the queue exists
        if queuename not in current_queues.get(str(ctx.guild.id), {}).get(str(ctx.channel.id), {}):
            await ctx.send(f"No queue named '{queuename}' exists in this channel.")
            return

        # Remove the queue
        del current_queues[str(ctx.guild.id)][str(ctx.channel.id)][queuename]

        # Check if the channel has any more queues, if not, remove the channel key
        if not current_queues[str(ctx.guild.id)][str(ctx.channel.id)]:
            del current_queues[str(ctx.guild.id)][str(ctx.channel.id)]

        # Check if the server has any more channels with queues, if not, remove the server key
        if not current_queues[str(ctx.guild.id)]:
            del current_queues[str(ctx.guild.id)]

        # Save the updated queues
        save_to_bson(current_queues, os.path.join(CACHE_DIR, 'queues.bson'))


        await ctx.send(f"Queue '{queuename}' has been removed.")

    @commands.command()
    async def pugsettings(self, ctx):

        if not await check_bot_admin(ctx):
            return
        
        current_channels = get_current_pug_channels()
        current_queues = get_current_queues()

        # Check if the server has any pug channels
        if str(ctx.guild.id) not in current_channels:
            await ctx.send("This server doesn't have any pug channels set.")
            return

        # Fetch the pug channel(s) for this server
        pug_channel_id = current_channels[str(ctx.guild.id)]
        pug_channel = self.bot.get_channel(int(pug_channel_id))
        
        embed = discord.Embed(title="Pug Settings", description=f"Pug Channel(s) in {ctx.guild.name}", color=0x00ff00)
        
        # If there's a valid pug channel, add it to the embed
        if pug_channel:
            embed.add_field(name="Pug Channel", value=pug_channel.name, inline=False)

            # Fetch the queues for the pug channel
            channel_queues = current_queues.get(str(ctx.guild.id), {}).get(pug_channel_id, {})
            for queue_name, queue_info in channel_queues.items():
                embed.add_field(name=f"Queue: {queue_name}", value=f"Size: {queue_info['size']}", inline=False)
        else:
            embed.add_field(name="Error", value="Couldn't fetch the pug channel details.", inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    async def adduser(self, ctx, user_input, queue_name):
        if not await check_bot_admin(ctx):
            return
        user_id = get_user_id_from_input(ctx, user_input)
        if user_id is None:
            await ctx.send(f"User `{user_input}` not found!")
            return
        
        response = add_player_to_queue(ctx.guild.id, ctx.channel.id, queue_name, user_id, self.bot)
        await ctx.send(response)


    @commands.command()
    async def removeuser(self, ctx, user_input, queue_name):
        if not await check_bot_admin(ctx):
            return
        user_id = get_user_id_from_input(ctx, user_input)
        if user_id is None:
            await ctx.send(f"User `{user_input}` not found!")
            return
        
        response = remove_player_from_queue(ctx.guild.id, ctx.channel.id, queue_name, user_id)
        await ctx.send(response)

    @commands.command()
    async def end(self, ctx, flag: str = None):
        player_id = ctx.author.id

        # Load ongoing games
        ongoing_games = load_from_bson(os.path.join(CACHE_DIR, 'ongoing_games.bson'))


        # Check if the user is admin and provided the -f flag
        if flag == "-f" and await check_bot_admin(ctx):
            await forcefully_end_all_games(ctx, ongoing_games)
            return

        # Find the game the user is a captain of
        game_id = None
        for gid, game in ongoing_games.items():
            if player_id in game["captains"]:
                game_id = gid
                break

        if not game_id:
            embed = discord.Embed(
                title="",
                description="You are not a captain of any ongoing game.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        game = ongoing_games[game_id]

        # Check if the game is from the channel the command was executed in
        if game["channel_id"] != ctx.channel.id:
            return

        await end_individual_game(ctx, game_id, game)

        # Remove the game from ongoing games
        del ongoing_games[game_id]     
        save_to_bson(ongoing_games, os.path.join(CACHE_DIR, 'ongoing_games.bson'))

    @commands.command()
    async def subuser(self, ctx, *, user_input: str):
        substitute_id = ctx.author.id

        # Check if the substituting player is in an ongoing game
        if get_ongoing_game_of_player(substitute_id):
            embed = discord.Embed(description="You are currently in an ongoing game and cannot substitute for another player.", color=0xff0000)
            await ctx.send(embed=embed)
            return

        # Get the user_id of the player to be substituted
        player_to_be_substituted_id = get_user_id_from_input(ctx, user_input)
        if not player_to_be_substituted_id:
            embed = discord.Embed(description="Invalid player mentioned or named.", color=0xff0000)
            await ctx.send(embed=embed)
            return

        # Check if the player to be substituted is in an ongoing game
        ongoing_game_id = get_ongoing_game_of_player(player_to_be_substituted_id)
        if not ongoing_game_id:
            embed = discord.Embed(description=f"The player {user_input} is not in an ongoing game.", color=0xff0000)
            await ctx.send(embed=embed)
            return

        # Load the ongoing games
        ongoing_games = load_from_bson(os.path.join(CACHE_DIR, 'ongoing_games.bson'))
        game = ongoing_games[ongoing_game_id]

        # Substitute the player
        for idx, player in enumerate(game["members"]):
            if player["id"] == player_to_be_substituted_id:
                game["members"][idx]["id"] = substitute_id
                game["members"][idx]["name"] = player_name_mapping.get(substitute_id, ctx.author.display_name)

        # If the substituted player was a captain, make the new player a captain
        if player_to_be_substituted_id in game["captains"]:
            game["captains"].remove(player_to_be_substituted_id)
            game["captains"].append(substitute_id)

        # Save the modified ongoing games back to the file
        save_to_bson(ongoing_games, os.path.join(CACHE_DIR, 'ongoing_games.bson'))


        # Remove the substituting player from all queues
        remove_player_from_all_queues(substitute_id)

        # Send an embed notification
        embed = discord.Embed(description=f"{ctx.author.display_name} has substituted for {user_input} in the ongoing game.", colour=Colour.green())
        await ctx.send(embed=embed)

        # Fetch player ratings and assign a default rating if not available
        data = fetch_data(datetime(2018, 1, 1), datetime.now(), 'NA')
        default_rating = trueskill.Rating(mu=15, sigma=5)
        player_ratings, _, _, _ = calculate_ratings(data, queue='NA')
        game = ongoing_games[ongoing_game_id]

        # Build a list similar to the `players` list in start_game
        players = [
            {
                'id': player["id"],
                'name': player_name_mapping.get(player["id"], ctx.bot.get_user(player["id"]).name if ctx.bot.get_user(player["id"]) else str(player["id"])),
                'mu': player_ratings.get(player["id"], default_rating).mu
            } for player in game["members"]
        ]

        await send_balanced_teams(ctx.bot, ctx.bot.get_channel(game["channel_id"]), players, player_ratings, game["captains"], data)
        

async def forcefully_end_all_games(ctx, ongoing_games):
    for game_id, game in list(ongoing_games.items()):  
        if game["channel_id"] == ctx.channel.id:
            del ongoing_games[game_id]
    
    save_to_bson(ongoing_games, os.path.join(CACHE_DIR, 'ongoing_games.bson'))
        
    embed = discord.Embed(
        title="",
        description="All ongoing games in this channel have been forcefully ended by an admin.",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

def store_completed_game(game_id, game):
    completed_game_id = f"{game_id}-{int(game['timestamp'] * 1000)}"
    game["version"] = CURRENT_SAVE_VERSION  

    completed_games = load_from_bson(os.path.join(DATA_DIR, 'completed_games.bson'))
    if not isinstance(completed_games, dict):
        completed_games = {}
    
    completed_games[completed_game_id] = game

    save_to_bson(completed_games, os.path.join(DATA_DIR, 'completed_games.bson'))



def generate_game_end_description(game, minutes, seconds):
    descriptions = [
            f"The game in `{{queue_name}}` took {minutes} minutes and {seconds} seconds. Another one for the history books, wouldn't you say?",
            f"And there we have it! `{{queue_name}}` wrapped up in {minutes} minutes and {seconds} seconds. Splendid game, everyone!",
            f"Ah, `{{queue_name}}` has come to an end after {minutes} minutes. Quite the quick match, right?",
            f"Bravo! `{{queue_name}}` concluded in just {minutes} minutes. Pip pip, cheerio to all the players!",
            f"That was a smashing {minutes}-minute bout in `{{queue_name}}`. Top-notch gaming, chaps!",
            f"The curtains have drawn on `{{queue_name}}` after {minutes} thrilling minutes. It's been an absolute pleasure, guv'nor!",
            f"That's a wrap for `{{queue_name}}`! Blimey, that was a quick {minutes} minutes!",
            f"Cracking game in `{{queue_name}}` that lasted {minutes} minutes! Hats off to all you fine players!",
            f"By George, `{{queue_name}}` has concluded after {minutes} minutes! Time for a spot of tea, perhaps?",
            f"Another day, another `{{queue_name}}` game wrapped up in {minutes} minutes. Marvellous, I must say!",
            f"Who would've thought `{{queue_name}}` would conclude in just {minutes} minutes? Remarkable!",
            f"And just like that, `{{queue_name}}` is done in {minutes} minutes. How time flies when you're having fun!",
            f"A round of applause for the players of `{{queue_name}}`! Concluded in a mere {minutes} minutes!",
            f"Jolly good show in `{{queue_name}}`! Wrapped up neatly in {minutes} minutes!"
        ]
    chosen_description = random.choice(descriptions)
    return chosen_description.format(**game)

async def end_individual_game(ctx, game_id, game):
    game["completion_timestamp"] = time.time()
    duration = int(game["completion_timestamp"] - game["timestamp"])
    minutes = duration // 60
    seconds = duration % 60

    # Store the game as completed
    store_completed_game(game_id, game)

    # Create and send embed
    embed_description = generate_game_end_description(game, minutes, seconds)
    embed = discord.Embed(
        title="",
        description=embed_description,
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

def get_ongoing_game_of_player(player_id):
    """Check if a player is in an ongoing game and return the game_id if found."""
    # Path to the ongoing games file
    file_path = os.path.join(CACHE_DIR, 'ongoing_games.bson')
    
    # If the ongoing games file doesn't exist, return None
    if not os.path.exists(file_path):
        return None

    ongoing_games = load_from_bson(file_path)

    for game_id, game in ongoing_games.items():
        for player in game["members"]:
            if player_id == player["id"]:
                return game_id

    return None


def get_user_id_from_input(ctx, user_input):
    # Check if the input is a mention
    if user_input.startswith('<@') and user_input.endswith('>'):
        user_id = user_input.strip('<@!>')
        return int(user_id)

    # If the input is not a mention, check the player_name_mapping
    reverse_mapping = {v: k for k, v in player_name_mapping.items()}
    if user_input in reverse_mapping:
        return reverse_mapping[user_input]

    # If not in the mapping, check the server members by name/nickname
    member = discord.utils.find(lambda m: m.name == user_input or m.display_name == user_input, ctx.guild.members)
    return member.id if member else None



async def start_game(bot, guild_id, channel_id, queue_name, members):
    data = fetch_data(datetime(2018, 1, 1), datetime.now(), 'NA')
    # Fetch player ratings and assign a default rating if not available
    default_rating = trueskill.Rating(mu=15, sigma=5)
    player_ratings, _, _, _ = calculate_ratings(data, queue='NA')
    
    players = [
        {
            'id': member[0],
            'name': player_name_mapping.get(member[0], bot.get_user(member[0]).name if bot.get_user(member[0]) else str(member[0])),
            'mu': player_ratings.get(member[0], default_rating).mu
        } for member in members
    ]

    # Randomly select 2 captains and extract their 'id' values to match the expected format
    captain_dicts = random.sample(players, 2)
    captains = [cap['id'] for cap in captain_dicts]

    channel = bot.get_channel(channel_id)

    # Create the new embed
    game_started_embed = discord.Embed(
        title="Game Started",
        description=f"Queue: `{queue_name}`",
        color=discord.Color.green()
    )
    await channel.send(embed=game_started_embed)

    # Create an embed for direct messages
    dm_embed = discord.Embed(
        title="Game Started!",
        description=f"The game has started in queue `{queue_name}`. All game info should be in the PUGs channel.",
        color=discord.Color.green()
    )
    dm_embed.add_field(name="Captains", value=", ".join([player_name_mapping.get(captain, bot.get_user(captain).name) for captain in captains]), inline=True)
    
    # Exclude captains from players list
    non_captain_players = [player["name"] for player in players if player["id"] not in captains]
    dm_embed.add_field(name="Players", value=", ".join(non_captain_players), inline=True)

    # Send the embed to all players in the game
    for player in players:
        try:
            user = bot.get_user(player["id"])
            if user:
                await user.send(embed=dm_embed)
        except Exception as e:
            print(f"Failed to send DM to {player['name']}. Error: {e}")

    # Store the game to ongoing_games.bson
    game_id = f"{guild_id}-{channel_id}-{queue_name}-{int(time.time())}"
    ongoing_game = {
        "members": [{"id": player["id"], "name": player["name"]} for player in players],
        "captains": captains,
        "timestamp": time.time(),
        "queue_name": queue_name,
        "guild_id": guild_id,
        "channel_id": channel_id
    }

    ongoing_games_path = os.path.join(CACHE_DIR, 'ongoing_games.bson')

    # If the file doesn't exist, create an empty BSON file
    if not os.path.exists(ongoing_games_path):
        save_to_bson({}, ongoing_games_path)

    # Load the ongoing games
    ongoing_games = load_from_bson(ongoing_games_path)
    ongoing_games[game_id] = ongoing_game

    # Save the modified ongoing games back to the file
    save_to_bson(ongoing_games, ongoing_games_path)

        

    # Remove all members from all queues
    for member in [m["id"] for m in ongoing_game["members"]]:
        remove_player_from_all_queues(member)

    await send_balanced_teams(bot, channel, players, player_ratings, captains, data)


        

def add_player_to_queue(guild_id, channel_id, queue_name, player_id, bot):
    global QUEUE_LOG
    # Load the current queues
    current_queues = get_current_queues()

    if is_player_in_ongoing_game(player_id):
        return "You are currently in an ongoing game and cannot join a new queue. Captains can use `!end` to finish the game"

    # Check if the server and channel are valid
    if str(guild_id) not in current_queues:
        return "Invalid server."
    if str(channel_id) not in current_queues[str(guild_id)]:
        return "Invalid channel."
    if queue_name not in current_queues[str(guild_id)][str(channel_id)]:
        return "Invalid queue."

    queue = current_queues[str(guild_id)][str(channel_id)][queue_name]
    if any(member[0] == player_id for member in queue["members"]):
        return "Player already in the queue."

    if len(queue["members"]) >= queue["size"]:
        return "Queue is full."

    # When adding a player
    current_timestamp = datetime.utcnow().timestamp()
    queue["members"].append((player_id, current_timestamp))

    # If the queue is full, determine if it should start immediately
    full_queues = [q for q, q_info in current_queues[str(guild_id)][str(channel_id)].items() if len(q_info["members"]) == q_info["size"]]
    full_queues.sort(key=lambda q: (-current_queues[str(guild_id)][str(channel_id)][q]["size"], q))

    if full_queues and queue_name == full_queues[0]:  # This queue has the highest priority
        asyncio.create_task(start_game(bot, guild_id, channel_id, queue_name, queue["members"]))
    elif full_queues:
        queue["members"].remove((player_id, current_timestamp))

    # Save the updated queues to the BSON file
    save_to_bson(current_queues, os.path.join(CACHE_DIR, 'queues.bson'))


    # Update the log after adding the player
    player_name = player_name_mapping.get(int(player_id))
    key = (guild_id, channel_id)
    QUEUE_LOG.setdefault(key, []).append((player_name, "added", queue_name))
    # Limit log size to 10 entries
    QUEUE_LOG[key] = QUEUE_LOG[key][-10:]
    
    return f"Added to `{queue_name}`."




def remove_player_from_queue(guild_id, channel_id, queue_name, player_id, reason=None):
    global QUEUE_LOG
    # Load the current queues
    current_queues = get_current_queues()

    # Check if the server and channel are valid
    if str(guild_id) not in current_queues:
        return "Invalid server."
    if str(channel_id) not in current_queues[str(guild_id)]:
        return "Invalid channel."
    if queue_name not in current_queues[str(guild_id)][str(channel_id)]:
        return "Invalid queue."

    queue = current_queues[str(guild_id)][str(channel_id)][queue_name]
    player_entry = next((entry for entry in queue["members"] if entry[0] == player_id), None)
    if not player_entry:
        return "Player not in the queue."
    queue["members"].remove(player_entry)

    # Save the updated queues to the BSON file
    save_to_bson(current_queues, os.path.join(CACHE_DIR, 'queues.bson'))


    # Update the log after removing the player
    player_name = player_name_mapping.get(int(player_id))
    key = (int(guild_id), int(channel_id))  # Use integers for the key
    action = "removed-afk" if reason == "afk" else "removed"
    


    # Append to the existing log or create a new one
    QUEUE_LOG[key] = QUEUE_LOG.get(key, [])
    QUEUE_LOG[key].append((player_name, action, queue_name))
    
    # Limit log size to 10 entries
    QUEUE_LOG[key] = QUEUE_LOG[key][-10:]
    

    return f"Removed from `{queue_name}`."

def generate_queue_embed(guild_id, channel_id, bot):
    current_queues = get_current_queues()
    channel_queues = current_queues.get(str(guild_id), {}).get(str(channel_id), {})

    # Check if ongoing games file exists
    file_path = os.path.join(CACHE_DIR, 'ongoing_games.bson')
    ongoing_games = load_from_bson(file_path)


    # Sort the queues based on their size, largest first
    sorted_queues = sorted(channel_queues.items(), key=lambda x: x[1]['size'], reverse=True)

    embed = discord.Embed(title="Game Queues", description=f"", colour=Colour.green())

    for queue_name, queue_info in sorted_queues:
        players_count = len(queue_info.get("members", []))
        player_names = [player_name_mapping.get(int(player_entry[0]), bot.get_guild(guild_id).get_member(int(player_entry[0])).display_name) if int(player_entry[0]) in player_name_mapping else None for player_entry in queue_info.get("members", [])]
        player_names = [name for name in player_names if name is not None]  # Remove any None values
        player_names_str = ", ".join(player_names) if player_names else ""
        embed.add_field(name=f"{queue_name} [{players_count}/{queue_info['size']}]", value=f"Players: {player_names_str}", inline=False)

    relevant_ongoing_games = {k: v for k, v in ongoing_games.items() if k.split('-')[1] == str(channel_id)}
    for _, game in relevant_ongoing_games.items():
        captain_names = []
        player_names = []
        for player in game['members']:
            name = player['name']
            if player['id'] in game['captains']:
                captain_names.append(f"**(C) {name}**")
            else:
                player_names.append(name)

        # Combine captain names and player names
        combined_names_str = ", ".join(captain_names + player_names)

        # Calculate the time since the game started using time.time()
        now = time.time()
        minutes_ago = int((now - game["timestamp"]) // 60)

        embed.add_field(name=f"Ongoing: {game['queue_name']} ({minutes_ago} minutes ago)", value=combined_names_str, inline=False)

    return embed




def is_player_in_ongoing_game(player_id):
    """Check if a player is in an ongoing game."""
    # Path to the ongoing games file
    file_path = os.path.join(CACHE_DIR, 'ongoing_games.bson')
    
    # Load ongoing games, will be an empty dictionary if the file doesn't exist
    ongoing_games = load_from_bson(file_path)
    
    if not ongoing_games:
        return False  # Player can't be in an ongoing game if no games are present

    # Check the player's presence in the ongoing games
    for game in ongoing_games.values():
        if any(member["id"] == player_id for member in game["members"]):  # Check for player_id in member dictionaries
            return True

    return False


def remove_player_from_all_queues(player_id):
    current_queues = get_current_queues()
    for guild_id, channels in current_queues.items():
        for channel_id, queues in channels.items():
            for queue_name, queue_data in queues.items():
                player_entry = next((entry for entry in queue_data["members"] if entry[0] == player_id), None)
                if player_entry:
                    queue_data["members"].remove(player_entry)
    save_to_bson(current_queues, os.path.join(CACHE_DIR, 'queues.bson'))

def get_current_queues():
    return load_from_bson(os.path.join(CACHE_DIR, 'queues.bson'))

def get_current_pug_channels():
    return load_from_bson(os.path.join(DATA_DIR, 'pug_channel.bson'))

def save_afk_time(guild_id, channel_id, minutes):
    afk_times = load_from_bson(AFK_TIMES_FILE)
    
    if str(guild_id) not in afk_times:
        afk_times[str(guild_id)] = {}
    afk_times[str(guild_id)][str(channel_id)] = minutes
    
    save_to_bson(afk_times, AFK_TIMES_FILE)

def get_afk_time(guild_id, channel_id):
    afk_times = load_from_bson(AFK_TIMES_FILE)
    return afk_times.get(str(guild_id), {}).get(str(channel_id), AFK_TIME_LIMIT_MINUTES)


def save_to_bson(data, filepath):
    """Save the data to a BSON file."""
    with open(filepath, 'wb') as f:
        f.write(bson.BSON.encode(data))

def load_from_bson(filepath):
    """Load data from a BSON file. If the file doesn't exist, return an empty dictionary."""
    if not os.path.exists(filepath):
        return {}
    with open(filepath, 'rb') as f:
        return bson.BSON(f.read()).decode()