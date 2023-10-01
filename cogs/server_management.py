import time
import asyncssh
import configparser
import asyncio
from discord.ext import commands
from discord import Embed
from data.player_mappings import player_name_mapping
from data.user_roles import privileged_users, bot_admins
from data.map_commands import map_commands, map_commands_arena
from modules.embeds_formatting import add_maps_to_embed
from modules.utilities import check_bot_admin

# Initialize the configparser and read the config.ini
config = configparser.ConfigParser()
config.read('config.ini')

class ServerManagementCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def kill(self, ctx, container: str = None):
        # Only proceed if the author's ID is in the allowed list
        if ctx.author.id not in privileged_users:
            embed = Embed(title="Permission Denied", description="Elevated admin permissions required to execute this command.", color=0xff0000)
            await ctx.send(embed=embed)
            return

        if container not in ["2v2", "pug", "all"]:
            embed = Embed(title="Invalid Argument", description="Please specify a valid container to kill (`2v2`, `pug`, or `all`).", color=0xff0000)
            await ctx.send(embed=embed)
            return

        containers_killed = []

        # Connect to the remote server via SSH
        try:
            async with asyncssh.connect(config['Server1']['IP'], username=config['Server1']['Username'], password=config['Server1']['Password']) as conn:
                if container == "2v2" or container == "all":
                    result_2v2 = await conn.run('docker kill taserver_2v2b_4')
                    if not result_2v2.stderr:
                        containers_killed.append("2V2")
                if container == "pug" or container == "all":
                    result_pug = await conn.run('docker kill PUGS')
                    if not result_pug.stderr:
                        containers_killed.append("PUG")
                
                killed_containers_str = " and ".join(containers_killed)
                user_id = ctx.author.id
                user_name = player_name_mapping.get(user_id, str(ctx.author))  # Use the discord username as fallback
                embed_title = f"{killed_containers_str} container{'s' if len(containers_killed) > 1 else ''} shut down successfully by {user_name}"
                embed_description = f"Container{'s' if len(containers_killed) > 1 else ''} {killed_containers_str} have been shut down."
                embed = Embed(title=embed_title, description=embed_description, color=0x00ff00)
                await ctx.send(embed=embed)

        except Exception as e:
            embed = Embed(title="Error", description=f"Error shutting down containers: {e}", color=0xff0000)
            await ctx.send(embed=embed)
            
    @commands.command()
    async def setmap(self, ctx, map_shortcut: str = None, server_num: int = 1):
        start_time = time.time()  # Start timer when the command is triggered
        print(f"Start Time: {start_time}")
        
        if not await check_bot_admin(ctx):
            return

        
        # Convert map_shortcut to lowercase if it is not None
        map_shortcut = map_shortcut.lower() if map_shortcut is not None else None
        
        # If map_shortcut is None or not in map_commands and map_commands_arena, send the list of valid commands
        if map_shortcut is None or (map_shortcut not in map_commands and map_shortcut not in map_commands_arena):
            embed_creation_time = time.time()  # Timer before creating the embed
            embed = Embed(title="Map Commands", description="Use '!setmap `id`' to start maps.", color=0x00ff00)
            
            add_maps_to_embed(embed, map_commands, "CTF Maps", 3)
            embed.add_field(name='\u200b', value='\u200b', inline=False)
            add_maps_to_embed(embed, map_commands_arena, "Arena Maps", 1)
            
            await ctx.send(embed=embed)
            
            embed_sent_time = time.time()  # Timer after sending the embed
            print(f"Embed Creation and Send Time: {embed_sent_time - embed_creation_time} seconds")
            return
        
        # Start the map using the start_map function
        await start_map(ctx, map_shortcut, server_num)

        end_time = time.time()  
        print(f"Total Execution Time: {end_time - start_time} seconds")

async def start_map(ctx, map_shortcut, server_num=1):
        """Starts the map based on the provided shortcut."""
        try:
            # Use the server_num to get the correct server configuration
            server_config = config[f'Server{server_num}']

            countdown_seconds = 35 + (20 if server_num == 2 else 0)
            
            # Establishing SSH connection and sending the command
            connect_time = time.time()  # Timer before connecting to SSH
            async with asyncssh.connect(server_config['IP'], 
                                username=server_config['Username'], 
                                password=server_config['Password'], 
                                known_hosts=None) as conn:
                print(f"SSH Connect Time: {time.time() - connect_time} seconds")
                
                if map_shortcut in map_commands:
                    command = f"~/pugs.sh -m {map_shortcut}"
                elif map_shortcut in map_commands_arena:
                    command = f"~/2v2b.sh -m {map_shortcut}"
                else:
                    await ctx.send("Invalid map shortcut")
                    return
                
                exec_time = time.time()  # Timer before executing command over SSH
                result = await conn.run(command)
                print(f"SSH Command Execution Time: {time.time() - exec_time} seconds")
                
                output, error = result.stdout, result.stderr

                # Handling the output or error from the SSH command
                if output:
                    user_id = ctx.author.id if hasattr(ctx, 'author') else "System"
                    user_name = player_name_mapping.get(user_id, str(ctx.author) if hasattr(ctx, 'author') else "System")
                    
                    embed = Embed(title=f"Map changed successfully by {user_name}", description=f"```{output}```", color=0x00ff00)
                    message = await ctx.send(embed=embed)
                    await asyncio.sleep(1)
                    
                    # Start the countdown
                    for i in range(countdown_seconds, 0, -1):
                        embed.description = f"```{output}```\nServer joinable in {i} seconds..."
                        await message.edit(embed=embed)
                        await asyncio.sleep(1)
                    
                    embed.description = f"```{output}```\nServer is now joinable!"
                    await message.edit(embed=embed)
                elif error:
                    embed = Embed(title="Error changing map", description=f"```{error}```", color=0xff0000)
                    await ctx.send(embed=embed)
                else:
                    embed = Embed(title="Map changed successfully", color=0x00ff00)
                    await ctx.send(embed=embed)
                
        except Exception as e:
            await ctx.send(f"Error changing map: {e}")
