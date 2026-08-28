"""Monitor de disponibilidade de passagens da EFVM."""

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus, EFVMClient
from efvm_monitor.config import Settings

__all__ = ["AvailabilityResult", "AvailabilityStatus", "EFVMClient", "Settings"]
__version__ = "0.4.1"
