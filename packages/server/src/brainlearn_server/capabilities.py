"""Lightweight host capability inspection without importing scientific frameworks."""

import importlib.util
import os
import platform
import shutil
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict


class AcceleratorCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["cuda", "mps"]
    candidate_detected: bool
    runtime_validated: bool = False
    detail: str


class ContainerCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: Literal["docker", "podman"]
    candidate_detected: bool
    runtime_validated: bool = False


class SystemCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operating_system: str
    os_version: str
    architecture: str
    cpu_count: int | None
    python_version: str
    memory_bytes: int | None
    accelerators: list[AcceleratorCapability]
    containers: list[ContainerCapability]


def _memory_bytes() -> int | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return int(status.total_physical)
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        return int(page_size * pages)
    except (AttributeError, OSError, ValueError):
        return None


def inspect_system_capabilities() -> SystemCapabilities:
    """Inspect cheap host signals; never import torch or other optional heavy packages."""

    torch_installed = importlib.util.find_spec("torch") is not None
    nvidia_tool = shutil.which("nvidia-smi") is not None
    is_apple_silicon = sys.platform == "darwin" and platform.machine().lower() in {
        "arm64",
        "aarch64",
    }
    return SystemCapabilities(
        operating_system=platform.system(),
        os_version=platform.release(),
        architecture=platform.machine(),
        cpu_count=os.cpu_count(),
        python_version=platform.python_version(),
        memory_bytes=_memory_bytes(),
        accelerators=[
            AcceleratorCapability(
                backend="cuda",
                candidate_detected=torch_installed and nvidia_tool,
                detail=(
                    "PyTorch and the NVIDIA system tool were detected. "
                    "The CUDA runtime has not been validated."
                    if torch_installed and nvidia_tool
                    else "No CUDA candidate was detected; runtime validation was not attempted."
                ),
            ),
            AcceleratorCapability(
                backend="mps",
                candidate_detected=torch_installed and is_apple_silicon,
                detail=(
                    "PyTorch on Apple Silicon was detected. The MPS runtime has not been validated."
                    if torch_installed and is_apple_silicon
                    else "No MPS candidate was detected; runtime validation was not attempted."
                ),
            ),
        ],
        containers=[
            ContainerCapability(
                runtime="docker",
                candidate_detected=shutil.which("docker") is not None,
            ),
            ContainerCapability(
                runtime="podman",
                candidate_detected=shutil.which("podman") is not None,
            ),
        ],
    )
