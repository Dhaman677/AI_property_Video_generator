#!/usr/bin/env sh
# One-command demo: renders the bundled sample listing to sample_output/demo.mp4
set -e
python -m pvg --input sample_data/property.json --output sample_output/demo.mp4
