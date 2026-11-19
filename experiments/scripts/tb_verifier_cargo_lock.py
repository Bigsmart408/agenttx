"""Pin the official geiger install to the lockfile published with that version."""

TASK = 'port-compressor'
CONTRACT = 'verifier-cargo-geiger-published-lock/v1'

SHELL = r'''
# This function belongs only to the trusted verifier's child shell.
cargo() {
  if [ "$#" -eq 4 ] && [ "$1" = install ] &&
     [ "$2" = cargo-geiger ] && [ "$3" = --version ] && [ "$4" = 0.12.0 ]; then
    printf '%s\n' '{"contract":"verifier-cargo-geiger-published-lock/v1","tool":"cargo-geiger","version":"0.12.0","mode":"published-lockfile"}' >&2
    command cargo "$@" --locked
  else
    command cargo "$@"
  fi
}
export -f cargo
'''


def configure(script):
    anchor = 'set +e\n'
    if script.count(anchor) != 1:
        raise ValueError('verifier bootstrap must have one registered child-shell boundary')
    return script.replace(anchor, SHELL + '\n' + anchor, 1)
