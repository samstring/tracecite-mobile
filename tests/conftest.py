"""Explicitly host the Mobile extension for tests that exercise its formats."""

from tracecite.extension import register_extension
from tracecite_mobile.extension import EXTENSION


register_extension(EXTENSION)
