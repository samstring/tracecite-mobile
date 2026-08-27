"""Optional, implementation-neutral screen and UI capability contracts.

The core platform backend deliberately stays small.  Screen inspection is an
optional extension that can be implemented by a backend without exposing the
transport used by a particular platform.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .platforms.models import DeviceRef


@runtime_checkable
class ScreenCapability(Protocol):
    """Screen/UI inspection operations shared by platform backends.

    ``dump_ui_hierarchy`` returns the source hierarchy text.  Consumers own
    parsing and evidence-file layout; the backend owns device communication.
    ``system_diagnostic`` is intentionally generic so UI tools do not need to
    know a platform's diagnostic command vocabulary.
    """

    platform: str

    def dump_ui_hierarchy(self, device: DeviceRef) -> str: ...

    def capture_screen(self, device: DeviceRef) -> bytes: ...

    def current_app(self, device: DeviceRef) -> str: ...

    def system_diagnostic(
        self,
        device: DeviceRef,
        *,
        kind: str,
        target: str = "",
    ) -> str: ...


__all__ = ["ScreenCapability"]
