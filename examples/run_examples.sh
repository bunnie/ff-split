#!/bin/bash
# Run several examples end to end: each name below is handed to
# run_example.sh in turn.  Comment out entries to run a subset.
#
#   counter   3-FF up-counter
#   const_d   constant D input
#   vecmul    4-bit vector DFFs
#   shift     direct flop-to-flop connection
#   twoclk    two independent clock domains
set -e
cd "$(dirname "$0")"

# To run a subset, comment lines out with '#', e.g.:  # vecmul
EXAMPLES=(
  counter
  const_d
  vecmul
  shift
  twoclk
)

failed=()
for design in "${EXAMPLES[@]}"; do
  if ! ./run_example.sh "$design"; then
    failed+=("$design")
  fi
done

if ((${#failed[@]})); then
  echo "FAILED: ${failed[*]}" >&2
  exit 1
fi

echo "all examples OK"
