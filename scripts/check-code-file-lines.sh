#!/usr/bin/env sh
set -eu

LIMIT="${CODE_FILE_LINE_LIMIT:-500}"
EXCEPTIONS_FILE="${CODE_FILE_LINE_EXCEPTIONS:-scripts/code-file-line-exceptions.txt}"

usage() {
  cat <<'EOF'
Usage: scripts/check-code-file-lines.sh [--self-test]

Checks version-controlled and new working-tree business source files and fails
when a file exceeds the configured line limit without being listed in
scripts/code-file-line-exceptions.txt.

Environment:
  CODE_FILE_LINE_LIMIT       Override the default limit of 500 lines.
  CODE_FILE_LINE_EXCEPTIONS  Override the exception list path.
EOF
}

line_count() {
  wc -l < "$1" | tr -d ' '
}

is_business_source() {
  case "$1" in
    backend/app/*.py|backend/app/**/*.py) return 0 ;;
    frontend/src/*.vue|frontend/src/**/*.vue) return 0 ;;
    frontend/src/*.ts|frontend/src/**/*.ts) return 0 ;;
    frontend/src/*.js|frontend/src/**/*.js) return 0 ;;
    frontend/src/*.css|frontend/src/**/*.css) return 0 ;;
    scripts/*.sh) return 0 ;;
    *) return 1 ;;
  esac
}

clean_exceptions() {
  exception_file="$1"
  output_file="$2"

  if [ ! -f "$exception_file" ]; then
    : > "$output_file"
    return
  fi

  awk '
    {
      sub(/[[:space:]]*#.*/, "", $0)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0)
      if ($0 != "") print
    }
  ' "$exception_file" > "$output_file"
}

is_exception() {
  file="$1"
  exception_list="$2"
  grep -Fxq "$file" "$exception_list"
}

run_check() {
  repo_root="$1"
  exceptions_file="$2"
  limit="$3"

  tmp_exceptions="$(mktemp)"
  tmp_files="$(mktemp)"
  clean_exceptions "$repo_root/$exceptions_file" "$tmp_exceptions"
  git -C "$repo_root" ls-files --cached --others --exclude-standard > "$tmp_files"

  checked=0
  failures=0
  warnings=0

  while IFS= read -r file; do
    if [ ! -f "$repo_root/$file" ]; then
      continue
    fi

    if ! is_business_source "$file"; then
      continue
    fi

    checked=$((checked + 1))
    count="$(line_count "$repo_root/$file")"

    if [ "$count" -le "$limit" ]; then
      continue
    fi

    if is_exception "$file" "$tmp_exceptions"; then
      warnings=$((warnings + 1))
      echo "warning: $file has $count lines (> $limit), allowed by exception list" >&2
    else
      failures=$((failures + 1))
      echo "error: $file has $count lines (> $limit). Refactor it or add a justified exception." >&2
    fi
  done < "$tmp_files"

  rm -f "$tmp_exceptions" "$tmp_files"

  if [ "$failures" -gt 0 ]; then
    echo "Code file line check failed: $failures file(s) exceed $limit lines without an exception." >&2
    return 1
  fi

  echo "Code file line check passed: $checked business source file(s) checked, $warnings exception warning(s)."
}

write_lines() {
  output_file="$1"
  lines="$2"
  i=0
  : > "$output_file"
  while [ "$i" -lt "$lines" ]; do
    printf 'x\n' >> "$output_file"
    i=$((i + 1))
  done
}

self_test() {
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT INT TERM

  mkdir -p "$tmp_dir/backend/app" "$tmp_dir/backend/tests" "$tmp_dir/backend/alembic/versions" "$tmp_dir/frontend/src" "$tmp_dir/scripts"
  git -C "$tmp_dir" init -q

  write_lines "$tmp_dir/backend/app/too_big.py" 6
  write_lines "$tmp_dir/backend/tests/test_too_big.py" 6
  write_lines "$tmp_dir/backend/alembic/versions/001_too_big.py" 6
  write_lines "$tmp_dir/frontend/src/small.ts" 3
  write_lines "$tmp_dir/scripts/helper.sh" 3
  : > "$tmp_dir/scripts/code-file-line-exceptions.txt"
  git -C "$tmp_dir" add .

  if run_check "$tmp_dir" "scripts/code-file-line-exceptions.txt" 5 >/dev/null 2>&1; then
    echo "self-test failed: oversized business file should fail without exception" >&2
    return 1
  fi

  printf 'backend/app/too_big.py # planned extraction\n' > "$tmp_dir/scripts/code-file-line-exceptions.txt"
  if ! run_check "$tmp_dir" "scripts/code-file-line-exceptions.txt" 5 >/dev/null 2>&1; then
    echo "self-test failed: oversized exception should pass" >&2
    return 1
  fi

  echo "self-test passed"
}

case "${1:-}" in
  "")
    repo_root="$(git rev-parse --show-toplevel)"
    run_check "$repo_root" "$EXCEPTIONS_FILE" "$LIMIT"
    ;;
  --self-test)
    self_test
    ;;
  -h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
