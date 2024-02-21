#!/bin/bash

#SBATCH -p gpu                
#SBATCH -t 0-16:00            
#SBATCH --mem=256G            
#SBATCH --gres=gpu:1          
#SBATCH -c 32                 
#SBATCH -o wandb_output_%j.log  
#SBATCH -e wandb_error_%j.log   



# Run the wandb agent command
wandb agent robot-rearrangement/sweeps/44hsqeuy # Square table baseline
