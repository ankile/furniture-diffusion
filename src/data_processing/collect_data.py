import argparse
import os
from pathlib import Path

from furniture_bench.data.data_collector import DataCollector

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--randomness", type=str, default="low")
    parser.add_argument("--num-demos", type=int, default=100)
    parser.add_argument("--obs-type", type=str, default="state")
    parser.add_argument("--gpu-id", type=int, default=0)
    parser.add_argument(
        "--resize-img-after-sim", dest="resize_img_after_sim", action="store_true"
    )
    parser.add_argument(
        "--resize-sim-img", dest="small_sim_img_size", action="store_true"
    )

    args = parser.parse_args()

    furniture = "one_leg"
    randomness = args.randomness

    BASE = Path(os.environ.get("FURNITURE_DATA_DIR", "data"))

    # Check that we are not trying to resize images with CV2 if images come from
    # the simulator as already small
    assert not (args.resize_img_after_sim and args.small_sim_img_size)

    obs_type = args.obs_type

    # Add the suffix _highres if we are not resizing images in or after simulation
    if not args.resize_img_after_sim and not args.small_sim_img_size:
        obs_type = obs_type + "_highres"

    # Add the suffix _small if we are resizing images only in simulation
    elif args.small_sim_img_size:
        obs_type = obs_type + "_small"

    data_path = BASE / "raw/sim" / obs_type / furniture / randomness
    print(f"Saving data to {data_path}")

    encoder_type = "vip"

    collector = DataCollector(
        is_sim=True,
        data_path=data_path,
        furniture=furniture,
        device_interface=None,
        headless=True,
        draw_marker=True,
        manual_label=True,
        scripted=True,
        randomness=randomness,
        gpu_id=args.gpu_id,
        pkl_only=True,
        save_failure=False,
        num_demos=args.num_demos,
        verbose=False,
        show_pbar=True,
        obs_type=args.obs_type,
        encoder_type=encoder_type,
        resize_img_after_sim=args.resize_img_after_sim,
        small_sim_img_size=args.small_sim_img_size,
    )

    collector.collect()
