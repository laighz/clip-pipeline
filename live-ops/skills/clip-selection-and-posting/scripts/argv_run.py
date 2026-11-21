"""Run an argument list. Separate from scripts that read local files first."""
import subprocess


def run(args):
    return subprocess.run(args, capture_output=True, text=True)
