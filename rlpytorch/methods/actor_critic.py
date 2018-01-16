# Copyright (c) 2017-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory.

import torch
import torch.nn as nn
from torch.autograd import Variable
import math

from ..args_provider import ArgsProvider
from .policy_gradient import PolicyGradient
from .discounted_reward import DiscountedReward
from .value_matcher import ValueMatcher
from .utils import add_err

# Actor critic model.
class ActorCritic:
    ''' An actor critic model '''
    def __init__(self):
        ''' Initialization of `PolicyGradient`, `DiscountedReward` and `ValueMatcher`.
        Initialize the arguments needed (num_games, batchsize, value_node) and in child_providers.
        '''
        self.pg = PolicyGradient()
        self.discounted_reward = DiscountedReward()
        self.value_matcher = ValueMatcher()

        self.args = ArgsProvider(
            call_from = self,
            define_args = [
            ],
            more_args = ["num_games", "batchsize", "value_node"],
            child_providers = [ self.pg.args, self.discounted_reward.args, self.value_matcher.args ],
        )

    def update(self, mi, batch, stats):
        ''' Actor critic model update.
        Feed stats for later summarization.

        Args:
            mi(`ModelInterface`): mode interface used
            batch(dict): batch of data. Keys in a batch:
                ``s``: state,
                ``r``: immediate reward,
                ``terminal``: if game is terminated
            stats(`Stats`): Feed stats for later summarization.
        '''
        m = mi["model"]
        args = self.args
        value_node = self.args.value_node

        T = batch["s"].size(0)

        state_curr = m(batch.hist(T - 1))
        isCuda = state_curr[value_node].is_cuda
        cudaify = lambda x: x.cuda() if isCuda else x

        self.discounted_reward.setR(state_curr[value_node].squeeze().data, stats)

        tot_err = None

        for t in range(T - 2, -1, -1):
            bht = batch.hist(t)
            state_curr = m.forward(bht)

            # go through the sample and get the rewards.
            V = state_curr[value_node].squeeze()

            R = self.discounted_reward.feed(
                dict(r=cudaify(batch["r"][t]), terminal=batch["terminal"][t]),
                stats=stats)

            err = None
            policy_err = self.pg.feed(R-V.data, state_curr, bht, stats, old_pi_s=bht)
            err = add_err(err, policy_err)
            err = add_err(err, self.value_matcher.feed({ value_node: V, "target" : R}, stats))
            err.backward()
            if tot_err is None:
                tot_err = torch.zeros_like(err.data)
            tot_err += err.data

        stats["cost"].feed(tot_err[0] / (T - 1))
