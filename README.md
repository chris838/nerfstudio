<p align="center">
    <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/_static/imgs/logo-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/_static/imgs/logo.png">
    <img alt="nerfactory" src="docs/_static/imgs/logo.png" width="400">
    </picture>
</p>

<p align="center"> The all-in-one repo for NeRFs </p>

<p align="center">
    <a href='https://plenoptix-nerfactory.readthedocs-hosted.com/en/latest/?badge=latest'>
        <img src='https://readthedocs.com/projects/plenoptix-nerfactory/badge/?version=latest&token=2c5ba6bdd52600523fa8a8513170ae7170fd927a8c9dfbcf7c03af7ede551f96' alt='Documentation Status' />
    </a>
    <!-- TODO: add license and have it point to that -->
    <a href="https://github.com/plenoptix/nerfactory/blob/master/LICENSE">
        <img alt="Documentation Status" src="https://img.shields.io/badge/License-Apache_2.0-blue.svg">
    </a>
    <!-- TODO: add version number badge -->
    <a href="https://badge.fury.io/py/nerfactory"><img src="https://badge.fury.io/py/nerfactory.svg" alt="PyPI version" height="18"></a>
</p>

- [Quickstart](#quickstart)
- [Supported Features](#supported-features)
- [Benchmarked Model Architectures](#benchmarked-model-architectures)

# Quickstart

The quickstart will help you get started with the default vanilla nerf trained on the classic blender lego scene.
For more complex changes (e.g. running with your own data/ setting up a new NeRF graph, please see our [docs](https://plenoptix-nerfactory.readthedocs-hosted.com/en/latest/quickstart/quick_tour.html).

#### 1. Installation: Setup the environment

This repository is tested with CUDA 11.3. Make sure to install [Conda](https://docs.conda.io/en/latest/miniconda.html#linux-installers) before preceding.

<details>
<summary>Installing Conda</summary>

    This step is fairly self-explanatory, but here are the basic steps. You can also find countless tutorials online.

    ```
    cd /path/to/install/miniconda

    mkdir -p miniconda3
    wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O miniconda3/miniconda.sh
    bash miniconda3/miniconda.sh -b -u -p miniconda3
    rm -rf miniconda/miniconda.sh
    ```

</details>

```
# Create the python environment
conda create --name nerfactory python=3.8.13
conda activate nerfactory

# Clone the repo
git clone git@github.com:plenoptix/nerfactory.git

# Install dependencies
cd nerfactory
pip install -r environment/requirements.txt

# Install nerfactory as a library
pip install -e .

# Install tiny-cuda-nn (tcnn) and apex to use with the graph_instant_ngp.yaml config
pip install git+https://github.com/NVlabs/tiny-cuda-nn/#subdirectory=bindings/torch

# Run the test cases
pytest tests
```

#### 2. Getting the data

Download the original [NeRF dataset](https://drive.google.com/drive/folders/128yBriW1IG_3NJ5Rp7APSTZsJqdJdfc1) and unfold it in the following format. This is for the blender dataset type. We support the major datasets and allow users to create their own dataset, described in detail [here](docs/tutorials/data_setup.rst).

```
|─ nerfactory/
   ├─ data/
   |  ├─ blender/
   |     ├─ fern/
   |     ├─ lego/
         ...
      |- <dataset_format>/
         |- <scene>
         ...
```

#### 3. Training a model

To run with all the defaults, e.g. vanilla nerf method with the blender lego image

```
# To see what models are available.
python scripts/run_train.py --help

# Run a vanilla nerf model.
python scripts/run_train.py vanilla_nerf

# Run a faster version with instant ngp using tcnn (without the viewer).
python scripts/run_train.py instant_ngp
```

#### 3.x Training a model with the viewer

Make sure to forward a port for the websocket to localhost. The default port is 7007, which you should be expose to localhost:7007.

```bash
# with the default port
python scripts/run_train.py instant_ngp --viewer.enable

# with a specified websocket port
python scripts/run_train.py instant_ngp --viewer.enable --viewer.websocket-port=7008

# with the viewer bridge server as a separate process
# in one terminal, start the bridge server:
viewer-bridge-server # or equivalently, python scripts/run_viewer_bridge_server.py
# in another terminal, start training:
python scripts/run_train.py instant_ngp --viewer.enable --viewer.no-launch-bridge-server
```

#### 4. Visualizing training runs

We support multiple methods to visualize training, the default configuration uses Tensorboard. More information on logging can be found [here](https://plenoptix-nerfactory.readthedocs-hosted.com/en/latest/tooling/logging.html).

<details>
<summary>Real-time Viewer</summary>

We have developed our own Real-time web viewer, more information can be found [here](https://plenoptix-nerfactory.readthedocs-hosted.com/en/latest/tooling/viewer.html). This viewer runs during training and is designed to work with models that have fast rendering pipelines.

</details>

<details>
<summary>Tensorboard</summary>

If you run everything with the default configuration we log all training curves, test images, and other stats. Once the job is launched, you will be able to track training by launching the tensorboard in `outputs/blender_lego/vanilla_nerf/<timestamp>/<events.tfevents>`.

```bash
tensorboard --logdir outputs
```

</details>

<details>
<summary>Weights & Biases</summary>

We support logging to weights and biases, to enable add the following to the config:

```
logging:
    writer:
        WandbWriter
```

</details>

#### 5. Rendering a trajectories during inference

```
python scripts/run_eval.py render-trajectory --load-config=outputs/blender_lego/instant_ngp/2022-07-07_230905/config.yml--traj=spiral --output-path=output.mp4
```

#### 6. In-depth guide

For a more in-depth tutorial on how to modify/implement your own NeRF Graph, please see our [walk-through](https://plenoptix-nerfactory.readthedocs-hosted.com/en/latest/tutorials/creating_graphs.html).

# Supported Features

We provide the following support strucutures to make life easier for getting started with NeRFs. For a full description, please refer to our [features page](#).

If you are looking for a feature that is not currently supported, please do not hesitate to contact the Plenoptix team!

#### :metal: Support for [Hydra](https://hydra.cc/) config structure

#### :metal: Support for multiple logging interfaces

#### :metal: Built-in support for profiling code

#### :metal: Benchmarking scripts

#### :metal: Speed up your code with Tiny Cuda NN

# Benchmarked Model Architectures

| Method                                                                            | PSNR                     |
| --------------------------------------------------------------------------------- | ------------------------ |
| [NeRF](https://arxiv.org/abs/2003.08934)                                          | :hourglass_flowing_sand: |
| [instant NGP](https://nvlabs.github.io/instant-ngp/assets/mueller2022instant.pdf) | :hourglass_flowing_sand: |
| [Mip NeRF](https://arxiv.org/abs/2103.13415)                                      | :hourglass_flowing_sand: |
