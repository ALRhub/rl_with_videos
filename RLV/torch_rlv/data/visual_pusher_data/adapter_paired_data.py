import pickle
import pandas as pd
import os
import torch as T
import numpy as np
from PIL import Image


class AdapterPairedData:
    def __init__(self):
        current_directory = os.path.dirname(__file__)

        path = os.path.join(current_directory, 'paired_500000_SAC_steps_10000_samples.pickle')
        data = pickle.load(open(path, 'rb'))

        # get data
        observation_img = data['observation_img']
        observation_img_raw = data['observation_img_raw']

        # convert to numpy array
        observation_img = np.array(observation_img)
        observation_img_raw = np.array(observation_img_raw)

        # store data length
        self.n = len(observation_img)

        # numpy reshape operations
        observation_img = np.reshape(observation_img, (self.n, 3, 80, 80))
        observation_img_raw = np.reshape(observation_img_raw, (self.n, 3, 80, 80))

        # store tensors
        self.observation_img = T.from_numpy(observation_img)
        self.observation_img_raw = T.from_numpy(observation_img_raw)


if __name__ == '__main__':
    a = AdapterPairedData()

    print(a.observation_img_raw.shape)
    print(a.observation_img.shape)
