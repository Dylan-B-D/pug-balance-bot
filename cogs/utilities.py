import discord
import configparser
from discord.ext import commands
from discord import Embed
from modules.charts import create_map_weights_chart
from data.map_weights import map_weights
from data.user_roles import bot_admins
from data.player_mappings import player_name_mapping

# Initialize the configparser and read the config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# Fetch constants
MIN_REACTIONS = int(config['Constants']['MIN_REACTIONS'])
MIN_REACTIONS_MAP = int(config['Constants']['MIN_REACTIONS_MAP'])


class UtilitiesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def help(self, ctx):
        # Organized commands by categories
        commands_dict = {
            "Restricted": {
                "!clear": "Clear the bot's messages from the last 10 mins.",
                "!match": "Simulate a game starting.",
                "!ranks": "Show player ranks.",
                "!startgame": "Simulate when teams have been picked."
            },
            "Elevated Admin": {
                "!kill": "Shutdown docker containers. Usage: `!kill [container]`"
            },
            "Admin": {
                "!debug": "Debug ID matching results for previous game.",
                "!getcapvalues": "Shows a list of players cap values, where 1 is always cap, and 0 is never cap.",
                "!setmap": "Set the PUG/2v2 map. Returns list of map IDs if no parameter given. Usage: `!setmap [map_id]`",
                "!setcapvalue": "Sets a users cap value. Usage: setcapvalue [@User][capvalue]"
            },
            "Others": {
                "!gamelengths": "Shows a graph of game lengths over time.",
                "!gamehistory": "Shows the last few maps played (Max 10). Usage: `!gamehistory [optional_history_count]`",
                "!help": "Shows a list of commands and how to use them.",
                "!info": "Shows map weighting for generated games, and voting thresholds.",
                "!listadmins": "Lists all the admins.",
                "!servers": "Shows server list for 'PUG Login'.",
                "!serverstats": "Gets some simple stats for the PUG queues.",
                "!setname": "Sets the player's username which is used to display stats/balanced teams etc. Usage: `!setname [@User][new_username]`",
                "!showstats": "Shows some basic stats for a given player. Usage: `!showstats [@User]`",
                "!status": "Shows server status for PUGs with no param, or shows status for Mixers with a param of '2'. Usage: `!status [optional_server_index]`"
            }
        }

        for category, commands in commands_dict.items():
            embed = Embed(title=f"TA-Bot Commands - {category}", description="", color=0x00ff00)
            
            for cmd, desc in commands.items():
                embed.add_field(name=cmd, value=desc, inline=False)

            await ctx.send(embed=embed)

    @commands.command(name='info')
    async def info(self, ctx):
        filename = create_map_weights_chart(map_weights)

        # Create a discord.File object
        file = discord.File(filename, filename="map_weights_chart.png")
        
        embed = discord.Embed(title="Bot Information", description="Details about bot settings and configurations.", color=discord.Colour.blue())
        
        # Adding bot configuration info
        embed.add_field(name="Votes Required to Rebalance Teams", value=str(MIN_REACTIONS), inline=False)
        embed.add_field(name="Votes Required to Skip Map", value=str(MIN_REACTIONS_MAP), inline=False)
        
        # Set the image url to reference the attached file
        embed.set_image(url="attachment://map_weights_chart.png")
        
        # Send the embed and the file
        await ctx.send(file=file, embed=embed)

    @commands.command()
    async def listadmins(self, ctx):

        embed = Embed(title="Bot Admins", description="List of users with admin for TA-Bot:", color=0x00ff00)

        admin_names = sorted([player_name_mapping.get(admin_id, str(admin_id)) for admin_id in bot_admins])
        
        num_cols = 3  # Number of columns
        num_rows = -(-len(admin_names) // num_cols)  
        
        for col in range(num_cols):
            field_value = []  
            for row in range(num_rows):
                idx = col * num_rows + row  
                if idx < len(admin_names):
                    field_value.append(admin_names[idx])
            
            field_name = "Admins" if col == 0 else '\u200b' 
            embed.add_field(name=field_name, value="\n".join(field_value), inline=True)
        
        await ctx.send(embed=embed)