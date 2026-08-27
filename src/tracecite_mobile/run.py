"""Public run-manifest adapter for Mobile domain extensions.

Company extensions can use :class:`CommandRun` to put non-scenario artifacts
under the same immutable input, artifact, and manifest contract as Mobile CLI
commands without importing ``tracecite_mobile.shared`` internals.
"""

from .shared.command_run import CommandRun

__all__ = ["CommandRun"]
