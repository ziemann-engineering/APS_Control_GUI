import pytest

from hardware.shared_resources import SharedHardwareRegistry


class FakeDevice:
    def __init__(self):
        self.disconnect_count = 0

    def disconnect(self):
        self.disconnect_count += 1


def test_shared_device_connects_once_and_disconnects_after_last_lease():
    registry = SharedHardwareRegistry()
    device = FakeDevice()
    connections = []

    def connect():
        connections.append(True)
        return device

    first = registry.acquire('psu', 'USB::1', connect)
    second = registry.acquire('psu', 'USB::1', connect)

    assert first.device is second.device
    assert first.lock is second.lock
    assert len(connections) == 1

    first.close()
    assert device.disconnect_count == 0
    second.close()
    assert device.disconnect_count == 1


def test_channel_is_released_only_after_final_owner():
    registry = SharedHardwareRegistry()
    first = registry.acquire('tcu', 'COM7', FakeDevice)
    second = registry.acquire('tcu', 'COM7', FakeDevice)

    assert first.reserve_channel(1, 125.0, tolerance=0.5)
    assert not second.reserve_channel(1, 125.2, tolerance=0.5)
    cleanup_calls = []
    assert not first.release_channel(1, lambda: cleanup_calls.append('first'))
    assert second.release_channel(1, lambda: cleanup_calls.append('final'))
    assert cleanup_calls == ['final']

    first.close()
    second.close()


def test_failed_channel_cleanup_does_not_strand_device_lease():
    registry = SharedHardwareRegistry()
    lease = registry.acquire('psu', 'USB::1', FakeDevice)
    lease.reserve_channel(1, 5.0, tolerance=0.01)

    with pytest.raises(RuntimeError, match='disable failed'):
        lease.release_channel(
            1,
            lambda: (_ for _ in ()).throw(RuntimeError('disable failed')),
        )

    lease.close()


def test_conflicting_channel_setpoint_is_rejected():
    registry = SharedHardwareRegistry()
    first = registry.acquire('psu', 'USB::1', FakeDevice)
    second = registry.acquire('psu', 'USB::1', FakeDevice)
    first.reserve_channel(2, 5.0, tolerance=0.01)

    with pytest.raises(ValueError, match='channel conflict'):
        second.reserve_channel(2, 12.0, tolerance=0.01)

    first.release_channel(2)
    first.close()
    second.close()


def test_exclusive_resource_rejects_second_owner_until_released():
    registry = SharedHardwareRegistry()
    first = registry.claim_exclusive('gss', 'COM4')

    with pytest.raises(ValueError, match='already used'):
        registry.claim_exclusive('gss', 'COM4')

    first.close()
    second = registry.claim_exclusive('gss', 'COM4')
    second.close()