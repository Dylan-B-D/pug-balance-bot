# pug-balance-bot

# Available commands
1. !debug   -  Admin-only command for debugging ID matching results for previous game.
2. !info  -  Shows map weighting for generated games, and voting thresholds.
3. !kill  -  Elevated-admin command to shutdown docker containers. Usage: !kill [container]
4. !listadmins  -  Lists all the admins.
5. !match -  Restricted command for simulating a game starting.
6. !servers  -  Shows server list for 'PUG Login'.
7. !serverstats  -  Gets some simple stats for the PUG queues.
8. !setmap - Admin only command for setting the PUG/2v2 map. Returns list of map IDs if no parameter given. Usage: !setmap [map_id]
9. !status - Shows server status for PUGs with no param, or shows status for Mixers with a param of '2'. Usage: !status [optional_server_index]

## Requirements

- Python 3.x
- Discord bot token

## Setup Instructions

1. Ensure that Python is installed on your system. If not, you can download it from [Python's official website](https://www.python.org/downloads/).
   
2. Clone the bot repository to your local machine.

3. Open a terminal or command prompt, navigate to the bot's directory, and run the following command to install the required packages:
   
   ```sh
   pip install discord.py requests trueskill scipy matplotlib configparser asyncio asyncssh

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
