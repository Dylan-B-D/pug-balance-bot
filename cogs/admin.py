import asyncio
import discord
import pytz
from discord import Member
from discord.ext import commands
from datetime import datetime, timedelta
from modules.utilities import check_bot_admin
from data.capper_data import capper_value_mapping
from data.player_mappings import player_name_mapping


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def clear(self, ctx):
        utc = pytz.UTC
        current_time = datetime.utcnow().replace(tzinfo=utc)  # timezone-aware datetime with UTC timezone
        minutes_ago = current_time - timedelta(minutes=5)

        # Only allow the user with the specific ID to use this command
        if ctx.author.id != 252190261734670336:
            await ctx.send("You do not have permission to use this command.")
            return
        
        # Delete bot's messages from the last 10 minutes
        async for message in ctx.channel.history(limit=100):  # Adjust limit if necessary
            if message.author == self.bot.user and message.created_at > minutes_ago:
                await message.delete()
                await asyncio.sleep(0.7)  # Delay of 700ms between deletions

        confirmation = await ctx.send("Bot messages from the last 10 minutes have been deleted.")
        await asyncio.sleep(5)  # Let the confirmation message stay for 5 seconds
        await confirmation.delete()

    @commands.command()
    async def getcapvalues(self, ctx):
        """
        Fetches and displays the capper values for the players.
        """
        if not await check_bot_admin(ctx):
            return
        
        embed = discord.Embed(title="Capper Values", color=discord.Color.blue())
        
        # Check if capper_data is empty
        if not capper_value_mapping:
            embed.description = "No capper values set."
        else:
            for user_id, value in capper_value_mapping.items():
                player_name = player_name_mapping.get(user_id, str(user_id))  # Use the ID as a fallback if the name isn't found
                embed.add_field(name=player_name, value=f"Capper Value: {value}", inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    async def setcapvalue(self, ctx, user: discord.User = None, value: float = None):
        """
        Sets the capper value for a specified user.
        Syntax: !setcapvalue @User [value]
        """
        if not await check_bot_admin(ctx):
            return
        
        # If no user or value is provided, or if the value is out of range
        if user is None or value is None or not 0 <= value <= 1:
            embed = discord.Embed(color=discord.Color.red(), description=f"**Incorrect syntax!**\nUsage: `!setcapvalue @User [value between 0 and 1]`")
            await ctx.send(embed=embed)
            return

        # Open the capper_data.py file to read its current contents
        with open("data/capper_data.py", "r") as file:
            lines = file.readlines()

        # Modify or add the new value for the user
        user_line_index = None
        # Fetch the player's name from the player_name_mapping or use the discord name as a fallback
        player_name = player_name_mapping.get(user.id, user.display_name)
        user_line_content = f"    {user.id}: {value},  # {player_name}\n"
        for i, line in enumerate(lines):
            if str(user.id) in line:
                user_line_index = i
                break

        action = "set"
        if user_line_index is not None:
            lines[user_line_index] = user_line_content
            action = "updated"
        else:
            # Assuming that the dictionary ends with a closing brace on the last line
            lines.insert(-1, user_line_content)

        # Write the modified content back to capper_data.py
        with open("data/capper_data.py", "w") as file:
            file.writelines(lines)

        # Notify the user of the successful update
        embed = discord.Embed(color=discord.Color.green(), description=f"Capper value for **{player_name}** has been {action} to **{value}**.")
        await ctx.send(embed=embed)

    
    @commands.command()
    async def setname(self, ctx, member: Member, *, new_name: str):

        if not await check_bot_admin(ctx):
            return


        try:
            # Update the in-memory mapping
            player_name_mapping[member.id] = new_name
            
            # Write the changes to the file
            with open('data/player_mappings.py', 'w', encoding='utf-8') as file:
                file.write("player_name_mapping = {\n")
                for id, name in player_name_mapping.items():
                    file.write(f"    {id}: \"{name}\",\n")
                file.write("}\n")

            # Send a confirmation using an embed
            embed = discord.Embed(title="Name Updated", color=0x00ff00) # Green color for success
            embed.description = f"Name for `{member.display_name}` has been set to `{new_name}`."
            await ctx.send(embed=embed)
            
        except Exception as e:
            # Send an error using an embed
            embed = discord.Embed(title="Error Setting Name", description=f"`{e}`", color=0xff0000) # Red color for error
            await ctx.send(embed=embed)
