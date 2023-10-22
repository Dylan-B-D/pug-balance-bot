import discord
import os
import json
import asyncio
import time
from datetime import datetime, timedelta
from discord.ext import commands
import random
from discord.ext import tasks
from discord.ui import Button, View
from modules.utilities import check_bot_admin
from data.player_mappings import player_name_mapping

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))  

# Create a 'data' directory if it doesn't exist
CACHE_DIR = os.path.join(BASE_DIR, 'cache')
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# Create a 'data' directory if it doesn't exist
DATA_DIR = os.path.join(BASE_DIR, 'data')
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

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

QUEUE_LOG = {}

AFK_TIME_LIMIT_MINUTES = 0.2

class QueueButton(discord.ui.Button):
    def __init__(self, label, queue_view, style=discord.ButtonStyle.primary):
        super().__init__(label=label, style=style)
        self.queue_view = queue_view

    async def callback(self, interaction: discord.Interaction):
            queue_name = self.label
            response = add_player_to_queue(self.queue_view.guild_id, self.queue_view.channel_id, queue_name, interaction.user.id)

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
        response = add_player_to_queue(self.guild_id, self.channel_id, queue_name, interaction.user.id)

        if response == "Player added to queue.":
            updated_embed = self.generate_updated_embed()
            await interaction.message.edit(embed=updated_embed)
        else:
            await interaction.response.send_message(response, ephemeral=True)

    def generate_updated_embed(self):
        current_queues = get_current_queues()
        channel_queues = current_queues.get(str(self.guild_id), {}).get(str(self.channel_id), {})

        # Sort the queues based on their size, largest first
        sorted_channel_queues = sorted(channel_queues.items(), key=lambda x: x[1]['size'], reverse=True)

        embed = discord.Embed(title="Game Queues", description=f"", color=0x00ff00)

        for queue_name, queue_info in sorted_channel_queues:
            players_count = len(queue_info.get("members", []))

            # Fetch players' names from player_name_mapping or use their server nickname
            player_names = [player_name_mapping.get(int(player_entry[0]), self.bot.get_guild(self.guild_id).get_member(int(player_entry[0])).display_name) if int(player_entry[0]) in player_name_mapping else None for player_entry in queue_info.get("members", [])]
            player_names = [name for name in player_names if name is not None]  # Remove any None values
            player_names_str = ", ".join(player_names) if player_names else ""

            embed.add_field(name=f"{queue_name} [{players_count}/{queue_info['size']}]", value=f"Players: {player_names_str}", inline=False)

        return embed




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

        print(f"Interaction received from {interaction.user.name}. Checking for update...")

        guild_id = interaction.guild.id
        current_queues = get_current_queues()
        player_updated = False

        for channel_id, queues in current_queues.get(str(guild_id), {}).items():
            for queue_name, queue_data in queues.items():
                for idx, (player_id, timestamp) in enumerate(queue_data["members"]):
                    if player_id == interaction.user.id:
                        queue_data["members"][idx] = (player_id, datetime.utcnow().timestamp())
                        player_updated = True
                        print(f"Updated timestamp for {interaction.user.name} in queue {queue_name} due to interaction.")
                        break

        # Save the updated queues to the JSON file
        if player_updated:  # Only save if we made an update
            with open(os.path.join(DATA_DIR, 'queues.json'), 'w') as f:
                json.dump(current_queues, f)
        else:
            print(f"Did not find {interaction.user.name} in any queue for update due to interaction.")


    @tasks.loop(minutes=0.1)
    async def check_stale_users(self):
        current_queues = get_current_queues()
        now = datetime.utcnow().timestamp()

        removals = {}  # Dictionary to track which users are removed from which queues

        for guild_id, channels in current_queues.items():
            for channel_id, queues in channels.items():
                for queue_name, queue_data in queues.items():
                    for player_id, timestamp in list(queue_data["members"]):  # Use list to avoid runtime errors due to size changes
                        time_difference = now - timestamp
                        print(f"Checking {player_name_mapping.get(int(player_id))}. Time since last message: {time_difference} seconds.")
                        if time_difference > AFK_TIME_LIMIT_MINUTES*60:
                            response = remove_player_from_queue(guild_id, channel_id, queue_name, player_id, reason="afk")
                            if response:
                                removals.setdefault((player_id, channel_id), []).append(queue_name)

        for (player_id, channel_id), removed_queues in removals.items():
            user = self.bot.get_user(int(player_id))
            
            # Send an embed PM to the user
            queues_str = ', '.join(f"`{queue_name}`" for queue_name in removed_queues)
            embed_pm = discord.Embed(description=f"You were removed from {queues_str} due to being AFK for more than {AFK_TIME_LIMIT_MINUTES} minutes.", color=0xff0000)
            await user.send(embed=embed_pm)
            
            print(f"Removed {player_name_mapping.get(int(player_id))} from {queues_str}.")
            
            channel = self.bot.get_channel(int(channel_id))
            # Send an embed indicating the user was removed due to being AFK
            embed_msg = discord.Embed(description=f"{player_name_mapping.get(int(player_id))} was removed from {queues_str} for being AFK for more than {AFK_TIME_LIMIT_MINUTES} minutes.", color=0xff0000)
            await channel.send(embed=embed_msg)


    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        print(f"Message received from {message.author.name}. Checking for update...")

        guild_id = message.guild.id
        current_queues = get_current_queues()
        player_updated = False

        for channel_id, queues in current_queues.get(str(guild_id), {}).items():
            for queue_name, queue_data in queues.items():
                for idx, (player_id, timestamp) in enumerate(queue_data["members"]):
                    if player_id == message.author.id:
                        queue_data["members"][idx] = (player_id, datetime.utcnow().timestamp())
                        player_updated = True
                        print(f"Updated timestamp for {message.author.name} in queue {queue_name}.")
                        break

        # Save the updated queues to the JSON file
        if player_updated:  # Only save if we made an update
            with open(os.path.join(DATA_DIR, 'queues.json'), 'w') as f:
                json.dump(current_queues, f)
        else:
            print(f"Did not find {message.author.name} in any queue for update.")

    @commands.command()
    async def menu(self, ctx):
        current_channels = get_current_pug_channels()
        current_queues = get_current_queues()

        # Check if the command is being executed in a pug channel
        if str(ctx.guild.id) not in current_channels or str(ctx.channel.id) != current_channels[str(ctx.guild.id)]:
            return

        # Fetch the queues for the pug channel and sort them based on size, largest first
        channel_queues = current_queues.get(str(ctx.guild.id), {}).get(str(ctx.channel.id), {})
        sorted_queues = sorted(channel_queues.items(), key=lambda x: x[1]['size'], reverse=True)

        embed = discord.Embed(title="Game Queues", description=f"", color=0x00ff00)

        for queue_name, queue_info in sorted_queues:
            players_count = len(queue_info.get("members", []))

            # Fetch players' names from player_name_mapping or use their server nickname
            player_names = [player_name_mapping.get(int(player_entry[0]), self.bot.get_guild(ctx.guild.id).get_member(int(player_entry[0])).display_name) if int(player_entry[0]) in player_name_mapping else None for player_entry in queue_info.get("members", [])]
            player_names = [name for name in player_names if name is not None]  # Remove any None values
            player_names_str = ", ".join(player_names) if player_names else ""

            embed.add_field(name=f"{queue_name} [{players_count}/{queue_info['size']}]", value=f"Players: {player_names_str}", inline=False)

        view = QueueView(channel_queues, ctx.guild.id, ctx.channel.id, ctx.author, self.bot)
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

        # Save the server and channel ID to a JSON file
        data = {
            "server_id": ctx.guild.id,
            "channel_id": ctx.channel.id
        }
        current_channels[str(ctx.guild.id)] = str(ctx.channel.id)
        with open(os.path.join(DATA_DIR, 'pug_channel.json'), 'w') as f:
            json.dump(current_channels, f)



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

        # Remove the channel from the JSON file
        del current_channels[str(ctx.guild.id)]
        with open(os.path.join(DATA_DIR, 'pug_channel.json'), 'w') as f:
            json.dump(current_channels, f)

    @commands.command()
    async def createqueue(self, ctx, queuename: str, queuesize: int):

        if not await check_bot_admin(ctx):
            return

        current_channels = get_current_pug_channels()

        # Check if the command is being executed in a pug channel
        if str(ctx.guild.id) not in current_channels or str(ctx.channel.id) != current_channels[str(ctx.guild.id)]:
            embed = discord.Embed(title="", 
                                description="This command can only be executed in a pug channel.", 
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
        with open(os.path.join(DATA_DIR, 'queues.json'), 'w') as f:
            json.dump(current_queues, f)

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
            await ctx.send("This command can only be executed in a pug channel.")
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
        with open(os.path.join(DATA_DIR, 'queues.json'), 'w') as f:
            json.dump(current_queues, f)

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

def add_player_to_queue(guild_id, channel_id, queue_name, player_id):
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
    if any(member[0] == player_id for member in queue["members"]):
        return "Player already in the queue."

    if len(queue["members"]) >= queue["size"]:
        return "Queue is full."

    # When adding a player
    current_timestamp = datetime.utcnow().timestamp()
    queue["members"].append((player_id, current_timestamp))


    # Save the updated queues to the JSON file
    with open(os.path.join(DATA_DIR, 'queues.json'), 'w') as f:
        json.dump(current_queues, f)

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

    # Save the updated queues to the JSON file
    with open(os.path.join(DATA_DIR, 'queues.json'), 'w') as f:
        json.dump(current_queues, f)

    # Update the log after removing the player
    player_name = player_name_mapping.get(int(player_id))
    key = (int(guild_id), int(channel_id))  # Use integers for the key
    action = "removed-afk" if reason == "afk" else "removed"
    
    # Print the log before the update
    print(f"Before update: {QUEUE_LOG}")

    # Append to the existing log or create a new one
    QUEUE_LOG[key] = QUEUE_LOG.get(key, [])
    QUEUE_LOG[key].append((player_name, action, queue_name))
    
    # Limit log size to 10 entries
    QUEUE_LOG[key] = QUEUE_LOG[key][-10:]
    
    # Print the log after the update
    print(f"After update: {QUEUE_LOG}")

    return f"Removed from `{queue_name}`."




def get_current_queues():
    try:
        with open(os.path.join(DATA_DIR, 'queues.json'), 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def get_current_pug_channels():
    try:
        with open(os.path.join(DATA_DIR, 'pug_channel.json'), 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    