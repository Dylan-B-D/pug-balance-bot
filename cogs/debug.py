import discord
import re
from discord.ext import commands
from discord import Embed
from modules.utilities import check_bot_admin
from modules.shared_data import (matched_results_store, substitution_store)

class DebugsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def startgame(self, ctx):
        allowed_user_id = 252190261734670336
        
        if ctx.author.id != allowed_user_id:
            embed = Embed(title="Permission Denied", description="This command is restricted.", color=0xff0000)
            await ctx.send(embed=embed)
            return
        
        """Sends an embed to manually trigger the start_map function with the map 'walledin'."""
        embed = Embed(title="Game Details", 
                    description="**Teams:**\nA Team: Player1, Player2, Player3\nB Team: Player4, Player5, Player6\n\n**Maps:** elysianbattlegrounds", 
                    color=0x00ff00)
        await ctx.send(embed=embed)

    @commands.command()
    async def match(self, ctx):
        # Specified user ID
        allowed_user_ids = [252190261734670336, 162786167765336064]
        
        if ctx.author.id not in allowed_user_ids:
            embed = Embed(title="Permission Denied", description="This command is restricted.", color=0xff0000)
            await ctx.send(embed=embed)
            return
        
        # Create the embed
        embed = discord.Embed(
            title="",
            description="**Captains: <@252190261734670336> & <@1146906869827391529>**\n"
                        "crodog5, dodg_, Greth, Giga, killjohnsonjr, TA-Bot, IHateDisc0rd, notsolly, imascrewup, Theobald the Bird, cjplayz_, gredwa",
            color=discord.Color.blue()
        )

        # Send both the content and embed in one message
        await ctx.send(content="`Game 'PUG' has begun`", embed=embed)


    @commands.command(name='debug')
    async def debug(self, ctx):
        # Check if the author of the command is in the list of admins
        if not await check_bot_admin(ctx):
            return


        channel_id = ctx.channel.id
        debug_embed = discord.Embed(title="Debug Information", color=discord.Color.blue())

        # Substitution Information
        embed_description = substitution_store.get(channel_id)
        if embed_description:
            player_names = re.findall(r'([^()]+) \((\d+)\) has been substituted with ([^()]+) \((\d+)\)', embed_description)
            if player_names:
                old_player_name, old_player_id, new_player_name, new_player_id = player_names[0]
                debug_embed.add_field(name="Substituted Player", value=f"Name: {old_player_name}\nID: {old_player_id}", inline=True)
                debug_embed.add_field(name="Replacement Player", value=f"Name: {new_player_name}\nID: {new_player_id}", inline=True)

        # Matched Results
        matched_results = matched_results_store.get(channel_id)
        if matched_results:
            matched_ids = matched_results.get('matched_ids', [])
            if matched_ids:
                matched_players = "\n".join([f"Name: {name.split(' (')[0]}\nID: {name.split('(')[1].rstrip(')')}" for name in matched_ids])
                debug_embed.add_field(name="Matched Players", value=matched_players, inline=False)

            unmatched_names = matched_results.get('unmatched_names', [])
            if unmatched_names:
                unmatched_players = "\n".join(unmatched_names)
                debug_embed.add_field(name="Unmatched Names", value=unmatched_players, inline=False)

            captains = matched_results.get('captains', [])
            if captains:
                captain_names = "\n".join([str(captain) for captain in captains])
                debug_embed.add_field(name="Captains", value=captain_names, inline=False)
        else:
            debug_embed.add_field(name="Matched Results", value="No matched results found for this channel.", inline=False)

        await ctx.send(embed=debug_embed)
