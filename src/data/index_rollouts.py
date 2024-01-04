import pickle
from glob import glob
from pathlib import Path
import os
from tqdm import tqdm
import pandas as pd

base_dir = Path(os.environ["FURNITURE_DATA_DIR"])

rollout_dir = base_dir / "raw" / "sim_rollouts"

## Index the raw rollout data
paths = glob(str(rollout_dir / "**/*.pkl"), recursive=True)
# Make a new index file in this directory specifying which rollouts were successful and for which task
file_path = rollout_dir / "index.csv"

# Check if the file already exists
if file_path.exists():
    print("Index file already exists, exiting")
else:
    print("Creating index file")
    # Create the index file
    with open(file_path, "w") as f:
        f.write("path,furniture,success\n")

# Get a set of all the paths that are already in the index file
read_idxs = set(pd.read_csv(file_path)["path"])
remaining_paths = [p for p in paths if p not in read_idxs]

print(f"Already indexed {len(read_idxs)} rollouts, {len(remaining_paths)} remaining")


# Process all the rollouts not already in the index file
for path in tqdm(remaining_paths):
    with open(path, "rb") as f:
        rollout = pickle.load(f)

    # Check if the rollout was successful
    success = rollout["success"]

    # Get the furniture name
    furniture = rollout["furniture"]

    # Append the path to the index file
    with open(file_path, "a") as f:
        f.write(f"{path},{furniture},{success}\n")