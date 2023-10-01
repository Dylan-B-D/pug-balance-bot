import discord
from typing import Union
from discord import Member
from collections import Counter
from discord.ext import commands
from discord import Embed, Colour
from datetime import datetime, timedelta
from data.player_mappings import player_name_mapping
from modules.data_managment import (fetch_data)
from modules.rating_calculations import calculate_ratings
from modules.charts import (create_rolling_percentage_chart, plot_game_lengths)


class StatsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    @commands.command(name='serverstats')
    async def server_stats(self, ctx):
        try:
            start_date_total = datetime.min
            end_date = datetime.now()
            start_date_7_days = end_date - timedelta(days=7)
            
            total_data_2v2 = fetch_data(start_date_total, end_date, '2v2')
            total_data_NA = fetch_data(start_date_total, end_date, 'NA')
            total_data = total_data_2v2 + total_data_NA
            last_7_days_data = [game for game in total_data if start_date_7_days <= datetime.fromtimestamp(game['timestamp'] / 1000) <= end_date]
            
            total_games = len(total_data)
            unique_players_total = len(set(player['user']['id'] for game in total_data for player in game['players']))
            
            games_last_7_days = len(last_7_days_data)
            unique_players_last_7_days = len(set(player['user']['id'] for game in last_7_days_data for player in game['players']))
            
            # Find top players with most games in the last 7 days
            player_counter = Counter(player['user']['id'] for game in last_7_days_data for player in game['players'])
            top_players = player_counter.most_common(5)
            
            top_players_text = ""
            for i, (player_id, count) in enumerate(top_players, start=1):
                player_id = int(player_id)  # Convert to int if player_id is a string in your data
                # Directly use the mapping for the username
                player_name = player_name_mapping.get(player_id, f"Unknown User ({player_id})")
                top_players_text += f"{i}. {player_name} - {count} games\n"
            
            # Create and send embed
            embed = Embed(title="Server Stats", color=Colour.blue())
            embed.add_field(name="Total Games", value=str(total_games), inline=True)
            embed.add_field(name="Unique Players", value=str(unique_players_total), inline=True)
            embed.add_field(name='\u200B', value='\u200B', inline=False)
            embed.add_field(name="Last 7 Days Games", value=str(games_last_7_days), inline=True)
            embed.add_field(name="Last 7 Days Players", value=str(unique_players_last_7_days), inline=True)
            embed.add_field(name='\u200B', value='\u200B', inline=False)
            embed.add_field(name="Top Players (Last 7 Days)", value=top_players_text.strip(), inline=False)

            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"Error fetching server stats: {e}")

    @commands.command()
    async def ranks(self, ctx, min_games: int = 30):
        if not ctx.guild:
            await ctx.send("This command can only be used within a server.")
            return

        allowed_user_id = 252190261734670336

        if ctx.author.id != allowed_user_id:
            embed = Embed(title="Permission Denied", description="This command is restricted.", color=0xff0000)
            await ctx.send(embed=embed)
            return

        try:
            # Get the timestamp for 6 months ago
            six_months_ago = datetime.now() - timedelta(days=6*30)  # Using a rough estimate of 6 months
            six_months_ago_timestamp = int(six_months_ago.timestamp() * 1000)  # Convert to the same format used in the data

            # Fetching the data and calculating ratings
            end_date = datetime.now()
            start_date = datetime(2018, 1, 1)
            data = fetch_data(start_date, end_date, 'NA')

            # Get the latest game timestamp for each player
            latest_game_timestamps = {}
            for game in data:
                for player in game['players']:
                    user_id = player['user']['id']
                    game_timestamp = game['timestamp']
                    if user_id not in latest_game_timestamps or game_timestamp > latest_game_timestamps[user_id]:
                        latest_game_timestamps[user_id] = game_timestamp

            # Filter players based on activity and minimum games played criteria
            active_players = {user_id for user_id, timestamp in latest_game_timestamps.items() if timestamp >= six_months_ago_timestamp}
            player_ratings, _, games_played, _ = calculate_ratings(data, queue='NA')
            players_list = [{'id': user_id, 'mu': rating.mu} for user_id, rating in player_ratings.items() 
                            if user_id in active_players and games_played[user_id] >= min_games]

            players_list.sort(key=lambda x: x['mu'], reverse=True)
            total_players = len(players_list)

            # Correcting the percentile calculation
            for index, player in enumerate(players_list):
                player["percentile"] = 1 - (index / total_players)
                player["rank"] = determine_rank(player["percentile"])

            # Group players by rank
            players_grouped_by_rank = {}
            for player in players_list:
                players_grouped_by_rank.setdefault(player["rank"], []).append(player)

            # Constants for formatting
            COLUMNS = 3
            PLAYERS_PER_COLUMN = 20

            for rank, players_of_rank in players_grouped_by_rank.items():
                total_players_of_rank = len(players_of_rank)
                players_per_embed = PLAYERS_PER_COLUMN * COLUMNS
                players_per_column_for_rank = (total_players_of_rank + COLUMNS - 1) // COLUMNS  # Distribute players evenly across columns

                for i in range(0, total_players_of_rank, players_per_embed):
                    embed = Embed(title=f"{rank} Rankings", color=0x00ff00)
                    chunk = players_of_rank[i:i+players_per_embed]

                    column_data = ["", "", ""]
                    for j, player in enumerate(chunk):
                        user_id = player['id']
                        mu = player['mu']
                        rank_position = i + j + 1

                        # Getting the player name from the mapping or the guild members
                        name = player_name_mapping.get(user_id)
                        if not name:
                            member = ctx.guild.get_member(user_id)
                            name = member.display_name if member else str(user_id)

                        col_idx = j // players_per_column_for_rank
                        column_data[col_idx] += f"{rank_position}. {name} - µ: {mu:.2f}\n"

                    for idx, col in enumerate(column_data):
                        if col:
                            embed.add_field(name=f"Column {idx+1}", value=col, inline=True)

                    await ctx.send(embed=embed)

        except Exception as e:
            embed = Embed(title="Error", description="An error occurred while fetching player rankings.", color=0xff0000)
            await ctx.send(embed=embed)
            print(f"Error in !ranks command: {e}")

    @commands.command()
    async def showstats(self, ctx, *, player_input: Union[Member, str]):
        try:
            # If the input is a Discord member, use their ID directly
            if isinstance(player_input, Member):
                player_id = player_input.id
                display_name = player_name_mapping.get(player_id, player_input.display_name)  # Use the mapped name if available

            # If the input is a string, check if it's in our name mapping or do a partial match search
            elif isinstance(player_input, str):
                if player_input in player_name_mapping.values():
                    player_id = next(k for k, v in player_name_mapping.items() if v == player_input)
                    display_name = player_input
                else:
                    player_id = next((k for k, v in player_name_mapping.items() if player_input.lower() in v.lower()), None)
                    if not player_id:
                        await ctx.send(f"Cannot find a player with the name {player_input}.")
                        return
                    display_name = player_name_mapping.get(player_id, player_input)  # Use the mapped name if available

            # Fetch data using the ALL queue
            start_date = datetime(2018, 1, 1)
            end_date = datetime.now()

            total_data_ALL = fetch_data(start_date, end_date, 'ALL')

            # Filter games for each queue
            total_data_NA = [game for game in total_data_ALL if game['queue']['name'] == 'PUGz']
            total_data_2v2 = [game for game in total_data_ALL if game['queue']['name'] == '2v2']

            games_for_player_NA = sum(1 for game in total_data_NA for player in game['players'] if player['user']['id'] == player_id)
            games_for_player_2v2 = sum(1 for game in total_data_2v2 for player in game['players'] if player['user']['id'] == player_id)
            games_for_player_ALL = games_for_player_NA + games_for_player_2v2

            # Assuming total_data_ALL is sorted in descending order of time (i.e., recent games first)
            last_10_games_played = sum(1 for game in total_data_ALL[:10] for player in game['players'] if player['user']['id'] == player_id)

            chart_filename_NA = create_rolling_percentage_chart(player_id, total_data_NA, 'NA')
            chart_filename_2v2 = create_rolling_percentage_chart(player_id, total_data_2v2, '2v2')

            # Create the embed for stats
            embed = Embed(title=f"{display_name}'s Stats", color=Colour.blue())
            
            last_played_str = calculate_last_played(player_id, total_data_ALL)
            if not last_played_str:
                last_played_str = "Not available"


            # Create the embed for stats
            embed = Embed(title=f"{display_name}'s Stats", color=Colour.blue())

            # Add Last Played Information
            embed.add_field(name="Last Played", value=last_played_str, inline=False)

            # Multi-column format for queue and games played
            embed.add_field(name="Queue", value="NA\n2v2\nTotal", inline=True)
            embed.add_field(name="Games Played", value=f"{games_for_player_NA}\n{games_for_player_2v2}\n{games_for_player_ALL}", inline=True)
            
            await ctx.send(embed=embed)
            
            # Create and send the NA chart if games were played in that queue
            if games_for_player_NA > 0:
                chart_filename_NA = create_rolling_percentage_chart(player_id, total_data_NA, 'PUG')
                file_NA = discord.File(chart_filename_NA, filename="rolling_percentage_chart_NA.png")
                embed_NA = Embed(title="Percentage played over the last 10 games (NA)", color=Colour.blue())
                embed_NA.set_image(url="attachment://rolling_percentage_chart_NA.png")
                await ctx.send(embed=embed_NA, file=file_NA)

            # Create and send the 2v2 chart if games were played in that queue
            if games_for_player_2v2 > 0:
                chart_filename_2v2 = create_rolling_percentage_chart(player_id, total_data_2v2, '2v2')
                file_2v2 = discord.File(chart_filename_2v2, filename="rolling_percentage_chart_2v2.png")
                embed_2v2 = Embed(title="Percentage played over the last 10 games (2v2)", color=Colour.blue())
                embed_2v2.set_image(url="attachment://rolling_percentage_chart_2v2.png")
                await ctx.send(embed=embed_2v2, file=file_2v2)
        except Exception as e:
            await ctx.send(f"Error fetching stats for {player_input}: {e}")

    
    @commands.command()
    async def gamelengths(self, ctx, queue: str = 'ALL'):
        try:
            # Fetch and process the data
            timestamps, game_lengths = get_game_lengths(queue)

            # Plot the data and get the filename
            filename = plot_game_lengths(timestamps, game_lengths, queue)

            # Send the image to Discord
            file = discord.File(filename, filename=filename.split('/')[-1])
            embed = discord.Embed(title="Game Length Over Time", description=f"", color=discord.Colour.blue())
            embed.set_image(url=f"attachment://{filename.split('/')[-1]}")
            await ctx.send(embed=embed, file=file)

        except Exception as e:
            await ctx.send(f"Error fetching and plotting game lengths: {e}")



    @commands.command()
    async def gamehistory(self, ctx, number_of_maps: int = 5):
        # Checking if number of maps requested is within limits
        if number_of_maps > 10:
            await ctx.send("Error: Cannot fetch more than 10 maps.")
            return

        # Parsing game history from channel
        maps = await parse_game_history_from_channel(ctx.channel, number_of_maps)
        
        if not maps:
            await ctx.send("No maps found in recent history.")
            return

        # Constructing the embed
        embed = Embed(title=f"Last {len(maps)} Maps", description="", color=0x00ff00)

        for map_data in maps:
            map_name = map_data["name"]
            map_date = map_data["date"]
            time_difference = time_ago(map_date)  # Directly pass the datetime object
            embed.add_field(name=map_name, value=f"Played `{time_difference}`", inline=False)

        await ctx.send(embed=embed)

def determine_rank(percentile):
    if percentile >= 0.95: return "Grandmaster"
    if percentile >= 0.85: return "Master"
    if percentile >= 0.70: return "Diamond"
    if percentile >= 0.40: return "Platinum"
    if percentile >= 0.25: return "Gold"
    if percentile >= 0.15: return "Silver"
    return "Bronze"

def calculate_last_played(player_id, game_data):
    for game in reversed(game_data):
        for player in game['players']:
            if player['user']['id'] == player_id:
                last_played = datetime.fromtimestamp(game['timestamp'] / 1000)
                return time_ago(last_played)
    return None

def time_ago(past):
    now = datetime.now()

    # Time differences in different periods
    delta_seconds = (now - past).total_seconds()
    delta_minutes = delta_seconds / 60
    delta_hours = delta_minutes / 60
    delta_days = delta_hours / 24
    delta_weeks = delta_days / 7
    delta_months = delta_days / 30.44  # Average number of days in a month
    delta_years = delta_days / 365.25  # Average number of days in a year considering leap years

    # Determine which format to use based on the period
    if delta_seconds < 60:
        return "{} second(s) ago".format(int(delta_seconds))
    elif delta_minutes < 60:
        return "{} minute(s) ago".format(int(delta_minutes))
    elif delta_hours < 24:
        return "{} hour(s) ago".format(int(delta_hours))
    elif delta_days < 7:
        return "{} day(s) ago".format(int(delta_days))
    elif delta_weeks < 4.35:  # Approximately number of weeks in a month
        return "{} week(s) ago".format(int(delta_weeks))
    elif delta_months < 12:
        return "{} month(s) ago".format(int(delta_months))
    else:
        return "{} year(s) ago".format(int(delta_years))
    

async def parse_game_history_from_channel(channel, limit):
    maps = []
    last_message_id = None
    continue_search = True

    while len(maps) < limit and continue_search:
        if last_message_id:
            history = channel.history(limit=100, before=discord.Object(id=last_message_id))
        else:
            history = channel.history(limit=100)

        fetched_messages = 0
        async for message in history:
            fetched_messages += 1

            if len(maps) == limit:  # Stop once we have enough
                break
            if message.embeds:
                embed = message.embeds[0]
                description = embed.description
                if description and "**Maps:**" in description:
                    map_name = description.split("**Maps:**")[1].strip()
                    naive_datetime = message.created_at.replace(tzinfo=None)
                    maps.append({"name": map_name, "date": naive_datetime})  # Store entire datetime

            last_message_id = message.id

        # If the loop didn't break due to the limit and we didn't fetch a full 100 messages, 
        # it means there's no more history
        if fetched_messages < 100:
            continue_search = False

    return maps

def get_game_lengths(queue):
    """
    Fetch game data, calculate game lengths and return them.
    :param queue: The game queue.
    :return: List of timestamps and game lengths.
    """
    start_date = datetime(2018, 1, 1)
    end_date = datetime.now()
    game_data = fetch_data(start_date, end_date, queue)

    # Calculate the game lengths and their respective timestamps
    timestamps = [game['timestamp'] for game in game_data]
    game_lengths = [(game['completionTimestamp'] - game['timestamp']) / (60 * 1000) for game in game_data]  # in minutes

    return timestamps, game_lengths