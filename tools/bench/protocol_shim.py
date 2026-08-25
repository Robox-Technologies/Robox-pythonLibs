"""Access to the firmware's protocol module from the harness."""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "src")
)

import protocol  # noqa: E402


def normalise(text):
    return protocol.normalise_program(text)


def program_checksum(text):
    return protocol.program_checksum(text)


def new_pacer():
    return protocol.AdaptivePacer()
