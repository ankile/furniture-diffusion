# Download Isaac Gym
curl -c ./cookie.txt -s -L "https://drive.google.com/uc?export=download&id=1J4bb5SfY-8H05xXiyF4N1xUOas390tll" > /dev/null
curl -Lb ./cookie.txt "https://drive.google.com/uc?export=download&confirm=$(awk '/confirm/ {print $NF}' ./cookie.txt)&id=1J4bb5SfY-8H05xXiyF4N1xUOas390tll" -o isaacgym.tar.gz

# Download Furniture-benchmark
git clone git@github.com:ankile/furniture-bench.git

# Install miniconda
mkdir -p ~/miniconda3
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda3/miniconda.sh
bash ~/miniconda3/miniconda.sh -b -u -p ~/miniconda3
rm -rf ~/miniconda3/miniconda.sh

~/miniconda3/bin/conda init bash

# Create conda environment
conda create -n rlgpu python=3.8

# Activate conda environment
conda activate rlgpu

# Install dependencies
cd isaacgym
pip install -e python

cd ../furnture-bench
pip install -e .

