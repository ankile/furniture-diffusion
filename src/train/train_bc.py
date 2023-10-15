import os
from pathlib import Path
import furniture_bench
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import wandb
from diffusers.optimization import get_scheduler
from src.data.dataset import FurnitureImageDataset, SimpleFurnitureDataset
from src.eval import calculate_success_rate
from src.gym import get_env
from tqdm import tqdm
from ipdb import set_trace as bp
from src.models.actor import DoubleImageActor
from src.common.pytorch_util import dict_apply
import argparse

from ml_collections import ConfigDict

model_save_dir = Path("models")


def main(config: ConfigDict):
    env = None
    device = torch.device(
        f"cuda:{config.gpu_id}" if torch.cuda.is_available() else "cpu"
    )

    # Init wandb
    wandb.init(
        project="furniture-diffusion",
        entity="ankile",
        config=config.to_dict(),
        mode="online" if not config.dryrun else "disabled",
    )
    config = wandb.config

    if config.observation_type == "image":
        dataset = FurnitureImageDataset(
            dataset_path=config.datasim_path,
            pred_horizon=config.pred_horizon,
            obs_horizon=config.obs_horizon,
            action_horizon=config.action_horizon,
            data_subset=config.data_subset,
        )
    # elif config.observation_type == "feature":
    #     dataset = SimpleFurnitureDataset(
    #         dataset_path=config.datasim_path,
    #         pred_horizon=config.pred_horizon,
    #         obs_horizon=config.obs_horizon,
    #         action_horizon=config.action_horizon,
    #     )
    else:
        raise ValueError(f"Unknown observation type: {config.observation_type}")

    # save training data statistics (min, max) for each dim
    stats = dataset.stats

    # Update the config object with the action dimension
    config.action_dim = dataset.action_dim
    config.robot_state_dim = dataset.robot_state_dim

    # Create the policy network
    actor = DoubleImageActor(
        device=device,
        encoder_name="resnet18",
        config=config,
        stats=stats,
    )

    # Update the config object with the observation dimension
    config.obs_dim = actor.obs_dim

    # save stats to wandb
    wandb.log(
        {
            "num_samples": len(dataset),
            "num_episodes": len(dataset.episode_ends),
            "stats": stats,
        }
    )

    # create dataloader
    dataloader = torch.utils.data.DataLoader(
        dataset,
        batch_size=config.batch_size,
        num_workers=config.dataloader_workers,
        shuffle=True,
        pin_memory=True,
        drop_last=False,
        persistent_workers=True,
    )

    # AdamW optimizer for noise_net
    opt_noise = torch.optim.AdamW(
        params=actor.parameters(),
        lr=config.actor_lr,
        weight_decay=config.weight_decay,
    )

    n_batches = len(dataloader)

    # Cosine LR schedule with linear warmup
    # lr_scheduler = get_scheduler(
    #     name=config.lr_scheduler_type,
    #     optimizer=opt_noise,
    #     num_warmup_steps=config.lr_scheduler_warmup_steps,
    #     num_training_steps=config.num_epochs,
    # )

    lr_scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer=opt_noise,
        max_lr=config.actor_lr,
        epochs=config.num_epochs,
        steps_per_epoch=n_batches,
        pct_start=config.lr_scheduler_pct_start,
    )

    tglobal = tqdm(range(config.num_epochs), desc="Epoch")
    best_success_rate = float("-inf")

    # epoch loop
    for epoch_idx in tglobal:
        epoch_loss = list()

        # batch loop
        tepoch = tqdm(dataloader, desc="Batch", leave=False, total=n_batches)
        for batch in tepoch:
            opt_noise.zero_grad()

            # device transfer
            batch = dict_apply(batch, lambda x: x.to(device, non_blocking=True))

            # Get loss
            loss = actor.compute_loss(batch)

            # backward pass
            # loss.backward()
            loss.backward()
            opt_noise.step()
            lr_scheduler.step()

            # Gradient clipping
            if config.clip_grad_norm:
                torch.nn.utils.clip_grad_norm_(actor.parameters(), max_norm=1)

            # logging
            loss_cpu = loss.item()
            epoch_loss.append(loss_cpu)
            wandb.log(
                dict(
                    lr=lr_scheduler.get_last_lr()[0],
                    batch_loss=loss_cpu,
                )
            )

            tepoch.set_postfix(loss=loss_cpu)
            if config.dryrun:
                break

        tepoch.close()

        tglobal.set_postfix(loss=np.mean(epoch_loss))
        wandb.log({"epoch_loss": np.mean(epoch_loss), "epoch": epoch_idx})

        if (
            config.rollout_every != -1
            and (epoch_idx + 1) % config.rollout_every == 0
            and np.mean(epoch_loss) < config.rollout_loss_threshold
        ):
            if env is None:
                env = get_env(
                    config.gpu_id,
                    obs_type=config.observation_type,
                    furniture=config.furniture,
                    num_envs=config.num_envs,
                    randomness=config.randomness,
                )

            # Perform a rollout with the current model
            success_rate = calculate_success_rate(
                env,
                actor,
                config,
                epoch_idx,
            )

            if success_rate > best_success_rate:
                best_success_rate = success_rate
                save_path = str(
                    model_save_dir / f"actor_{config.furniture}_{wandb.run.name}.pt"
                )
                torch.save(
                    actor.state_dict(),
                    save_path,
                )

                wandb.save(save_path)
                wandb.log({"best_success_rate": best_success_rate})

    tglobal.close()
    wandb.finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu-id", "-g", type=int, default=0)
    parser.add_argument("--batch-size", "-b", type=int, default=64)
    parser.add_argument("--dryrun", "-d", action="store_true")
    parser.add_argument("--cpus", "-c", type=int, default=24)
    parser.add_argument("--wb-mode", "-w", type=str, default="online")
    args = parser.parse_args()

    data_base_dir = Path(os.environ.get("FURNITURE_DATA_DIR", "data"))
    maybe = lambda x, fb=1: x if args.dryrun is False else fb

    n_workers = min(args.cpus, os.cpu_count())
    num_envs = 4

    config = ConfigDict(
        dict(
            action_horizon=8,
            actor_lr=1e-4,
            batch_size=args.batch_size,
            beta_schedule="squaredcos_cap_v2",
            clip_grad_norm=False,
            clip_sample=True,
            dataloader_workers=n_workers,
            demo_source="sim",
            down_dims=[512, 1024, 2048],
            dryrun=args.dryrun,
            furniture="one_leg",
            gpu_id=args.gpu_id,
            inference_steps=16,
            lr_scheduler_type="OneCycleLR",
            mixed_precision=False,
            n_rollouts=8 if args.dryrun is False else num_envs,
            num_diffusion_iters=100,
            num_envs=num_envs,
            num_epochs=100,
            obs_horizon=2,
            observation_type="image",
            pred_horizon=16,
            prediction_type="epsilon",
            randomness="high",
            rollout_every=5 if args.dryrun is False else 1,
            rollout_loss_threshold=1e9,
            rollout_max_steps=750 if args.dryrun is False else 10,
            vision_encoder_pretrained=False,
            vision_encoder="resnet18",
            weight_decay=1e-6,
            data_subset=None if args.dryrun is False else 10,
        )
    )

    config.lr_scheduler_pct_start = 0.1

    assert (
        config.n_rollouts % config.num_envs == 0
    ), "n_rollouts must be divisible by num_envs"

    config.datasim_path = data_base_dir / "processed/sim/image/one_leg/high/data.zarr"

    print(f"Using data from {config.datasim_path}")

    main(config)
