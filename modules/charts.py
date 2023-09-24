import matplotlib.pyplot as plt
import os

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