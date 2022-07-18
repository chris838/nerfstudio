# Copyright 2022 The Plenoptix Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Data loader.
"""

from abc import abstractmethod
import random
from typing import Dict, List, Tuple, Union
from omegaconf import ListConfig

from torchtyping import TensorType

from torch import nn

from pyrad.cameras.cameras import Camera, get_camera
from pyrad.cameras.rays import RayBundle
from pyrad.data.image_dataset import ImageDataset
from pyrad.data.image_sampler import ImageSampler
from pyrad.data.pixel_sampler import PixelSampler
from pyrad.data.structs import DatasetInputs, GenericDataContainer
from pyrad.data.utils import get_dataset_inputs_from_dataset_config
from pyrad.graphs.modules.ray_generator import RayGenerator
from pyrad.utils import profiler
from pyrad.utils.config import DataConfig
from pyrad.utils.misc import get_dict_to_torch, instantiate_from_dict_config


@profiler.time_function
def setup_dataset_train(config: DataConfig, device: str) -> Tuple[DatasetInputs, "TrainDataloader"]:
    """Helper method to load train dataset
    Args:
        config (DataConfig): Configuration of training dataset.
        device (str): device to load the dataset to

    Returns:
        Tuple[DatasetInputs, "TrainDataloader"]: returns both the dataset input information and associated dataloader
    """
    dataset_inputs_train = get_dataset_inputs_from_dataset_config(**config.dataset_inputs_train, split="train")
    # ImageDataset
    image_dataset_train = instantiate_from_dict_config(config.image_dataset_train, **dataset_inputs_train.as_dict())
    # ImageSampler
    image_sampler_train = instantiate_from_dict_config(
        config.dataloader_train.image_sampler, dataset=image_dataset_train, device=device
    )
    # PixelSampler
    pixel_sampler_train = instantiate_from_dict_config(config.dataloader_train.pixel_sampler)
    # Dataloader
    dataloader_train = TrainDataloader(image_sampler_train, pixel_sampler_train)
    return dataset_inputs_train, dataloader_train


@profiler.time_function
def setup_dataset_eval(config: DataConfig, test_mode: bool, device: str) -> Tuple[DatasetInputs, "EvalDataloader"]:
    """Helper method to load test or val dataset based on test/train mode
    Args:
        config (DataConfig): Configuration of training dataset.
        test_mode (bool): specifies whether you are training/testing mode, to load validation/test data
        device (str): device to load the dataset to

    Returns:
        Tuple[DatasetInputs, "TrainDataloader"]: returns both the dataset input information and associated dataloader
    """
    eval_split = "test" if test_mode else "val"
    dataset_inputs_eval = get_dataset_inputs_from_dataset_config(**config.dataset_inputs_eval, split=eval_split)
    image_dataset_eval = instantiate_from_dict_config(config.image_dataset_eval, **dataset_inputs_eval.as_dict())
    dataloader_eval = instantiate_from_dict_config(
        config.dataloader_eval,
        image_dataset=image_dataset_eval,
        device=device,
        **dataset_inputs_eval.as_dict(),
    )
    return dataset_inputs_eval, dataloader_eval


class IterableWrapper:
    """A helper that will allow an instance of a class to return multiple kinds of iterables bound to different functions of that class.

    To use this, pass in the instance of the class you want to have multiple kinds of iterables for, and pass in a
    method belonging to that class that you want to be the __next__() method of the iterable. The resulting instantiated object will
    be an iterable that will use the passed in class method as the __next__() method."""

    def __init__(self, instance: object, new_next: callable):
        """
        Args:
            instance (object): instance class we are wrapping
            new_next (callable): function that will be called instead as the __next__()
        """
        self.instance = instance
        self.new_next = new_next

    def __next__(self):
        return self.new_next(self.instance)


class AbstractDataloaderV2(nn.Module):
    """Second version of the dataloader class

    This version of the dataloader is designed to subsume both the train and eval dataloaders, especially since
    this may contain learnable parameters which need to be shared across the train and test dataloaders. The idea
    is that we have setup methods for train and eval separatley and this can be a combined train/eval if you want.


    Train Methods:
        setup_train: sets up for being used as train
        iter_train: returns an iterator of the train dataloader
        next_train: will be called on __next__() for the training iterator

    Eval Methods:
        setup_eval: sets up for being used as eval
        iter_eval: returns an iterator of the eval dataloader
        next_eval: will be called on __next__() for the eval iterator


    Args:
        image_sampler (ImageSampler): image sampler
        pixel_sampler (PixelSampler): pixel sampler
        ray_generator (RayGenerator): ray generator
        is_train (bool): whether this is being used for training
        is_eval (bool): whether this is being used for evaluation



    Attributes:
        image_sampler (ImageSampler): image sampler
        pixel_sampler (PixelSampler): pixel sampler
        ray_generator (RayGenerator): ray generator
        train_count (int): number of times train has been called
        eval_count (int): number of times eval has been called
    """

    def __init__(
        self,
        image_sampler: ImageSampler,
        pixel_sampler: PixelSampler,
        ray_generator: RayGenerator,
        is_train: bool,
        is_eval: bool,
    ):
        super().__init__()
        self.image_sampler = image_sampler
        self.pixel_sampler = pixel_sampler
        self.iter_image_sampler = iter(self.image_sampler)
        self.ray_generator = ray_generator
        self.is_train = is_train
        self.is_eval = is_eval
        self.train_count = 0
        self.eval_count = 0
        assert is_train or is_eval
        if is_train:
            self.setup_train()
        if is_eval:
            self.setup_eval()

    def iter_train(self) -> IterableWrapper:
        """Returns an iterator that executes the self.next_train function"""
        self.train_count = 0
        return IterableWrapper(self, self.next_train)

    def iter_eval(self) -> IterableWrapper:
        """Returns an iterator that executes the self.next_eval function"""
        self.eval_count = 0
        return IterableWrapper(self, self.next_eval)

    @abstractmethod
    def setup_train(self):
        """Sets up the dataloader for training"""

    @abstractmethod
    def setup_eval(self):
        """Sets up the dataloader for evaluation"""

    @abstractmethod
    def next_train(self) -> GenericDataContainer:
        """Returns the next batch of data from the train dataloader"""

    @abstractmethod
    def next_eval(self) -> GenericDataContainer:
        """Returns the next batch of data from the eval dataloader"""


class TrainDataloader:
    """Training dataloader base class."""

    def __init__(self, image_sampler: ImageSampler, pixel_sampler: PixelSampler):
        self.image_sampler = image_sampler
        self.pixel_sampler = pixel_sampler
        self.iter_image_sampler = iter(self.image_sampler)
        self.count = 0

    def __iter__(self):
        self.count = 0
        return self

    def __next__(self) -> Tuple[TensorType["num_rays", 3], Dict]:
        self.count += 1
        image_batch = next(self.iter_image_sampler)
        batch = self.pixel_sampler.sample(image_batch)
        ray_indices = batch["indices"]
        return ray_indices, batch


class EvalDataloader:  # pylint: disable=too-few-public-methods
    """Evaluation dataloader base class"""

    def __init__(
        self, image_dataset: ImageDataset, intrinsics, camera_to_world, num_rays_per_chunk: int, device="cpu", **kwargs
    ):
        super().__init__()
        self.image_dataset = image_dataset
        self.intrinsics = intrinsics
        self.camera_to_world = camera_to_world
        self.num_rays_per_chunk = num_rays_per_chunk
        self.device = device
        self.kwargs = kwargs

    def get_camera(self, image_idx) -> Camera:
        """Get camera for the given image index"""
        intrinsics = self.intrinsics[image_idx].to(self.device)
        camera_to_world = self.camera_to_world[image_idx].to(self.device)
        camera = get_camera(intrinsics, camera_to_world, camera_index=image_idx)
        return camera

    def get_data_from_image_idx(self, image_idx) -> Tuple[RayBundle, Dict]:
        """Returns the data for a specific image index."""
        camera = self.get_camera(image_idx)
        ray_bundle = camera.get_camera_ray_bundle(device=self.device)
        ray_bundle.num_rays_per_chunk = self.num_rays_per_chunk
        batch = self.image_dataset[image_idx]
        batch = get_dict_to_torch(batch, device=self.device)
        return ray_bundle, batch


class FixedIndicesEvalDataloader(EvalDataloader):
    """Dataloader that returns a fixed set of indices."""

    def __init__(
        self,
        image_dataset: ImageDataset,
        intrinsics,
        camera_to_world,
        num_rays_per_chunk: int,
        image_indices: Union[List[int], ListConfig],
        device="cpu",
        **kwargs,
    ):
        super().__init__(image_dataset, intrinsics, camera_to_world, num_rays_per_chunk, device, **kwargs)
        self.image_indices = image_indices
        self.num_rays_per_chunk = num_rays_per_chunk
        self.count = 0

    def __iter__(self):
        self.count = 0
        return self

    def __next__(self):
        if self.count < len(self.image_indices):
            image_idx = self.image_indices[self.count]
            ray_bundle, batch = self.get_data_from_image_idx(image_idx)
            ray_bundle.num_rays_per_chunk = self.num_rays_per_chunk
            self.count += 1
            return ray_bundle, batch
        raise StopIteration


class RandIndicesEvalDataloader(EvalDataloader):
    """Dataloader that returns random images."""

    def __init__(
        self, image_dataset: ImageDataset, intrinsics, camera_to_world, num_rays_per_chunk: int, device="cpu", **kwargs
    ):
        super().__init__(image_dataset, intrinsics, camera_to_world, num_rays_per_chunk, device, **kwargs)
        self.count = 0

    def __iter__(self):
        self.count = 0
        return self

    def __next__(self):
        if self.count < 1:
            image_indices = range(len(self.camera_to_world))
            image_idx = random.choice(image_indices)
            ray_bundle, batch = self.get_data_from_image_idx(image_idx)
            self.count += 1
            return ray_bundle, batch
        raise StopIteration
