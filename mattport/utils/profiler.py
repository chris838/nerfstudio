"""
Profiler base class and functionality
"""
import sys
import logging
import time
from typing import Callable

from omegaconf import DictConfig

from mattport.utils.decorators import check_main_thread, check_profiler_enabled, decorate_all

PROFILER = None


def time_function(func: Callable) -> Callable:
    """Decorator: time a function call"""

    def wrapper(*args, **kwargs):
        start = time.time()
        ret = func(*args, **kwargs)
        vals = vars(sys.modules[func.__module__])
        class_str = ""
        for attr in func.__qualname__.split(".")[:-1]:
            class_str += f"{vals[attr].__qualname__}_"
        class_str += func.__name__
        PROFILER.update_time(class_str, start, time.time())
        return ret

    return wrapper


@decorate_all([check_main_thread, check_profiler_enabled])
class Profiler:
    """Profiler class"""

    def __init__(self, config: DictConfig, is_main_thread: bool):
        self.config = config
        if self.config.debug.enable_profiler:
            self.is_main_thread = is_main_thread
        self.profiler_dict = {}

    def update_time(self, func_name: str, start_time: float, end_time: float):
        """update the profiler dictionary with running averages of durations

        Args:
            func_name (str): the function name that is being profiled
            start_time (float): the start time when function is called
            end_time (float): the end time when function terminated
        """
        val = end_time - start_time
        func_dict = self.profiler_dict.get(func_name, {"val": 0, "step": 0})
        prev_val = func_dict["val"]
        prev_step = func_dict["step"]
        self.profiler_dict[func_name] = {"val": (prev_val * prev_step + val) / (prev_step + 1), "step": prev_step + 1}

    def print_profile(self):
        """helper to print out the profiler stats"""
        logging.info("Printing profiling stats, from longest to shortest duration in seconds")
        sorted_keys = [k for k, _ in sorted(self.profiler_dict.items(), key=lambda item: item[1]["val"], reverse=True)]
        for k in sorted_keys:
            val = f"{self.profiler_dict[k]['val']:0.4f}"
            print(f"{k:<20}: {val:<20}")
