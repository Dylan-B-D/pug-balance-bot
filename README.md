# pug-balance-bot
This project uses the ta-network-api made by Giga to retrieve server info, which can be found here:
[ta-network api GitHub](https://github.com/wilderzone/ta-network-api)

# Available commands

1. !adduser  - Usage: !adduser user_input queue_name
2. !clear  - Restricted command to clear the bot's messages from the last 10 mins.
3. !createqueue  - Usage: !createqueue [optional_queuename] [optional_queuesize].
4. !debug  - Admin-only command for debugging ID matching results for the previous game.
5. !end  - Usage: !end [optional_flag].
6. !gamehistory  - Shows the last few maps played (Max 10). Usage: !gamehistory [optional_history_count].
7. !gamelengths  - Shows a graph of game lengths over time. Usage: !gamelengths [optional_queue].
8. !gamestats
9. !getcapvalues - Shows a list of players cap values, where 1 is always cap, and 0 is never cap.
10. !help - Shows a list of commands and how to use them.
11. !info  - Shows map weighting for generated games, and voting thresholds.
12. !kill  - Elevated-admin command to shutdown docker containers. Usage: !kill [optional_container].
13. !listadmins  - Lists all the admins.
14. !listplayers  - Usage: !listplayers [optional_min_games].
15. !log
16. !match - Restricted command for simulating a game starting.
17. !menu
18. !pli  - Usage: !pli [optional_min_games].
19. !pugsettings
20. !ranks  - Restricted command to show player ranks. Usage: !ranks [optional_min_games].
21. !removepugchannel
22. !removequeue  - Usage: !removequeue queuename.
23. !removeuser  - Usage: !removeuser user_input queue_name.
24. !servers  - Shows server list for 'PUG Login'.
25. !setafktime  - Usage: !setafktime minutes.
26. !setcapvalue - Sets a user's cap value. Usage: !setcapvalue [optional_@User] [optional_value].
27. !setmap - Admin only command for setting the PUG/2v2 map. Returns a list of map IDs if no parameter given. Usage: !setmap [optional_map_shortcut] [optional_server_num].
28. !setname  - Sets the player's username which is used to display stats/balanced teams etc. Usage: !setname [@User] new_username.
29. !setpugchannel
30. !showstats - Shows some basic stats for a given player. Usage: !showstats [@User or username].
31. !startgame  - Restricted debug command to simulate when teams have been picked.
32. !status - Shows server status for PUGs with no param, or shows status for Mixers with a param of '2'. Usage: !status [optional_server_index].
33. !subuser  - Usage: !subuser user_input.
34. !serverstats  - Gets some simple stats for the PUG queues.


## Requirements

- Python 3.x
- Discord bot token

## Setup Instructions

1. Ensure that Python is installed on your system. If not, you can download it from [Python's official website](https://www.python.org/downloads/).
   
2. Clone the bot repository to your local machine.

3. Before running the bot, ensure you have both `npm` and `node` installed. If not, install them first. Instructions can be found on the [official Node.js website](https://nodejs.org/).

4. Open a terminal or command prompt, navigate to the bot's directory, and run the following command to install the required Python packages:

   ```sh
   pip install discord.py requests trueskill scipy matplotlib configparser asyncio asyncssh pytz pandas filelock aiohttp bson

5. Also install these nmp package(s)
   ```sh
   nmp install ini

## Configuration

1. Copy `config.example.ini` to `config.ini`.
2. Open `config.ini` and fill in the appropriate values for each field.
3. Save `config.ini` and run the software.

**Note: Never share your `config.ini` file with anyone as it contains sensitive information.**
. Replace the placeholder text with your bot's actual token, which can be obtained from the [Discord Developer Portal](https://discord.com/developers/applications).

## Running the bot locally
Run the bot by executing the Python file in the terminal or command prompt.
   
   ```sh
   python pug_balance_bot.py
