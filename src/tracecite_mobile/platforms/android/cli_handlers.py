# -*- coding: utf-8 -*-
"""Android CLI compatibility handlers.

The public command root is now ``commands.device`` and dispatches every
platform through ``PlatformBackend``.  This module remains only as a
compatibility import path; ``android_dispatch`` forwards to the unified root
and is not used as the public composition root.
"""

from __future__ import annotations


def android_dispatch(args) -> int:
    """Compatibility entry point; delegate to the unified backend dispatcher.

    Older callers may still import this function, but it performs no platform
    orchestration and simply forwards to the public command root.
    """

    from ...commands.device import dispatch_device_command

    result = dispatch_device_command(args)
    return 1 if result is None else result
