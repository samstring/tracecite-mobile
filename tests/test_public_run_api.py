from tracecite_mobile import device_api
from tracecite_mobile.device_api import DeviceRef, ScreenCapability, get_backend
from tracecite_mobile.run import CommandRun
from tracecite_mobile.shared.command_run import CommandRun as InternalCommandRun


def test_public_run_api_exposes_command_run() -> None:
    assert CommandRun is InternalCommandRun


def test_public_device_api_exposes_extension_contracts() -> None:
    assert DeviceRef.__name__ == "DeviceRef"
    assert callable(get_backend)


def test_public_device_api_hides_transport_names() -> None:
    forbidden = ("adb", "devicectl", "xctrace", "perfetto", "instruments")
    public_names = set(device_api.__all__) | {
        name for name in dir(device_api) if not name.startswith("_")
    }
    assert not any(
        any(token in name.lower() for token in forbidden) for name in public_names
    )


def test_screen_protocol_is_independent_of_platform_backend() -> None:
    assert ScreenCapability.__module__ == "tracecite_mobile.ui_api"
    assert "ScreenCapability" in device_api.__all__
