import furniture_bench
import torch

import collections

import numpy as np
from tqdm import tqdm, trange
from ipdb import set_trace as bp
from furniture_bench.envs.furniture_sim_env import FurnitureSimEnv
from src.gym import get_env

from src.behavior.actor import DoubleImageActor


import wandb


def rollout(
    env: FurnitureSimEnv,
    actor: DoubleImageActor,
    rollout_max_steps: int,
    pbar=True,
):
    # get first observation
    obs = env.reset()

    obs_horizon = actor.obs_horizon
    action_horizon = actor.action_horizon

    # keep a queue of last 2 steps of observations
    obs_deque = collections.deque(
        [obs] * obs_horizon,
        maxlen=obs_horizon,
    )

    # save visualization and rewards
    imgs1 = [obs["color_image1"].cpu()]
    imgs2 = [obs["color_image2"].cpu()]
    rewards = list()
    done = torch.BoolTensor([[False]] * env.num_envs)
    step_idx = 0
    sum_reward = 0

    pbar = tqdm(
        total=rollout_max_steps,
        desc="Eval OneLeg State Env",
        disable=not pbar,
    )
    while not done.all():
        # Get the next actions from the actor
        action_pred = actor.action(obs_deque)

        # only take action_horizon number of actions
        start = obs_horizon - 1
        end = start + action_horizon
        action = action_pred[:, start:end, :]
        # (action_horizon, action_dim)

        # execute action_horizon number of steps
        # without replanning
        for i in range(action.shape[1]):
            # stepping env
            masked_action = action[:, i, :].clone()
            masked_action[done.repeat((1, actor.action_dim))] = 0
            obs, reward, done, _ = env.step(masked_action)

            # save observations
            obs_deque.append(obs)

            # and reward/vis
            rewards.append(reward.cpu())
            imgs1.append(obs["color_image1"].cpu())
            imgs2.append(obs["color_image2"].cpu())

            # update progress bar
            step_idx += 1
            sum_reward
            pbar.update(1)
            pbar.set_postfix(reward=sum_reward)
            if step_idx >= rollout_max_steps:
                done = torch.BoolTensor([[True]] * env.num_envs)

            if done.all():
                break

    return (
        torch.cat(rewards, dim=1),
        torch.stack(imgs1).transpose(0, 1),
        torch.stack(imgs2).transpose(0, 1),
    )


@torch.no_grad()
def calculate_success_rate(
    env: FurnitureSimEnv,
    actor: DoubleImageActor,
    n_rollouts: int,
    rollout_max_steps: int,
    epoch_idx: int,
):
    tbl = wandb.Table(columns=["rollout", "success", "epoch", "steps_to_success"])
    pbar = trange(
        n_rollouts,
        desc="Performing rollouts",
        postfix=dict(success=0),
        leave=False,
    )
    n_success = 0
    all_rewards = list()
    all_imgs1 = list()
    all_imgs2 = list()

    for _ in range(n_rollouts // env.num_envs):
        # Perform a rollout with the current model
        rewards, imgs1, imgs2 = rollout(
            env,
            actor,
            rollout_max_steps,
            pbar=False,
        )

        # Calculate the success rate
        success = rewards.sum(dim=1) > 0
        n_success += success.sum().item()

        # Save the results from the rollout
        all_rewards.extend(rewards)
        all_imgs1.extend(imgs1)
        all_imgs2.extend(imgs2)

        # Update progress bar
        pbar.update(env.num_envs)
        pbar.set_postfix(success=n_success)

    for rollout_idx in range(n_rollouts):
        # Get the rewards and images for this rollout
        rewards = all_rewards[rollout_idx].numpy()
        video1 = all_imgs1[rollout_idx].numpy()
        video2 = all_imgs2[rollout_idx].numpy()

        # Stack the two videoes side by side into a single video
        # and swap the axes from (T, H, W, C) to (T, C, H, W)
        video = np.concatenate([video1, video2], axis=2).transpose(0, 3, 1, 2)
        success = (rewards.sum() > 0).item()

        # Measure how many steps the agent took before it succeeded
        # or failed
        if success:
            success_idx = np.where(rewards > 0)[0][0]
        else:
            success_idx = video.shape[0]

        tbl.add_data(wandb.Video(video, fps=10), success, epoch_idx, success_idx)

    # Log the videos to wandb table
    wandb.log(
        {
            "rollouts": tbl,
            "epoch": epoch_idx,
        }
    )

    # Log the success rate to wandb
    wandb.log({"success_rate": n_success / n_rollouts, "epoch": epoch_idx})
    pbar.close()

    return n_success / n_rollouts


def do_rollout_evaluation(config, env, model_save_dir, actor, best_success_rate, epoch_idx) -> float:
    # Perform a rollout with the current model
    success_rate = calculate_success_rate(
        env,
        actor,
        n_rollouts=config.rollout.count,
        rollout_max_steps=config.rollout.max_steps,
        epoch_idx=epoch_idx,
    )

    if success_rate > best_success_rate:
        best_success_rate = success_rate
        save_path = str(model_save_dir / f"actor_best.pt")
        torch.save(
            actor.state_dict(),
            save_path,
        )

        wandb.save(save_path)
        wandb.log({"best_success_rate": best_success_rate})

    # Checkpoint the model
    save_path = str(model_save_dir / f"actor_{epoch_idx}.pt")
    torch.save(
        actor.state_dict(),
        save_path,
    )

    return best_success_rate
