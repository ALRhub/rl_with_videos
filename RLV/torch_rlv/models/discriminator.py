import os
import torch as T
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim


class DiscriminatorNetwork(nn.Module):

    def __init__(self, input_dims, beta=0.00000003, fc1_dims=64, fc2_dims=64, fc3_dims=64, name='discriminator',
                 chkpt_dir='tmp/discriminator'):
        super(DiscriminatorNetwork, self).__init__()
        self.beta = beta
        self.input_dims = input_dims
        self.output_dims = 1
        self.fc1_dims = fc1_dims
        self.fc2_dims = fc2_dims
        self.fc3_dims = fc3_dims
        self.name = name
        self.checkpoint_dir = chkpt_dir
        self.checkpoint_file = os.path.join(self.checkpoint_dir, name + '_inverse')

        self.fc1 = nn.Linear(self.input_dims, self.fc1_dims)
        self.fc2 = nn.Linear(self.fc1_dims, self.fc2_dims)
        self.fc3 = nn.Linear(self.fc2_dims, self.fc3_dims)
        self.q = nn.Linear(self.fc3_dims, output_dims)
        self.optimizer = optim.Adam(self.parameters(), lr=beta)
        self.device = T.device('cuda:0' if T.cuda.is_available() else 'cpu')

    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = F.relu(self.fc3(x))
        q = T.Sigmoid(self.q(x))
        return q

    def save_checkpoint(self):
        T.save(self.state_dict(), self.checkpoint_file)

    def load_checkpoint(self):
        self.load_state_dict(T.load(self.checkpoint_file))
