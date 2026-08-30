#!/bin/sh
case "$1" in
  Username*) printf '%s' "x-access-token" ;;
  *)         printf '%s' "$GITHUB_TOKEN" ;;
esac
