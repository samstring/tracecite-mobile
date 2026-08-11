"""Explicitly host the Mobile extension for tests that exercise its formats."""

from tracecite.extension import ExtensionAPI
from tracecite_mobile.extension import register


register(ExtensionAPI())
