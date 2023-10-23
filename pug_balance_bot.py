# Standard Libraries
import configparser
from datetime import datetime

# Third-party Libraries
import discord
import aiohttp
from discord.ext import commands

# Bot Data
from data.shared_data import (last_messages)

# Bot Modules
from modules.utilities import (check_substitution, 
                               check_2v2_game_start, 
                               check_map_start, 
                               check_pug_game_start)

# Bot Cogs
from cogs.utilities import UtilitiesCog
from cogs.debug import DebugsCog
from cogs.admin import AdminCog
from cogs.server_management import ServerManagementCog
from cogs.stats import StatsCog
from cogs.ta_network import TANetworkCog
from cogs.cache_queues import QueueCacheCog
from cogs.backup_queue import QueueCog
from cogs.pug_queue import PugQueueCog


# ==============================
# CONFIGURATION
# ==============================

# Configuration File Parsing
config = configparser.ConfigParser()
try:
    config.read('config.ini')
    TOKEN = config['Bot']['Token']
except (configparser.Error, KeyError) as e:
    raise SystemExit("Error reading configuration file.") from e

# Bot Command Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Bot initialization
bot = commands.Bot(command_prefix='!', intents=intents)
bot.remove_command('help')


# ==============================
# BOT EVENTS
# ==============================

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

    # Update bot's avatar
    async with aiohttp.ClientSession() as session:
        async with session.get('https://i.ibb.co/54cQcDj/eclipse2.jpg') as response:
            if response.status == 200:
                data = await response.read()
                await bot.user.edit(avatar=data)
            else:
                print("Failed to fetch the avatar image.")

    # Add cogs to bot
    await bot.add_cog(UtilitiesCog(bot))
    await bot.add_cog(DebugsCog(bot))
    await bot.add_cog(AdminCog(bot))
    await bot.add_cog(ServerManagementCog(bot))
    await bot.add_cog(StatsCog(bot))
    await bot.add_cog(TANetworkCog(bot))
    # await bot.add_cog(QueueCacheCog(bot))
    # await bot.add_cog(QueueCog(bot))
    await bot.add_cog(PugQueueCog(bot))


@bot.event
async def on_message(message):
    await bot.process_commands(message)

    # Update the last message for the channel
    last_messages[message.channel.id] = message.content

    end_date = datetime.now()
    start_date = datetime(2018, 1, 1)

    await check_substitution(bot, message, start_date, end_date)

    if message.embeds:
        embed = message.embeds[0]
        
        if await check_2v2_game_start(message, embed):
            return
        
        await check_pug_game_start(bot, message, embed, start_date, end_date)
        await check_map_start(message, embed)


bot.run(TOKEN)