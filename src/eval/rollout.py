import furniture_bench
from omegaconf import DictConfig  # noqa: F401
import torch

import collections

import numpy as np
from tqdm import tqdm, trange
from ipdb import set_trace as bp  # noqa: F401
from furniture_bench.envs.furniture_sim_env import FurnitureSimEnv

from typing import Union

from src.behavior.base import Actor
from src.visualization.render_mp4 import create_in_memory_mp4
from src.common.context import suppress_all_output
from src.common.tasks import furniture2idx
from src.common.files import trajectory_save_dir
from src.data_collection.io import save_raw_rollout
from src.data_processing.utils import resize, resize_crop

import wandb


RolloutStats = collections.namedtuple(
    "RolloutStats",
    [
        "success_rate",
        "n_success",
        "n_rollouts",
        "epoch_idx",
        "rollout_max_steps",
        "total_return",
        "total_reward",
    ],
)


def rollout(
    env: FurnitureSimEnv,
    actor: Actor,
    rollout_max_steps: int,
    pbar: tqdm = None,
    resize_video: bool = True,
):
    # get first observation
    with suppress_all_output(True):
        obs = env.reset()

    if env.furniture_name == "lamp":
        # Before we start, let the environment settle by doing nothing for 1 second
        for _ in range(50):
            obs, reward, done, _ = env.step_noop()

    video_obs = obs.copy()

    # Resize the images in the observation
    obs["color_image1"] = resize(obs["color_image1"])
    obs["color_image2"] = resize_crop(obs["color_image2"])

    obs_horizon = actor.obs_horizon

    # keep a queue of last 2 steps of observations
    obs_deque = collections.deque(
        [obs] * obs_horizon,
        maxlen=obs_horizon,
    )

    if resize_video:
        video_obs["color_image1"] = resize(video_obs["color_image1"])
        video_obs["color_image2"] = resize_crop(video_obs["color_image2"])

    # save visualization and rewards
    robot_states = [video_obs["robot_state"].cpu()]
    imgs1 = [video_obs["color_image1"].cpu()]
    imgs2 = [video_obs["color_image2"].cpu()]
    parts_poses = [video_obs["parts_poses"].cpu()]
    actions = list()
    rewards = list()
    done = torch.zeros((env.num_envs, 1), dtype=torch.bool, device="cuda")

    step_idx = 0
    while not done.all():
        # Get the next actions from the actor
        action_pred = actor.action(obs_deque)

        obs, reward, done, _ = env.step(action_pred)

        video_obs = obs.copy()

        # Resize the images in the observation
        obs["color_image1"] = resize(obs["color_image1"])
        obs["color_image2"] = resize_crop(obs["color_image2"])

        # Save observations for the policy
        obs_deque.append(obs)

        if resize_video:
            video_obs["color_image1"] = resize(video_obs["color_image1"])
            video_obs["color_image2"] = resize_crop(video_obs["color_image2"])

        # Store the results for visualization and logging
        robot_states.append(video_obs["robot_state"].cpu())
        imgs1.append(video_obs["color_image1"].cpu())
        imgs2.append(video_obs["color_image2"].cpu())
        actions.append(action_pred.cpu())
        rewards.append(reward.cpu())
        parts_poses.append(video_obs["parts_poses"].cpu())

        # update progress bar
        step_idx += 1
        if pbar is not None:
            pbar.set_postfix(step=step_idx)
            pbar.update()

        if step_idx >= rollout_max_steps:
            done = torch.ones((env.num_envs, 1), dtype=torch.bool, device="cuda")

        if done.all():
            break

    return (
        torch.stack(robot_states, dim=1),
        torch.stack(imgs1, dim=1),
        torch.stack(imgs2, dim=1),
        torch.stack(actions, dim=1),
        # Using cat here removes the singleton dimension
        torch.cat(rewards, dim=1),
        torch.stack(parts_poses, dim=1),
    )


@torch.no_grad()
def calculate_success_rate(
    env: FurnitureSimEnv,
    actor: Actor,
    n_rollouts: int,
    rollout_max_steps: int,
    epoch_idx: int,
    gamma: float = 0.99,
    rollout_save_dir: Union[str, None] = None,
    save_failures: bool = False,
    n_parts_assemble: Union[int, None] = None,
    compress_pickles: bool = False,
    resize_video: bool = True,
) -> RolloutStats:
    def pbar_desc(self: tqdm, i: int, n_success: int):
        rnd = i + 1
        total = rnd * env.num_envs
        success_rate = n_success / total if total > 0 else 0
        self.set_description(
            f"Performing rollouts ({env.furniture_name}): round {rnd}/{n_rollouts//env.num_envs}, success: {n_success}/{total} ({success_rate:.1%})"
        )

    if n_parts_assemble is None:
        n_parts_assemble = len(env.furniture.should_be_assembled)

    tbl = wandb.Table(
        columns=["rollout", "success", "epoch", "reward", "return", "steps"]
    )
    pbar = trange(
        n_rollouts,
        desc="Performing rollouts",
        leave=False,
        total=rollout_max_steps * (n_rollouts // env.num_envs),
    )

    tqdm.pbar_desc = pbar_desc

    n_success = 0

    all_robot_states = list()
    all_imgs1 = list()
    all_imgs2 = list()
    all_actions = list()
    all_rewards = list()
    all_parts_poses = list()
    all_success = list()

    pbar.pbar_desc(0, n_success)
    for i in range(n_rollouts // env.num_envs):
        # Perform a rollout with the current model
        robot_states, imgs1, imgs2, actions, rewards, parts_poses = rollout(
            env,
            actor,
            rollout_max_steps,
            pbar=pbar,
            resize_video=resize_video,
        )

        # Calculate the success rate
        success = rewards.sum(dim=1) == n_parts_assemble
        n_success += success.sum().item()

        # Save the results from the rollout
        all_robot_states.extend(robot_states)
        all_imgs1.extend(imgs1)
        all_imgs2.extend(imgs2)
        all_actions.extend(actions)
        all_rewards.extend(rewards)
        all_parts_poses.extend(parts_poses)
        all_success.extend(success)

        # Update the progress bar
        pbar.pbar_desc(i, n_success)

    total_return = 0
    total_reward = 0
    table_rows = []
    for rollout_idx in trange(n_rollouts, desc="Saving rollouts", leave=False):
        # Get the rewards and images for this rollout
        robot_states = all_robot_states[rollout_idx].numpy()
        video1 = all_imgs1[rollout_idx].numpy()
        video2 = all_imgs2[rollout_idx].numpy()
        actions = all_actions[rollout_idx].numpy()
        rewards = all_rewards[rollout_idx].numpy()
        parts_poses = all_parts_poses[rollout_idx].numpy()
        success = all_success[rollout_idx].item()
        furniture = env.furniture_name

        # Number of steps until success, i.e., the index of the final reward received
        n_steps = np.where(rewards == 1)[0][-1] + 1 if success else rollout_max_steps

        # Stack the two videos side by side into a single video
        # and keep axes as (T, H, W, C) (and cut off after rollout reaches success)
        video = np.concatenate([video1, video2], axis=2)[:n_steps]
        video = create_in_memory_mp4(video, fps=20)

        # Calculate the reward and return for this rollout
        total_reward += np.sum(rewards)
        episode_return = np.sum(rewards * gamma ** np.arange(len(rewards)))
        total_return += episode_return

        table_rows.append(
            [
                wandb.Video(video, fps=20, format="mp4"),
                success,
                epoch_idx,
                np.sum(rewards),
                episode_return,
                n_steps,
            ]
        )

        if rollout_save_dir is not None and (save_failures or success):
            # Save the raw rollout data
            save_raw_rollout(
                robot_states=robot_states[: n_steps + 1],
                imgs1=video1[: n_steps + 1],
                imgs2=video2[: n_steps + 1],
                parts_poses=parts_poses[: n_steps + 1],
                actions=actions[:n_steps],
                rewards=rewards[:n_steps],
                success=success,
                furniture=furniture,
                action_type=env.action_type,
                rollout_save_dir=rollout_save_dir,
                compress_pickles=compress_pickles,
            )

    # Sort the table rows by return (highest at the top)
    table_rows = sorted(table_rows, key=lambda x: x[4], reverse=True)

    for row in table_rows:
        tbl.add_data(*row)

    # Log the videos to wandb table if a run is active
    if wandb.run is not None:
        wandb.log(
            {
                "rollouts": tbl,
                "epoch": epoch_idx,
                "epoch_mean_return": total_return / n_rollouts,
            }
        )

    pbar.close()

    return RolloutStats(
        success_rate=n_success / n_rollouts,
        n_success=n_success,
        n_rollouts=n_rollouts,
        epoch_idx=epoch_idx,
        rollout_max_steps=rollout_max_steps,
        total_return=total_return,
        total_reward=total_reward,
    )


def do_rollout_evaluation(
    config: DictConfig,
    env: FurnitureSimEnv,
    save_rollouts: bool,
    actor: Actor,
    best_success_rate: float,
    epoch_idx: int,
) -> float:
    rollout_save_dir = None

    if save_rollouts:
        rollout_save_dir = trajectory_save_dir(
            environment="sim",
            task=env.furniture_name,
            demo_source="rollout",
            randomness=config.randomness,
            # Don't create here because we have to do it when we save anyway
            create=False,
        )

    actor.set_task(furniture2idx[env.furniture_name])

    rollout_stats = calculate_success_rate(
        env,
        actor,
        n_rollouts=config.rollout.count,
        rollout_max_steps=config.rollout.max_steps,
        epoch_idx=epoch_idx,
        gamma=config.discount,
        rollout_save_dir=rollout_save_dir,
        save_failures=config.rollout.save_failures,
    )
    success_rate = rollout_stats.success_rate
    best_success_rate = max(best_success_rate, success_rate)

    # Log the success rate to wandb
    wandb.log(
        {
            "success_rate": success_rate,
            "best_success_rate": best_success_rate,
            "n_success": rollout_stats.n_success,
            "n_rollouts": rollout_stats.n_rollouts,
            "epoch": epoch_idx,
        }
    )

    return best_success_rate
