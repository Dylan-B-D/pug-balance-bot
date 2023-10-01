import matplotlib.pyplot as plt
import os
import numpy as np
import pandas as pd
from datetime import datetime

def create_map_weights_chart(map_weights):
    map_names = list(map_weights.keys())
    weights = list(map_weights.values())
    
    plt.figure(figsize=(10,6))
    
    plt.gca().set_facecolor('none')
    
    plt.barh(map_names, weights, color='skyblue')
    
    # Setting the color of the labels and title to white
    plt.xlabel('Weights', color='white')
    plt.title('Map Weights', color='white')
    
    # Changing the color of the ticks to white
    plt.tick_params(axis='x', colors='white')
    plt.tick_params(axis='y', colors='white')
    
    plt.gca().invert_yaxis()
    
    plt.tight_layout()
    subfolder = "charts"
    os.makedirs(subfolder, exist_ok=True)  
    
    filename = f"{subfolder}/map_weights_chart.png"
    plt.savefig(filename, facecolor='none')
    plt.close()
    return filename

def create_rolling_percentage_chart(player_id, data, queue_type):
    SMOOTHING_WINDOW = 40  
    GRAPH_HEIGHT = 1.3  

    percentages = []

    # Start from the 11th game and look at the last 10 games from that point
    for idx in range(10, len(data)):
        last_10_games = data[idx-10:idx]
        games_played_in_last_10 = sum(1 for game in last_10_games for player in game['players'] if player['user']['id'] == player_id)
        percentage_played = (games_played_in_last_10 / 10) * 100
        percentages.append(percentage_played)
    
    # Apply a rolling mean with a window defined by SMOOTHING_WINDOW
    smoothed_percentages = pd.Series(percentages).rolling(window=SMOOTHING_WINDOW).mean().dropna()

    plt.figure(figsize=(8,GRAPH_HEIGHT))
    plt.plot(np.arange(len(smoothed_percentages)), smoothed_percentages, color='skyblue')

    # Remove everything that's not the graph line and the y-axis ticks
    plt.gca().axes.get_xaxis().set_visible(False)
    plt.gca().spines["top"].set_visible(False)
    plt.gca().spines["right"].set_visible(False)
    plt.gca().spines["bottom"].set_visible(False)
    plt.gca().set_facecolor('none')
    plt.tick_params(axis='y', colors='white')
    plt.tight_layout()

    subfolder = "charts"
    os.makedirs(subfolder, exist_ok=True)  

    filename = f"{subfolder}/rolling_percentage_chart_{queue_type}.png"
    plt.savefig(filename, facecolor='none')
    plt.close()
    return filename

def plot_game_lengths(timestamps, game_lengths, queue):
    """
    Plot the game lengths over games played and save it to a file.
    :param timestamps: List of game timestamps.
    :param game_lengths: List of game lengths.
    :param queue: The queue for which the data pertains.
    :return: Filename of the saved plot.
    """
    
    SMOOTHNESS_WINDOW = 40  # Window size for moving average smoothness

    # Filter out anomalies in game lengths
    valid_indices = [i for i, length in enumerate(game_lengths) if 20 <= length <= 60]
    game_lengths = [game_lengths[i] for i in valid_indices]
    timestamps = [timestamps[i] for i in valid_indices]

    # Sorting the data based on timestamps to get them in order
    sorted_indices = np.argsort(timestamps)
    game_lengths = np.array(game_lengths)[sorted_indices]

    # Apply a simple moving average to smooth the line
    game_lengths_smoothed = np.convolve(game_lengths, np.ones(SMOOTHNESS_WINDOW) / SMOOTHNESS_WINDOW, mode='valid')
    
    # Create an x-axis for games played
    games_played = np.arange(len(game_lengths_smoothed))

    # Plot the data
    plt.figure(figsize=(10, 5))
    plt.plot(games_played, game_lengths_smoothed, linestyle='-')

    plt.xlabel('Games Played', color="white")
    plt.ylabel('Game Length (minutes)', color="white")
    plt.xticks(color="white")
    plt.yticks(color="white")

    # Simplify the graph
    plt.gca().spines['top'].set_visible(False)
    plt.gca().spines['right'].set_visible(False)
    plt.gca().spines['bottom'].set_linewidth(0.5)
    plt.gca().spines['left'].set_linewidth(0.5)
    plt.gca().set_facecolor('none')
    plt.grid(False)

    # Save the plot as an image with no background and return the filename
    filename = "charts/game_lengths_over_games_played.png"
    plt.savefig(filename, transparent=True, bbox_inches='tight', pad_inches=0)
    
    return filename