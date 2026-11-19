"""Bounded index fallback for verifier prerequisites; explicit indexes stay fixed."""

CONTRACT = 'verifier-uv-network-error-domestic-index-fallback/v3'

SHELL = r'''
# Only the trusted verifier's child bash inherits this function.
uv() {
  local err rc arg current index attempt=0
  err=$(mktemp /tmp/agenttx-verifier-uv.XXXXXXXX) || return $?
  if command uv "$@" 2>"$err"; then
    cat "$err" >&2; rm -f "$err"; return 0
  else rc=$?; fi
  cat "$err" >&2
  if [ "${1:-}" != pip ] || [ "${2:-}" != install ]; then
    rm -f "$err"; return "$rc"
  fi
  for arg in "$@"; do
    case "$arg" in
      --index*|--extra-index*|--default-index*|--find-links*|--offline|--no-index|-i|-i?*|-f|-f?*)
        rm -f "$err"; return "$rc" ;;
    esac
  done
  current=${UV_INDEX_URL:-}
  case "$current" in
    https://mirrors.aliyun.com/pypi/simple|https://mirrors.aliyun.com/pypi/simple/|https://repo.huaweicloud.com/repository/pypi/simple|https://repo.huaweicloud.com/repository/pypi/simple/) ;;
    *) rm -f "$err"; return "$rc" ;;
  esac
  for index in https://mirrors.aliyun.com/pypi/simple https://repo.huaweicloud.com/repository/pypi/simple https://pypi.org/simple; do
    [ "${current%/}" != "$index" ] || continue
    if ! grep -qiE 'failed to (fetch|download)' "$err" ||
       ! grep -qiE 'timed out|network timeout|network.*unreachable|connection.*(refused|reset)|temporary failure|failed to lookup|name resolution' "$err"; then
      break
    fi
    attempt=$((attempt + 1))
    printf '{"contract":"verifier-uv-network-error-domestic-index-fallback/v3","index":"%s","retry":%d,"prior_returncode":%d}\n' "$index" "$attempt" "$rc" >&2
    printf 'AgentTX verifier prerequisite network retry %s: %s\n' "$attempt" "$index" >&2
    if UV_INDEX_URL="$index" command uv "$@" 2>"$err"; then
      cat "$err" >&2; rm -f "$err"; return 0
    else rc=$?; fi
    cat "$err" >&2
  done
  rm -f "$err"
  return "$rc"
}
export -f uv
'''


def configure(script):
    anchor = 'set +e\n'
    if script.count(anchor) != 1:
        raise ValueError('verifier bootstrap must have one registered child-shell boundary')
    return script.replace(anchor, SHELL + '\n' + anchor, 1)
