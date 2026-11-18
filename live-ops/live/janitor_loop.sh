#!/bin/bash
# Runs janitor.py every 10 min
while true; do python3 ~/.hermes/cache/scratch/live/janitor.py >/dev/null 2>&1; sleep 600; done
