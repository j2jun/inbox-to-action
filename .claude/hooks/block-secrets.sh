#!/bin/sh
# PreToolUse hook: block shell commands that would print secrets into the transcript.
# Exit 2 = block the call; stderr is shown to Claude.
cmd=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))')
case "$cmd" in
  *.env*|*NOTION_TOKEN*|*ANTHROPIC_API_KEY*|*printenv*)
    echo "Blocked: command touches secrets (.env / API keys). Use the env var by name in code instead." >&2
    exit 2 ;;
esac
exit 0
