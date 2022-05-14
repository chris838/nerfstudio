"""
run_train_nerf.py
"""
import datetime
import logging
import random
import socket
from datetime import timedelta
import traceback
from typing import Any, Callable

import hydra
import numpy as np
import torch
import torch.distributed as dist
import torch.multiprocessing as mp
from omegaconf import DictConfig

from mattport.nerf.trainer import Trainer
from mattport.utils import comms, profiler

logging.basicConfig(format="[%(filename)s:%(lineno)d] %(message)s", level=logging.DEBUG)

DEFAULT_TIMEOUT = timedelta(minutes=30)

# speedup for when input size to model doesn't change (much)
torch.backends.cudnn.benchmark = True


def _find_free_port() -> str:
    """Find free port on open socket"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _set_random_seed(seed) -> None:
    """Set randomness seed in torch and numpy"""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _distributed_worker(
    local_rank: int,
    main_func: Callable,
    world_size: int,
    num_gpus_per_machine: int,
    machine_rank: int,
    dist_url: str,
    config: DictConfig,
    timeout: datetime = DEFAULT_TIMEOUT,
) -> Any:
    """Spawned distributed worker that handles the initialization of process group and handles the
       training process on multiple processes

    Args:
        local_rank (int): current rank of process
        main_func (Callable): function that will be called by the distributed workers
        world_size (int): total number of gpus available
        num_gpus_per_machine (int): number of GPUs per machine
        machine_rank (int): rank of this machine
        dist_url (str): url to connect to for distributed jobs, including protocol
                        e.g. "tcp://127.0.0.1:8686".
                        Can be set to "auto" to automatically select a free port on localhost
        config (DictConfig): config file specifying trainng regimen
        timeout (datetime, optional): timeout of the distributed workers Defaults to DEFAULT_TIMEOUT.

    Raises:
        e: Exception in initializing the process group

    Returns:
        Any: TODO(): determine the return type
    """
    assert torch.cuda.is_available(), "cuda is not available. Please check your installation."
    global_rank = machine_rank * num_gpus_per_machine + local_rank
    try:
        dist.init_process_group(
            backend="NCCL",
            init_method=dist_url,
            world_size=world_size,
            rank=global_rank,
            timeout=timeout,
        )
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error("Process group URL: %s", dist_url)
        raise e

    assert comms._LOCAL_PROCESS_GROUP is None
    num_machines = world_size // num_gpus_per_machine
    for i in range(num_machines):
        ranks_on_i = list(range(i * num_gpus_per_machine, (i + 1) * num_gpus_per_machine))
        pg = dist.new_group(ranks_on_i)
        if i == machine_rank:
            comms._LOCAL_PROCESS_GROUP = pg

    assert num_gpus_per_machine <= torch.cuda.device_count()
    torch.cuda.set_device(local_rank)
    _set_random_seed(1234 + local_rank)  # NOTE(ethan): will this work for multiple machines? should we use global_rank?
    comms.synchronize(world_size)

    output = main_func(local_rank, world_size, config)
    comms.synchronize(world_size)
    dist.destroy_process_group()
    return output


def _train(local_rank: int, world_size: int, config: DictConfig) -> Any:
    """Main training function that sets up and runs the trainer per process

    Args:
        local_rank (int): _description_
        world_size (int): _description_
        config (DictConfig): _description_

    Returns:
        Any: TODO(): determine the return type
    """
    trainer = Trainer(config, local_rank, world_size)
    trainer.setup()
    trainer.train()
    return 0


def launch(
    main_func: Callable,
    num_gpus_per_machine: int,
    num_machines: int = 1,
    machine_rank: int = 0,
    dist_url: str = "auto",
    config: DictConfig = None,
    timeout: datetime = DEFAULT_TIMEOUT,
) -> None:
    """Function that spawns muliple processes to call on main_func

    Args:
        main_func (Callable): function that will be called by the distributed workers
        num_gpus_per_machine (int): number of GPUs per machine
        num_machines (int, optional): total number of machines
        machine_rank (int, optional): rank of this machine. Defaults to 0.
        dist_url (str, optional): url to connect to for distributed jobs. Defaults to "auto".
        config (DictConfig, optional): config file specifying trainng regimen Defaults to None.
        timeout (datetime, optional): timeout of the distributed workers Defaults to DEFAULT_TIMEOUT.
    """
    world_size = num_machines * num_gpus_per_machine
    if world_size == 0:
        # Using only CPU and one process.
        main_func(local_rank=0, world_size=0, config=config)
    elif world_size == 1:
        # Using one gpu and one process.
        try:
            main_func(local_rank=0, world_size=1, config=config)
        except KeyboardInterrupt:
            print(traceback.format_exc())
        finally:
            profiler.flush_profiler(config.logging)
    elif world_size > 1:
        # Using multiple gpus with multiple processes.
        if dist_url == "auto":
            assert num_machines == 1, "dist_url=auto not supported in multi-machine jobs."
            port = _find_free_port()
            dist_url = f"tcp://127.0.0.1:{port}"
        if num_machines > 1 and dist_url.startswith("file://"):
            logger = logging.getLogger(__name__)
            logger.warning("file:// is not a reliable init_method in multi-machine jobs. Prefer tcp://")

        process_context = mp.spawn(
            _distributed_worker,
            nprocs=num_gpus_per_machine,
            args=(
                main_func,
                world_size,
                num_gpus_per_machine,
                machine_rank,
                dist_url,
                config,
                timeout,
            ),
            join=False,
        )
        try:
            process_context.join()
        except KeyboardInterrupt:
            logger = logging.getLogger(__name__)
            for i, process in enumerate(process_context.processes):
                if process.is_alive():
                    logger.info("Terminating process %s", str(i))
                    process.terminate()
                process.join()
                logger.info("Process %s finished", str(i))
        finally:
            profiler.flush_profiler(config.logging)


@hydra.main(config_path="../configs", config_name="default.yaml")
def main(config: DictConfig):
    """Main function."""
    launch(
        _train,
        config.machine_config.num_gpus,
        num_machines=config.machine_config.num_machines,
        machine_rank=config.machine_config.machine_rank,
        dist_url=config.machine_config.dist_url,
        config=config,
    )


if __name__ == "__main__":
    main()
