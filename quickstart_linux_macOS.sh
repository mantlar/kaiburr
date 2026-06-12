#!/bin/bash

###############################################
# ALLOWS QUICK KAIBURR START WITHOUT INPUTS #
###############################################

# Save the current directory
original_dir=$(pwd)

# Run cleanup.sh with predefined input (Y)
echo "Y" | ./cleanup.sh

# Add a small delay before running cleanup.sh
sleep 1

# Change to the start/linux_macOS directory
cd "${original_dir}/start/linux_macOS"

# Run startDebugSilent.sh with predefined input (N)
echo "N" | ./startDebugSilent.sh

# Return to the original directory (kaiburr root)
cd "$original_dir"
