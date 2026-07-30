"""Process-wide ownership for hardware shared by parallel GSS procedures."""

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass
class _ChannelReservation:
    setpoint: float
    owners: int = 1


@dataclass
class _SharedDevice:
    device: Any
    io_lock: threading.RLock = field(default_factory=threading.RLock)
    owners: int = 1
    channels: Dict[int, _ChannelReservation] = field(default_factory=dict)


class SharedDeviceLease:
    """Reference-counted lease on one physical instrument connection."""

    def __init__(self, registry, key: Tuple[str, str], shared: _SharedDevice):
        self._registry = registry
        self._key = key
        self._shared = shared
        self._closed = False
        self._reserved_channels = []

    @property
    def device(self):
        return self._shared.device

    @property
    def lock(self):
        return self._shared.io_lock

    def reserve_channel(self, channel: int, setpoint: float, tolerance: float) -> bool:
        """Reserve a channel, returning True when this is its first owner."""
        if self._closed:
            raise RuntimeError('Cannot reserve a channel on a closed lease')
        first_owner = self._registry._reserve_channel(
            self._key, channel, setpoint, tolerance
        )
        self._reserved_channels.append(channel)
        return first_owner

    def release_channel(self, channel: int, on_final: Optional[Callable[[], None]] = None) -> bool:
        """Release one reservation, returning True when no owners remain."""
        if channel not in self._reserved_channels:
            return False
        self._reserved_channels.remove(channel)
        return self._registry._release_channel(self._key, channel, on_final)

    def close(self):
        """Release the device reference after all channels were released."""
        if self._closed:
            return
        if self._reserved_channels:
            raise RuntimeError('Release all channel reservations before closing a lease')
        self._closed = True
        self._registry._release_device(self._key)


class ExclusiveResourceLease:
    """Lease proving that one logical resource has exactly one owner."""

    def __init__(self, registry, key: Tuple[str, str]):
        self._registry = registry
        self._key = key
        self._closed = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._registry._release_exclusive(self._key)


class SharedHardwareRegistry:
    """Owns one live driver object per physical resource within the process."""

    def __init__(self):
        self._lock = threading.RLock()
        self._devices: Dict[Tuple[str, str], _SharedDevice] = {}
        self._exclusive = set()

    def claim_exclusive(self, device_type: str, resource: str) -> ExclusiveResourceLease:
        key = (device_type, resource)
        with self._lock:
            if key in self._exclusive:
                raise ValueError(
                    f'{device_type.upper()} {resource} is already used by another running test'
                )
            self._exclusive.add(key)
        return ExclusiveResourceLease(self, key)

    def _release_exclusive(self, key: Tuple[str, str]):
        with self._lock:
            self._exclusive.remove(key)

    def acquire(
        self,
        device_type: str,
        resource: str,
        connect: Callable[[], Optional[Any]],
    ) -> Optional[SharedDeviceLease]:
        key = (device_type, resource)
        with self._lock:
            shared = self._devices.get(key)
            if shared is None:
                device = connect()
                if device is None:
                    return None
                shared = _SharedDevice(device=device)
                self._devices[key] = shared
            else:
                shared.owners += 1
            return SharedDeviceLease(self, key, shared)

    def _reserve_channel(
        self,
        key: Tuple[str, str],
        channel: int,
        setpoint: float,
        tolerance: float,
    ) -> bool:
        with self._lock:
            shared = self._devices[key]
            reservation = shared.channels.get(channel)
            if reservation is None:
                shared.channels[channel] = _ChannelReservation(setpoint=setpoint)
                return True
            if abs(reservation.setpoint - setpoint) > tolerance:
                kind, resource = key
                raise ValueError(
                    f'{kind.upper()} channel conflict on {resource} ch{channel}: '
                    f'{reservation.setpoint:g} requested previously, {setpoint:g} requested now'
                )
            reservation.owners += 1
            return False

    def _release_channel(
        self,
        key: Tuple[str, str],
        channel: int,
        on_final: Optional[Callable[[], None]],
    ) -> bool:
        with self._lock:
            shared = self._devices[key]
            reservation = shared.channels[channel]
            reservation.owners -= 1
            if reservation.owners == 0:
                with shared.io_lock:
                    try:
                        if on_final is not None:
                            on_final()
                    finally:
                        del shared.channels[channel]
                return True
            return False

    def _release_device(self, key: Tuple[str, str]):
        with self._lock:
            shared = self._devices[key]
            shared.owners -= 1
            if shared.owners > 0:
                return
            if shared.channels:
                raise RuntimeError('Cannot disconnect hardware while channels are reserved')
            with shared.io_lock:
                shared.device.disconnect()
            del self._devices[key]


shared_hardware = SharedHardwareRegistry()