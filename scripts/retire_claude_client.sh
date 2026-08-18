#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/retire_claude_client.sh
  ./scripts/retire_claude_client.sh --restore BACKUP_DIRECTORY

The default action moves only known Claude configuration/credential files to a
timestamped backup. It never removes ~/.claude/projects or history files.

Optional:
  DSV4_CLIENT_HOME=/alternate/home
EOF
}

client_home=${DSV4_CLIENT_HOME:-${HOME:?HOME is required}}

history_count() {
  local projects_dir="$client_home/.claude/projects"
  if [[ -d "$projects_dir" ]]; then
    find "$projects_dir" -type f -name '*.jsonl' -print 2>/dev/null | wc -l
  else
    printf '0\n'
  fi
}

restore_backup() {
  local backup_dir=$1
  [[ "$backup_dir" == /* ]] || {
    printf 'Backup directory must be an absolute path: %s\n' "$backup_dir" >&2
    exit 2
  }
  [[ -d "$backup_dir/files" ]] || {
    printf 'Not a Claude config backup: %s\n' "$backup_dir" >&2
    exit 2
  }

  local conflicts=0 restored=0 source_file relative target
  while IFS= read -r -d '' source_file; do
    relative=${source_file#"$backup_dir/files/"}
    target="$client_home/$relative"
    if [[ -e "$target" || -L "$target" ]]; then
      printf 'CONFLICT: refusing to overwrite %s\n' "$target" >&2
      conflicts=$((conflicts + 1))
      continue
    fi
    mkdir -p "$(dirname -- "$target")"
    mv -- "$source_file" "$target"
    printf 'RESTORED: %s\n' "$target"
    restored=$((restored + 1))
  done < <(find "$backup_dir/files" \( -type f -o -type l \) -print0)

  printf 'RESTORED_FILES=%s\nCONFLICTS=%s\n' "$restored" "$conflicts"
  ((conflicts == 0))
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
  --restore)
    [[ $# -eq 2 ]] || {
      usage >&2
      exit 2
    }
    restore_backup "$2"
    exit
    ;;
  "") ;;
  *)
    usage >&2
    exit 2
    ;;
esac

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_root="$client_home/.claude-config-backups/$timestamp"
backup_files="$backup_root/files"
mkdir -p "$backup_files"

history_before=$(history_count)
history_json_before=NO
[[ -f "$client_home/.claude/history.jsonl" ]] && history_json_before=YES

config_paths=(
  "$client_home/.claude.json"
  "$client_home/.claude/settings.json"
  "$client_home/.claude/settings.local.json"
  "$client_home/.claude/.credentials.json"
  "$client_home/.claude/config.json"
  "$client_home/.claude/mcp.json"
  "$client_home/.config/claude/settings.json"
  "$client_home/.config/claude/config.json"
  "$client_home/.config/claude-code/settings.json"
  "$client_home/.config/claude-code/config.json"
)

moved=0
for source_path in "${config_paths[@]}"; do
  [[ -f "$source_path" || -L "$source_path" ]] || continue
  relative=${source_path#"$client_home/"}
  target_path="$backup_files/$relative"
  mkdir -p "$(dirname -- "$target_path")"
  mv -- "$source_path" "$target_path"
  printf 'MOVED_CONFIG: %s\n' "$source_path"
  moved=$((moved + 1))
done

history_after=$(history_count)
history_json_after=NO
[[ -f "$client_home/.claude/history.jsonl" ]] && history_json_after=YES

{
  printf 'backup_created_utc=%s\n' "$timestamp"
  printf 'client_home=%s\n' "$client_home"
  printf 'moved_config_files=%s\n' "$moved"
  printf 'project_jsonl_before=%s\n' "$history_before"
  printf 'project_jsonl_after=%s\n' "$history_after"
  printf 'history_jsonl_before=%s\n' "$history_json_before"
  printf 'history_jsonl_after=%s\n' "$history_json_after"
  printf '\nPreserved paths (never selected as move targets):\n'
  printf '%s\n' \
    "$client_home/.claude/projects" \
    "$client_home/.claude/history.jsonl" \
    "$client_home/.claude/file-history" \
    "$client_home/.claude/session-env" \
    "$client_home/.claude/todos"
} >"$backup_root/PRESERVED-HISTORY.txt"

if [[ "$history_before" != "$history_after" || "$history_json_before" != "$history_json_after" ]]; then
  printf 'ERROR: Claude history inventory changed unexpectedly; inspect %s\n' "$backup_root" >&2
  exit 1
fi

printf '\nCLAUDE_ACTIVE_CONFIG_RETIRED=YES\n'
printf 'MOVED_CONFIG_FILES=%s\n' "$moved"
printf 'CLAUDE_PROJECT_JSONL_PRESERVED=%s\n' "$history_after"
printf 'CLAUDE_HISTORY_JSONL_PRESERVED=%s\n' "$history_json_after"
printf 'RECOVERABLE_BACKUP=%s\n' "$backup_root"

rc_hits=0
for rc_file in \
  "$client_home/.bashrc" \
  "$client_home/.bash_profile" \
  "$client_home/.profile" \
  "$client_home/.zshrc"; do
  [[ -f "$rc_file" ]] || continue
  matches=$(grep -nE '(^|[[:space:]])(export[[:space:]]+)?(ANTHROPIC_[A-Z0-9_]+|CLAUDE_CODE_[A-Z0-9_]+)=' "$rc_file" || true)
  if [[ -n "$matches" ]]; then
    printf '\nPERSISTENT_ENV_REVIEW_REQUIRED: %s\n%s\n' "$rc_file" "$matches"
    rc_hits=$((rc_hits + 1))
  fi
done

if ((rc_hits > 0)); then
  printf '\nThe shell startup files above were not modified. Remove only the listed old variables manually.\n'
fi
printf 'Run the unset command from CODEX-CLI-GUIDE.md in the current shell.\n'
