#!/bin/bash
cd "$(dirname "$0")"

echo "👂 Claude Ears"
echo ""
echo "What word or phrase should I listen for?"
read -p "> " TERM

if [ -z "$TERM" ]; then
  echo "No term entered. Exiting."
  exit 1
fi

python3 claude-ears.py "$TERM"
