# %% Imports
import os

os.environ["ACCELERATE_DISABLE_RICH"] = "1"
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

from dataclasses import dataclass
from typing import Optional, Union, List
import numpy as np
import gym
import gym.spaces
import gym.envs.registration
import plotly.express as px
import plotly.graph_objects as go
from tqdm import tqdm, trange
import sys
import time
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Union, Tuple
import torch as t
from torch import nn, Tensor
from gym.spaces import Discrete, Box
from numpy.random import Generator
import pandas as pd
import wandb
import pandas as pd
from pathlib import Path
from jaxtyping import Float, Int, Bool
import pytorch_lightning as pl
from pytorch_lightning.loggers import WandbLogger, CSVLogger

Arr = np.ndarray

# Make sure exercises are in the path
chapter = r"chapter2_rl"
exercises_dir = Path(f"{os.getcwd().split(chapter)[0]}/{chapter}/exercises").resolve()
section_dir = exercises_dir / "part2_dqn"
if str(exercises_dir) not in sys.path:
    sys.path.append(str(exercises_dir))

from part1_intro_to_rl.utils import make_env
from part1_intro_to_rl.solutions import Environment, Toy, Norvig, find_optimal_policy
import part2_dqn.utils as utils
import part2_dqn.tests as tests
from plotly_utils import line, cliffwalk_imshow, plot_cartpole_obs_and_dones

device = t.device("cuda" if t.cuda.is_available() else "cpu")

MAIN = __name__ == "__main__"
# %% DiscreteEnviroGym

ObsType = int
ActType = int


class DiscreteEnviroGym(gym.Env):
    action_space: gym.spaces.Discrete
    observation_space: gym.spaces.Discrete

    def __init__(self, env: Environment):
        super().__init__()
        self.env = env
        self.observation_space = gym.spaces.Discrete(env.num_states)
        self.action_space = gym.spaces.Discrete(env.num_actions)
        self.reset()

    def step(self, action: ActType) -> Tuple[ObsType, float, bool, dict]:
        """
        Samples from the underlying dynamics of the environment
        """
        (states, rewards, probs) = self.env.dynamics(self.pos, action)
        idx = self.np_random.choice(len(states), p=probs)
        (new_state, reward) = (states[idx], rewards[idx])
        self.pos = new_state
        done = self.pos in self.env.terminal
        return (new_state, reward, done, {"env": self.env})

    def reset(
        self, seed: Optional[int] = None, return_info=False, options=None
    ) -> Union[ObsType, Tuple[ObsType, dict]]:
        super().reset(seed=seed)
        self.pos = self.env.start
        return (self.pos, {"env": self.env}) if return_info else self.pos

    def render(self, mode="human"):
        assert mode == "human", f"Mode {mode} not supported!"


# %%  Register gym environments

gym.envs.registration.register(
    id="NorvigGrid-v0",
    entry_point=DiscreteEnviroGym,
    max_episode_steps=100,
    nondeterministic=True,
    kwargs={"env": Norvig(penalty=-0.04)},
)

gym.envs.registration.register(
    id="ToyGym-v0",
    entry_point=DiscreteEnviroGym,
    max_episode_steps=2,
    nondeterministic=False,
    kwargs={"env": Toy()},
)


# %% Base agents and random agents


@dataclass
class Experience:
    """A class for storing one piece of experience during an episode run"""

    obs: ObsType
    act: ActType
    reward: float
    new_obs: ObsType
    new_act: Optional[ActType] = None


@dataclass
class AgentConfig:
    """Hyperparameters for agents"""

    epsilon: float = 0.1
    lr: float = 0.05
    optimism: float = 0


defaultConfig = AgentConfig()


class Agent:
    """Base class for agents interacting with an environment (you do not need to add any implementation here)"""

    rng: np.random.Generator

    def __init__(
        self,
        env: DiscreteEnviroGym,
        config: AgentConfig = defaultConfig,
        gamma: float = 0.99,
        seed: int = 0,
    ):
        self.env = env
        self.reset(seed)
        self.config = config
        self.gamma = gamma
        self.num_actions = env.action_space.n
        self.num_states = env.observation_space.n
        self.name = type(self).__name__

    def get_action(self, obs: ObsType) -> ActType:
        raise NotImplementedError()

    def observe(self, exp: Experience) -> None:
        """
        Agent observes experience, and updates model as appropriate.
        Implementation depends on type of agent.
        """
        pass

    def reset(self, seed: int) -> None:
        self.rng = np.random.default_rng(seed)

    def run_episode(self, seed) -> List[int]:
        """
        Simulates one episode of interaction, agent learns as appropriate
        Inputs:
            seed : Seed for the random number generator
        Outputs:
            The rewards obtained during the episode
        """
        rewards = []
        obs = self.env.reset(seed=seed)
        self.reset(seed=seed)
        done = False
        while not done:
            act = self.get_action(obs)
            (new_obs, reward, done, info) = self.env.step(act)
            exp = Experience(obs, act, reward, new_obs)
            self.observe(exp)
            rewards.append(reward)
            obs = new_obs
        return rewards

    def train(self, n_runs=500):
        """
        Run a batch of episodes, and return the total reward obtained per episode
        Inputs:
            n_runs : The number of episodes to simulate
        Outputs:
            The discounted sum of rewards obtained for each episode
        """
        all_rewards = []
        for seed in trange(n_runs):
            rewards = self.run_episode(seed)
            all_rewards.append(utils.sum_rewards(rewards, self.gamma))
        return all_rewards


class Random(Agent):
    def get_action(self, obs: ObsType) -> ActType:
        return self.rng.integers(0, self.num_actions)


# %% Cheater agent


class Cheater(Agent):
    def __init__(
        self,
        env: DiscreteEnviroGym,
        config: AgentConfig = defaultConfig,
        gamma=0.99,
        seed=0,
    ):
        super().__init__(env, config, gamma, seed)
        self.optimal_policy: np.ndarray = find_optimal_policy(
            env_toy.unwrapped.env, self.gamma
        )

    def get_action(self, obs):
        """Returns the optimal action for the given state"""
        return self.optimal_policy[obs]


env_toy = gym.make("ToyGym-v0")
agents_toy: List[Agent] = [Cheater(env_toy), Random(env_toy)]
returns_list = []
names_list = []
for agent in agents_toy:
    returns = agent.train(n_runs=100)
    returns_list.append(utils.cummean(returns))
    names_list.append(agent.name)

line(returns_list, names=names_list, title=f"Avg. reward on {env_toy.spec.name}")
# %%


class Cheater(Agent):
    def __init__(
        self,
        env: DiscreteEnviroGym,
        config: AgentConfig = defaultConfig,
        gamma=0.99,
        seed=0,
    ):
        super().__init__(env, config, gamma, seed)
        self.optimal_policy: np.ndarray = find_optimal_policy(
            self.env.unwrapped.env, self.gamma
        )

    def get_action(self, obs):
        """Returns the optimal action for the given state"""
        return self.optimal_policy[obs]


class EpsilonGreedy(Agent):
    """
    A class for SARSA and Q-Learning to inherit from.
    """

    def __init__(
        self,
        env: DiscreteEnviroGym,
        config: AgentConfig = defaultConfig,
        gamma: float = 0.99,
        seed: int = 0,
    ):
        super().__init__(env, config, gamma, seed)
        self.Q = np.zeros((self.num_states, self.num_actions)) + self.config.optimism
        self.N = np.zeros((self.num_states, self.num_actions))

    def get_action(self, obs: ObsType) -> ActType:
        """
        Selects an action using epsilon-greedy with respect to Q-value estimates
        """
        epsilon = self.config.epsilon  # agentConfig
        if self.rng.random() < epsilon:
            return self.rng.integers(0, self.num_actions)
        else:
            return np.argmax(self.Q[obs])


class QLearning(EpsilonGreedy):
    def observe(self, exp: Experience) -> None:
        state = exp.obs
        action = exp.act
        reward = exp.reward
        next_state = exp.new_obs

        update_delta = np.max(self.Q[next_state]) - self.Q[state, action]

        self.Q[state, action] = self.Q[state, action] + self.config.lr * (
            reward + self.gamma * update_delta - self.Q[state, action]
        )


class SARSA(EpsilonGreedy):
    def observe(self, exp: Experience):
        state = exp.obs
        action = exp.act
        reward = exp.reward
        next_state = exp.new_obs
        next_action = exp.new_act

        self.Q[state, action] = self.Q[state, action] + self.config.lr * (
            reward
            + (self.gamma * self.Q[next_state, next_action])
            - self.Q[state, action]
        )

    def run_episode(self, seed) -> List[int]:
        # Regular episode does not have next_action, need to store it
        rewards = []
        obs = self.env.reset(seed=seed)
        self.reset(seed=seed)
        done = False
        while not done:
            act = self.get_action(obs)
            (new_obs, reward, done, info) = self.env.step(act)
            new_act = self.get_action(new_obs)
            exp = Experience(obs, act, reward, new_obs, new_act)
            self.observe(exp)
            rewards.append(reward)
            obs = new_obs
            act = new_act
        return rewards


n_runs = 1000
gamma = 0.99
seed = 1
env_norvig = gym.make("NorvigGrid-v0")
config_norvig = AgentConfig()
args_norvig = (env_norvig, config_norvig, gamma, seed)
agents_norvig: List[Agent] = [
    Cheater(*args_norvig),
    QLearning(*args_norvig),
    SARSA(*args_norvig),
    Random(*args_norvig),
]
returns_norvig = {}
fig = go.Figure(
    layout=dict(
        title_text=f"Avg. reward on {env_norvig.spec.name}",
        template="simple_white",
        xaxis_range=[-30, n_runs + 30],
    )
)
for agent in agents_norvig:
    returns = agent.train(n_runs)
    fig.add_trace(go.Scatter(y=utils.cummean(returns), name=agent.name))
fig.show()
# %%

gamma = 1
seed = 0

config_cliff = AgentConfig(epsilon=0.1, lr=0.1, optimism=0)
env = gym.make("CliffWalking-v0")
n_runs = 2500
args_cliff = (env, config_cliff, gamma, seed)

returns_list = []
name_list = []
agents: List[Union[QLearning, SARSA]] = [QLearning(*args_cliff), SARSA(*args_cliff)]

for agent in agents:
    returns = agent.train(n_runs)[1:]
    returns_list.append(utils.cummean(returns))
    name_list.append(agent.name)
    V = agent.Q.max(axis=-1).reshape(4, 12)
    pi = agent.Q.argmax(axis=-1).reshape(4, 12)
    cliffwalk_imshow(V, pi, title=f"CliffWalking: {agent.name} Agent")

line(
    returns_list,
    names=name_list,
    template="simple_white",
    title="Q-Learning vs SARSA on CliffWalking-v0",
    labels={"x": "Episode", "y": "Avg. reward", "variable": "Agent"},
)
# %% DEEP Q LEARNING


class QNetwork(nn.Module):
    """For consistency with your tests, please wrap your modules in a `nn.Sequential` called `layers`."""

    layers: nn.Sequential

    def __init__(
        self,
        dim_observation: int,
        num_actions: int,
        hidden_sizes: List[int] = [120, 84],
    ):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(in_features=dim_observation, out_features=hidden_sizes[0]),
            nn.ReLU(),
            nn.Linear(in_features=hidden_sizes[0], out_features=hidden_sizes[1]),
            nn.ReLU(),
            nn.Linear(in_features=hidden_sizes[1], out_features=num_actions),
        )

    def forward(self, x: t.Tensor) -> t.Tensor:
        return self.layers(x)


net = QNetwork(dim_observation=4, num_actions=2)
n_params = sum((p.nelement() for p in net.parameters()))
assert isinstance(getattr(net, "layers", None), nn.Sequential)
print(net)
print(f"Total number of parameters: {n_params}")
print("You should manually verify network is Linear-ReLU-Linear-ReLU-Linear")
assert n_params == 10934
# %%


@dataclass
class ReplayBufferSamples:
    """
    Samples from the replay buffer, converted to PyTorch for use in neural network training.
    """

    observations: Float[Tensor, "sampleSize *obsShape"]
    actions: Int[Tensor, "sampleSize"]
    rewards: Float[Tensor, "sampleSize"]
    dones: Bool[Tensor, "sampleSize"]
    next_observations: Float[Tensor, "sampleSize *obsShape"]


class ReplayBuffer:
    """
    Contains buffer; has a method to sample from it to return a ReplayBufferSamples object.
    """

    rng: Generator
    observations: t.Tensor
    actions: t.Tensor
    rewards: t.Tensor
    dones: t.Tensor
    next_observations: t.Tensor

    def __init__(
        self, buffer_size: int, num_environments: int, seed: int, obs_shape=(4,)
    ):
        assert (
            num_environments == 1
        ), "This buffer only supports SyncVectorEnv with 1 environment inside."
        self.num_environments = num_environments
        obs_dtype = t.float32
        self.rng = np.random.default_rng(seed=seed)
        self.buffer_size = buffer_size
        self.observations = t.zeros((buffer_size, *obs_shape), dtype=obs_dtype)
        self.actions = t.zeros((buffer_size,), dtype=t.int64, device=device)
        self.rewards = t.zeros((buffer_size,), dtype=float, device=device)
        self.dones = t.zeros((buffer_size,), dtype=bool, device=device)
        self.next_observations = t.zeros(
            (buffer_size, *obs_shape), dtype=obs_dtype, device=device
        )
        self.next_pos = 0
        self.capacity = 0
        self.buffer_components = (
            self.observations,
            self.actions,
            self.rewards,
            self.dones,
            self.next_observations,
        )

    def add(
        self,
        obs: np.ndarray,
        actions: np.ndarray,
        rewards: np.ndarray,
        dones: np.ndarray,
        next_obs: np.ndarray,
    ) -> None:
        """
        obs: shape (num_environments, *observation_shape)
            Observation before the action
        actions: shape (num_environments,)
            Action chosen by the agent
        rewards: shape (num_environments,)
            Reward after the action
        dones: shape (num_environments,)
            If True, the episode ended and was reset automatically
        next_obs: shape (num_environments, *observation_shape)
            Observation after the action
            If done is True, this should be the terminal observation, NOT the first observation of the next episode.
        """
        assert obs.shape[0] == self.num_environments
        assert actions.shape == (self.num_environments,)
        assert rewards.shape == (self.num_environments,)
        assert dones.shape == (self.num_environments,)
        assert next_obs.shape[0] == self.num_environments

        self.observations[self.next_pos] = t.tensor(obs)
        self.actions[self.next_pos] = t.tensor(actions)
        self.rewards[self.next_pos] = t.tensor(rewards)
        self.dones[self.next_pos] = t.tensor(dones)
        self.next_observations[self.next_pos] = t.tensor(next_obs)

        self.capacity += 1
        self.capacity = min(self.capacity, self.buffer_size)
        self.next_pos = (self.next_pos + 1) % self.buffer_size

    def sample(self, sample_size: int, device: t.device) -> ReplayBufferSamples:
        """
        Uniformly sample sample_size entries from the buffer and convert them to PyTorch tensors on device.
        Sampling is with replacement, and sample_size may be larger than the buffer size.
        """
        idxs = self.rng.choice(self.capacity, size=sample_size)
        result = ReplayBufferSamples(
            observations=self.observations[idxs].to(device),
            actions=self.actions[idxs].to(device),
            rewards=self.rewards[idxs].to(device),
            dones=self.dones[idxs].to(device),
            next_observations=self.next_observations[idxs].to(device),
        )
        return result


tests.test_replay_buffer_single(ReplayBuffer)
tests.test_replay_buffer_deterministic(ReplayBuffer)
tests.test_replay_buffer_wraparound(ReplayBuffer)
# %%
rb = ReplayBuffer(buffer_size=256, num_environments=1, seed=0)
envs = gym.vector.SyncVectorEnv([make_env("CartPole-v1", 0, 0, False, "test")])
obs = envs.reset()
for i in range(256):
    actions = np.array([0])
    (next_obs, rewards, dones, infos) = envs.step(actions)
    real_next_obs = next_obs.copy()
    for (i, done) in enumerate(dones):
        if done:
            real_next_obs[i] = infos[i]["terminal_observation"]
    rb.add(obs, actions, rewards, dones, next_obs)
    obs = next_obs


plot_cartpole_obs_and_dones(rb.observations.flip(0), rb.dones.flip(0))

sample = rb.sample(256, t.device("cpu"))
plot_cartpole_obs_and_dones(sample.observations.flip(0), sample.dones.flip(0))

# %%
