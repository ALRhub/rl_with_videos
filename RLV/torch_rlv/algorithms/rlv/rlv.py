from typing import Any, Dict, List, Optional, Tuple, Type, Union
import gym
import os

import numpy as np

import torch as th
from torch.nn import functional as F

from RLV.torch_rlv.buffer.type_aliases import GymEnv, MaybeCallback, Schedule

import wandb
import pickle
import torch.optim as optim
from RLV.torch_rlv.models.inverse_model_network import InverseModelNetwork
from RLV.torch_rlv.buffer.buffers import DictReplayBuffer, ReplayBuffer
from RLV.torch_rlv.algorithms.sac.sac import SAC
from stable_baselines3.common.noise import ActionNoise
from stable_baselines3.common.noise import NormalActionNoise
from RLV.torch_rlv.buffer.type_aliases import ReplayBufferSamples
from RLV.torch_rlv.data.human_data.adapter import Adapter
from RLV.torch_rlv.data.sac_data.adapter_sac_data import AdapterSAC
from RLV.torch_rlv.algorithms.sac.softactorcritic import SoftActorCritic, SaveOnBestTrainingRewardCallback
from RLV.torch_rlv.visualizer.plot import plot_learning_curve, plot_env_step, animate_env_obs
from datetime import datetime
from stable_baselines3.common.utils import polyak_update


class RLV(SAC):
    def __init__(self, warmup_steps=1500, beta_inverse_model=0.0003, env_name='acrobot_continuous', policy='MlpPolicy',
                 env=None, learning_rate=0.0003, buffer_size=1000000, learning_starts=1000, batch_size=256, tau=0.005,
                 gamma=0.99, train_freq=1, gradient_steps=1, optimize_memory_usage=False, ent_coef='auto',
                 target_update_interval=1, target_entropy='auto', initial_exploration_steps=1000, wandb_log=False,
                 domain_shift=False, domain_shift_generator_weight=0.01,
                 domain_shift_discriminator_weight=0.01, paired_loss_scale=1.0,
                 action_noise: Optional[ActionNoise] = None,
                 replay_buffer_class: Optional[ReplayBuffer] = None,
                 replay_buffer_kwargs: Optional[Dict[str, Any]] = None,
                 use_sde: bool = False,
                 sde_sample_freq: int = -1,
                 use_sde_at_warmup: bool = False,
                 tensorboard_log: Optional[str] = None,
                 create_eval_env: bool = False,
                 policy_kwargs: Dict[str, Any] = None,
                 verbose: int = 0,
                 seed: Optional[int] = None,
                 device: Union[th.device, str] = "auto",
                 _init_setup_model: bool = True,
                 ):
        super(RLV, self).__init__(
            policy=policy,
            env=env,
            learning_rate=learning_rate,
            buffer_size=buffer_size,
            learning_starts=learning_starts,
            batch_size=batch_size,
            tau=tau,
            gamma=gamma,
            train_freq=train_freq,
            target_entropy=target_entropy,
            gradient_steps=gradient_steps,
            action_noise=action_noise,
            replay_buffer_class=replay_buffer_class,
            replay_buffer_kwargs=replay_buffer_kwargs,
            policy_kwargs=policy_kwargs,
            tensorboard_log=tensorboard_log,
            verbose=verbose,
            device=device,
            create_eval_env=create_eval_env,
            seed=seed,
            use_sde=use_sde,
            sde_sample_freq=sde_sample_freq,
            use_sde_at_warmup=use_sde_at_warmup,
            optimize_memory_usage=optimize_memory_usage,
        )

        self.wandb_log = wandb_log
        self.inverse_model_loss = 0
        self.warmup_steps = warmup_steps
        self.beta_inverse_model = beta_inverse_model

        self.domain_shift = domain_shift
        self.domain_shift_discrim_lr = 3e-4
        self.paired_loss_lr = 3e-4
        self.paired_loss_scale = paired_loss_scale

        self.domain_shift = domain_shift
        self.domain_shift_generator_weight = domain_shift_generator_weight
        self.domain_shift_discriminator_weight = domain_shift_discriminator_weight

        self.initial_exploration_steps = initial_exploration_steps

        self.env_name = env_name

        if 'multi_world' in self.env_name:
            self.n_actions = env.action_space.shape[0]
        else:
            self.n_actions = env.action_space.shape[-1]

        self.inverse_model = InverseModelNetwork(beta=beta_inverse_model,
                                                 input_dims=env.observation_space.shape[-1] * 2,
                                                 output_dims=env.action_space.shape[-1],
                                                 fc1_dims=64, fc2_dims=64, fc3_dims=64)

        self.action_free_replay_buffer = ReplayBuffer(
            buffer_size=buffer_size, observation_space=env.observation_space,
            action_space=env.action_space, device='cpu', n_envs=1,
            optimize_memory_usage=optimize_memory_usage, handle_timeout_termination=False)
        self.training_ops = {}

    def fill_action_free_buffer(self, human_data=False, num_steps=200000, sac=None):
        if human_data:
            data = Adapter(data_type='unpaired', env_name='Acrobot')
            observations = data.observations
            next_observations = data.next_observations
            actions = data.actions
            rewards = data.rewards
            terminals = data.terminals

            for i in range(0, observations.shape[0]):
                self.action_free_replay_buffer.add(
                    obs=observations[i],
                    next_obs=next_observations[i],
                    action=actions[i],
                    reward=rewards[i],
                    done=terminals[i],
                    infos={'': ''}
                )
        else:
            if sac is not None:
                print('Training done')

                data = {'observations': sac.replay_buffer.observations, 'actions': sac.replay_buffer.actions,
                        'next_observations': sac.replay_buffer.next_observations, 'rewards': sac.replay_buffer.rewards,
                        'terminals': sac.replay_buffer.dones}

                with open(f"../data/sac_data/data_from_sac_trained_for_{num_steps}_steps.pickle", 'wb') \
                        as handle:
                    pickle.dump(data, handle, protocol=pickle.HIGHEST_PROTOCOL)

                self.action_free_replay_buffer = sac.replay_buffer
            else:
                data = Adapter(env_name='Acrobot')
                observations = data.observations
                next_observations = data.next_observations
                actions = data.actions
                rewards = data.rewards
                terminals = data.terminals

                for i in range(0, observations.shape[0]):
                    self.action_free_replay_buffer.add(
                        obs=observations[i],
                        next_obs=next_observations[i],
                        action=actions[i],
                        reward=rewards[i],
                        done=terminals[i],
                        infos={'': ''}
                    )

    def set_reward(self, reward_obs):
        if self.env_name == 'acrobot_continuous':
            if reward_obs > -1:
                return 10
            else:
                return -1
        elif self.env_name == 'franca_robot':
            return 100 #TODO implement reward function for robot in simulation framework
        else:
            # reward for mujoco environments
            if reward_obs > -1:
                return 10
            else:
                return 0

    def warmup_inverse_model(self):
        "Loss inverse model:"
        for x in range(0, self.warmup_steps):
            state_obs, target_action, next_state_obs, _, done_obs \
                = self.action_free_replay_buffer.sample(batch_size=self.batch_size)

            input_inverse_model = th.cat((state_obs, next_state_obs), dim=1)
            action_obs = self.inverse_model.forward(input_inverse_model)

            self.inverse_model_loss = self.inverse_model.criterion(action_obs, target_action)

            self.inverse_model.optimizer.zero_grad()
            self.inverse_model_loss.backward()
            self.inverse_model.optimizer.step()

            if x % 100 == 0:
                print(f"Steps {x}, Loss: {self.inverse_model_loss.item()}")

    def train(self, gradient_steps: int, batch_size: int = 64) -> None:
        # Update optimizers learning rate
        global logging_parameters
        optimizers = [self.actor.optimizer, self.critic.optimizer]
        if self.ent_coef_optimizer is not None:
            optimizers += [self.ent_coef_optimizer]

        # Update learning rate according to lr schedule
        self._update_learning_rate(optimizers)

        ent_coef_losses, ent_coefs = [], []
        actor_losses, critic_losses = [], []

        for gradient_step in range(gradient_steps):
            state_obs, target_action, next_state_obs, _, done_obs \
                = self.action_free_replay_buffer.sample(batch_size=self.batch_size)

            # get predicted action from inverse model
            input_inverse_model = th.cat((state_obs, next_state_obs), dim=1)
            action_obs = self.inverse_model.forward(input_inverse_model)

            # Compute inverse model loss
            self.inverse_model_loss = self.inverse_model.criterion(action_obs, target_action)

            # TODO: Optimize Inverse Model
            # self.inverse_model.optimizer.zero_grad()
            # self.inverse_model_loss.backward()
            # self.inverse_model.optimizer.step()

            self.training_ops.update({'action_obs': action_obs})

            # set rewards for observational data
            reward_obs = th.zeros(self.batch_size, 1)
            for i in range(0, self.batch_size):
                reward_obs[i] = self.set_reward(reward_obs=reward_obs[i])

            # get robot data - sample from replay pool from the SAC model
            data_int = self.replay_buffer.sample(self.batch_size, env=self._vec_normalize_env)

            # replace the data used in SAC for each gradient steps by observational plus robot data
            replay_data = ReplayBufferSamples(
                observations=th.cat((data_int.observations, state_obs), dim=0),
                actions=th.cat((data_int.actions, action_obs), dim=0),
                next_observations=th.cat((data_int.next_observations, next_state_obs), dim=0),
                dones=th.cat((data_int.dones, done_obs), dim=0),
                rewards=th.cat((data_int.rewards, reward_obs), dim=0)
            )

            # We need to sample because `log_std` may have changed between two gradient steps
            if self.use_sde:
                self.actor.reset_noise()

            # Action by the current actor for the sampled state
            actions_pi, log_prob = self.actor.action_log_prob(replay_data.observations)
            log_prob = log_prob.reshape(-1, 1)

            ent_coef_loss = None
            if self.ent_coef_optimizer is not None:
                # Important: detach the variable from the graph
                # so we don't change it with other losses
                # see https://github.com/rail-berkeley/softlearning/issues/60
                ent_coef = th.exp(self.log_ent_coef.detach())
                ent_coef_loss = -(self.log_ent_coef * (log_prob + self.target_entropy).detach()).mean()
                ent_coef_losses.append(ent_coef_loss.item())
            else:
                ent_coef = self.ent_coef_tensor

            ent_coefs.append(ent_coef.item())

            # Optimize entropy coefficient, also called
            # entropy temperature or alpha in the paper
            if ent_coef_loss is not None:
                self.ent_coef_optimizer.zero_grad()
                ent_coef_loss.backward()
                self.ent_coef_optimizer.step()

            with th.no_grad():
                # Select action according to policy
                next_actions, next_log_prob = self.actor.action_log_prob(replay_data.next_observations)
                # Compute the next Q values: min over all critics targets
                next_q_values = th.cat(self.critic_target(replay_data.next_observations, next_actions), dim=1)
                next_q_values, _ = th.min(next_q_values, dim=1, keepdim=True)
                # add entropy term
                next_q_values = next_q_values - ent_coef * next_log_prob.reshape(-1, 1)
                # td error + entropy term
                target_q_values = replay_data.rewards + (1 - replay_data.dones) * self.gamma * next_q_values

            # Get current Q-values estimates for each critic network
            # using action from the replay buffer
            current_q_values = self.critic(replay_data.observations, replay_data.actions)

            # Compute critic loss
            critic_loss = 0.5 * sum([F.mse_loss(current_q, target_q_values) for current_q in current_q_values])
            critic_losses.append(critic_loss.item())

            # Optimize the critic
            self.critic.optimizer.zero_grad()
            critic_loss.backward()
            self.critic.optimizer.step()

            # Compute actor loss
            # Alternative: actor_loss = th.mean(log_prob - qf1_pi)
            # Mean over all critic networks
            q_values_pi = th.cat(self.critic.forward(replay_data.observations, actions_pi), dim=1)
            min_qf_pi, _ = th.min(q_values_pi, dim=1, keepdim=True)
            actor_loss = (ent_coef * log_prob - min_qf_pi).mean()
            actor_losses.append(actor_loss.item())

            # Optimize the actor
            self.actor.optimizer.zero_grad()
            actor_loss.backward()
            self.actor.optimizer.step()

            # Update target networks
            if gradient_step % self.target_update_interval == 0:
                polyak_update(self.critic.parameters(), self.critic_target.parameters(), self.tau)

        self._n_updates += gradient_steps

        if self.wandb_log:
            logging_parameters = {
                "train/n_updates": self._n_updates,
                "train/ent_coef": np.mean(ent_coefs),
                "train/actor_loss": np.mean(actor_losses),
                "train/critic_loss": np.mean(critic_losses),
            }

        self.logger.record("train/n_updates", self._n_updates, exclude="tensorboard")
        self.logger.record("train/ent_coef", np.mean(ent_coefs))
        self.logger.record("train/actor_loss", np.mean(actor_losses))
        self.logger.record("train/critic_loss", np.mean(critic_losses))
        self.logger.record("train/inverse_model_loss", self.inverse_model_loss.item())

        if len(ent_coef_losses) > 0:
            self.logger.record("train/ent_coef_loss", np.mean(ent_coef_losses))
            if self.wandb_log:
                logging_parameters["train/ent_coef_loss"] = np.mean(ent_coef_losses)

        if self.wandb_log:
            wandb.log(logging_parameters)
