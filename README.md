# pug-balance-bot

# Available commands
1. !clear  -  Restricted command to clear the bot's messages from the last 10 mins.
2. !debug  -  Admin-only command for debugging ID matching results for previous game.
3. !gamelengths  -  Shows a graph of game lengths over time.
4. !gamehistory  -  Shows the last few maps played (Max 10). Usage: !gamehistory [optional_history_count].
5. !help - Shows a list of commands and how to use them.
6. !info  -  Shows map weighting for generated games, and voting thresholds.
7. !kill  -  Elevated-admin command to shutdown docker containers. Usage: !kill [container].
8. !listadmins  -  Lists all the admins.
9. !match - Restricted command for simulating a game starting.
10. !ranks  -  Restricted command to show player ranks.
11. !servers  -  Shows server list for 'PUG Login'.
12. !serverstats  -  Gets some simple stats for the PUG queues.
13. !setmap - Admin only command for setting the PUG/2v2 map. Returns list of map IDs if no parameter given. Usage: !setmap [map_id].
14. !setname  -  Sets the player's username which is used to display stats/balanced teams etc. Usage: !setname [@User][new_username].
15. !showstats - Shows some basic stats for a given player. Usage: !showstats [@User].
16. !startgame  -  Restricted debug command to simulate when teams have been picked.
17. !status - Shows server status for PUGs with no param, or shows status for Mixers with a param of '2'. Usage: !status [optional_server_index].


## Requirements

- Python 3.x
- Discord bot token

## Setup Instructions

1. Ensure that Python is installed on your system. If not, you can download it from [Python's official website](https://www.python.org/downloads/).
   
2. Clone the bot repository to your local machine.

3. Before running the bot, ensure you have both `npm` and `node` installed. If not, install them first. Instructions can be found on the [official Node.js website](https://nodejs.org/).

4. Open a terminal or command prompt, navigate to the bot's directory, and run the following command to install the required Python packages:

```sh
pip install discord.py requests trueskill scipy matplotlib configparser asyncio asyncssh pytz

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
