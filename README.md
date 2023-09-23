# pug-balance-bot

# Available commands
1. debug - Shows debug info for matching IDs to a BullyBot game
2. info - Shows info about map weightings for map selection and voting thresholds       
3. match - Debug command to simulate a match
4. servers - Shows servers on a given login server
5. serverstats - Shows some basic stats from a bullybot data source
6. setmap - Sets the game map based on a given id

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
