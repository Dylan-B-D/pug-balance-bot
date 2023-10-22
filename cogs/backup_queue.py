import discord
import os
import json
import time
from discord.ext import commands
import random
from discord.ext import tasks
from data.player_mappings import player_name_mapping

allowed_user_ids = [252190261734670336, 162786167765336064]

# Cache for fetched servers list
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))  # Go up one directory level

# Create a 'data' directory if it doesn't exist
CACHE_DIR = os.path.join(BASE_DIR, 'cache')
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

class QueueCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.cache_file_path = os.path.join(CACHE_DIR, 'queue_cache.json')
        self.queues = self.load_queue_from_cache()
        self.check_queues.start()
 

    queue_mapping = {
    "1": "PUG"
    }
    
    
    def load_queue_from_cache(self):
        """Load queue and last added times from the cache file if it exists, otherwise return default queue data."""
        default_queues = {
            "PUG": {"members": [], "size": 14}
        }

        default_last_added_times = {}

        if os.path.exists(self.cache_file_path):
            with open(self.cache_file_path, 'r') as f:
                try:
                    data = json.load(f)
                    self.user_last_added_time = data.get("user_last_added_time", default_last_added_times)
                    return data.get("queues", default_queues)
                except json.JSONDecodeError:
                    # If there's an error decoding the file, return the default queues and set default last added times
                    self.user_last_added_time = default_last_added_times
                    return default_queues
        else:
            self.user_last_added_time = default_last_added_times
            return default_queues


    def save_queue_to_cache(self):
        """Save the current queue data and last added times to the cache file."""
        with open(self.cache_file_path, 'w') as f:
            data = {
                "queues": self.queues,
                "user_last_added_time": self.user_last_added_time
            }
            json.dump(data, f)


    def _get_user_name(self, user_id):
        user = self.bot.get_user(user_id)
        return player_name_mapping.get(user_id, user.display_name if user else "Unknown User")



    @commands.command(name='status2')
    async def status(self, ctx):
        embed = discord.Embed(title="Queue Status", color=0x00ff00)
        for queue_name, queue_data in self.queues.items():
            if queue_data["members"]:
                player_names = [self._get_user_name(member["id"]) for member in queue_data["members"]]
                embed.add_field(name=f"{queue_name} ({len(queue_data['members'])}/{queue_data['size']})", value=", ".join(player_names), inline=False)
            else:
                embed.add_field(name=f"{queue_name} ({len(queue_data['members'])}/{queue_data['size']})", value="No players in the queue.", inline=False)
        await ctx.send(embed=embed)


    async def start_game(self, ctx, queue_name, members):
        # Load the current queues from the cache
        self.queues = self.load_queue_from_cache()

        captains = random.sample(members, 2)
        
        # Fetching the list of player names using their nicknames (or usernames if nicknames aren't set)
        players_list = ', '.join([f"{ctx.guild.get_member(member_id).nick or ctx.guild.get_member(member_id).name}" for member_id in members if member_id not in captains])

        # Notify all players via DM that the game has begun
        for member_id in members:
            member = ctx.guild.get_member(member_id)
            try:
                embed = discord.Embed(title="Good day!", 
                      description=f"Dear Sir/Madam {member.display_name},\n\nI hope this message finds you in good spirits. I am most delighted to inform you that the game '{queue_name}' is now underway. Would you be so kind as to direct your esteemed attention to the main channel for further particulars?\n\nWith utmost respect and anticipation, \nYour Trusty Bot Butler",
                      color=0x3498db)

                await member.send(embed=embed)
            except discord.Forbidden:  # This exception is raised if the bot can't send the member a DM
                pass

        # Remove all members of the current queue from all other queues
        for q_name, queue_data in self.queues.items():
            for member in members:
                if member in queue_data["members"]:
                    queue_data["members"].remove(member)

        embed = discord.Embed(
            title="",
            description=f"**Captains: <@{captains[0]}> & <@{captains[1]}>**\n{players_list}",
            color=discord.Color.blue()
        )

        await ctx.send(content=f"`Game '{queue_name}' has begun`", embed=embed)

        # Save the changes to the cache
        self.save_queue_to_cache()



    async def _get_member_from_name_or_mention(self, ctx, name_or_mention):
        if ctx.message.mentions:
            return ctx.message.mentions[0]
        else:
            for member in ctx.guild.members:
                if member.name == name_or_mention or member.display_name == name_or_mention:
                    return member
            return None

    async def _add_to_queues(self, user, ctx, qtype=None):
        # Limit users to add only in specific channels
        allowed_channel_ids = [1154371216946249740, 912341856048775198]  # You can add more channel IDs here if needed
        if ctx.channel.id not in allowed_channel_ids:
            await ctx.send("You cannot add to the queue in this channel.")
            return

        if qtype and qtype in self.queue_mapping:
            qtype = self.queue_mapping[qtype]

        message_parts = []
        already_in_queues = []

        for queue_name, queue_data in self.queues.items():
            if qtype and qtype != queue_name:
                continue

            # Check if user is not already in the queue
            if user.id not in [m["id"] for m in queue_data["members"]]:
                queue_data["members"].append({
                    "id": user.id, 
                    "joined_at": time.time(), 
                    "channel_id": ctx.channel.id,
                    "guild_id": ctx.guild.id
                })

                # Update the user's most recent addition time
                self.user_last_added_time[str(user.id)] = time.time()

                message_parts.append(f"{queue_name} ({len(queue_data['members'])}/{queue_data['size']})")

                # Check if queue is full and start game
                if len(queue_data["members"]) == queue_data["size"]:
                    await self.start_game(ctx, queue_name, [m["id"] for m in queue_data["members"]])
                    queue_data["members"] = []
            else:
                already_in_queues.append(queue_name)

        # Create response message
        response_message = ""
        if message_parts:
            response_message += f"`{user.display_name}` has been added to {', '.join(message_parts)}."
        if already_in_queues:
            response_message += f" `{user.display_name}` is already in {', '.join(already_in_queues)}."

        await ctx.send(embed=discord.Embed(description=response_message, color=0x00ff00))

        # Save the changes to the cache
        self.save_queue_to_cache()

    @commands.command(name='add2')
    async def add(self, ctx, qtype: str = None):
        await self._add_to_queues(ctx.author, ctx, qtype)

    @commands.command(name='addplayer')
    async def addplayer(self, ctx, name_or_mention: str, qtype: str = None):
        if ctx.author.id not in allowed_user_ids:
            await ctx.send("You do not have permission to use this command!")
            return
            
        member = await self._get_member_from_name_or_mention(ctx, name_or_mention)
            
        if not member:
            await ctx.send(f"Could not find member {name_or_mention}")
            return

        await self._add_to_queues(member, ctx, qtype)

    async def _remove_from_queues(self, user, ctx, qtype=None):
        if qtype and qtype in self.queue_mapping:
            qtype = self.queue_mapping[qtype]

        message_parts = []
        not_in_queues = []

        for queue_name, queue_data in self.queues.items():
            if qtype and qtype != queue_name:
                continue

            if user.id in [m["id"] for m in queue_data["members"]]:
                queue_data["members"] = [m for m in queue_data["members"] if m["id"] != user.id]
                message_parts.append(f"{queue_name} ({len(queue_data['members'])}/{queue_data['size']})")
            else:
                not_in_queues.append(queue_name)

        # Create response message
        response_message = ""
        if message_parts:
            response_message += f"`{user.display_name}` has been removed from {', '.join(message_parts)}."
        if not_in_queues:
            response_message += f" Note: `{user.display_name}` was not in {', '.join(not_in_queues)}."

        color = 0xff0000 if message_parts else 0xffff00
        await ctx.send(embed=discord.Embed(description=response_message, color=color))

        # Save the changes to the cache
        self.save_queue_to_cache()

    @commands.command(name='del2')
    async def remove_from_queue(self, ctx, qtype: str = None):
        await self._remove_from_queues(ctx.author, ctx, qtype)

    @commands.command(name='removeplayer')
    async def removeplayer(self, ctx, name_or_mention: str, qtype: str = None):
        if ctx.author.id not in allowed_user_ids:
            await ctx.send("You do not have permission to use this command!")
            return
            
        member = await self._get_member_from_name_or_mention(ctx, name_or_mention)
            
        if not member:
            await ctx.send(f"Could not find member {name_or_mention}")
            return

        await self._remove_from_queues(member, ctx, qtype)


    def is_member_offline(self, member):
        return member.status == discord.Status.offline

    @tasks.loop(minutes=5)
    async def check_queues(self):
        for guild in self.bot.guilds:
            for queue_name, queue_data in self.queues.items():
                members_to_remove = []  # Store members to remove after iterating through the queue
                messages_to_send = set()  # Store messages to send after iterating through the queue

                for member_data in queue_data["members"]:
                    member_id = member_data["id"]
                    member = guild.get_member(member_id)


                    # Use the most recent addition time to check for 180 minutes expiry
                    last_added_time = self.user_last_added_time.get(str(member_id), 0)
                    elapsed_time = time.time() - last_added_time

                    if elapsed_time > 180 * 60:
                        members_to_remove.append(member_data)
                        messages_to_send.add((f"{member.mention} You have been removed from all queues due to being AFK for over 180 minutes.", member_data["channel_id"], member_data["guild_id"]))
                        
                        # Adding the print statement here
                        print(f"Removed {member.name} due to AFK for {elapsed_time/60:.2f} minutes.")
                        continue


                    # Remove members who are offline and have been offline for over 20 minutes
                    # if self.is_member_offline(member) and elapsed_time > 20 * 60:
                        # members_to_remove.append(member_data)
                        # messages_to_send.add((f"{member.mention} You have been removed from the `{queue_name}` queue due to being offline for over 20 minutes.", member_data["channel_id"], member_data["guild_id"]))
                        # continue

                # Remove members from all queues
                for member_data in members_to_remove:
                    for queue in self.queues.values():
                        if member_data in queue["members"]:
                            queue["members"].remove(member_data)

                # Notify the user only once about the removal
                for msg, channel_id, guild_id in messages_to_send:
                    specific_guild = self.bot.get_guild(guild_id)
                    if specific_guild:
                        channel = specific_guild.get_channel(channel_id)
                        if channel:
                            if channel.permissions_for(specific_guild.me).send_messages:
                                await channel.send(embed=discord.Embed(description=msg, color=0xff0000))

            self.save_queue_to_cache()