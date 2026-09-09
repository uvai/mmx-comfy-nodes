#!/usr/bin/env bash
# mmx_library_sync.sh — mirror the NAS library (Subjects / VideoRef / Sets on /volume1/subgenula)
# into /workspace/mmx/library for the MMX Library Image node. Copied to /root by
# additional_params.sh (section 3c) and run detached at boot; the node's "Mirror from NAS"
# button re-runs it. Same ssh path as the nas_worker (mmx_nas_key + SOCKS via userspace
# tailscaled). A locked share, a missing key or an unreachable NAS is a logged skip, exit 0.
#
#   mmx_library_sync.sh [--wait]     --wait (boot use): poll every 60 s for up to 2 h until the
#                                    NAS is reachable AND the share is unlocked, then mirror;
#                                    without it: one attempt (the node's Mirror button)
# Env: MMX_NAS (user@host, default from NAS_DEST), MMX_NAS_KEY, MMX_NAS_SHARE, MMX_LIBRARY,
#      MMX_LIBRARY_FOLDERS (default "Subjects Sets VideoRef", each mirrored recursively),
#      MMX_LIBRARY_MAX_SIZE (rsync --max-size, default 1500m), MMX_NAS_PROXY ("none" = no SOCKS
#      hop), MMX_LIBRARY_WAIT_TRIES / MMX_LIBRARY_WAIT_SECS (default 120 x 60 s)
set -u
LOG="${MMX_LIBRARY_SYNC_LOG:-/workspace/mmx_library_sync.log}"
DEST="${MMX_LIBRARY:-/workspace/mmx/library}"
FOLDERS="${MMX_LIBRARY_FOLDERS:-Subjects Sets VideoRef}"
SHARE="${MMX_NAS_SHARE:-/volume1/subgenula}"
KEY="${MMX_NAS_KEY:-/root/.ssh/mmx_nas_key}"
MAXSIZE="${MMX_LIBRARY_MAX_SIZE:-1500m}"
WAIT=0; [ "${1:-}" = "--wait" ] && WAIT=1
mkdir -p "$(dirname "$LOG")" "$DEST"
log() { echo "$(date -u +%FT%TZ) [library-sync] $*" >> "$LOG"; }

# exactly one sync at a time (the button and the boot run may overlap)
LOCK=/tmp/mmx_library_sync.lock
exec 9>"$LOCK"
if ! flock -n 9; then log "another sync is running — skipped"; exit 0; fi

# user@host from MMX_NAS, else the template's NAS_DEST (user@host:path), else PID 1's env
USERHOST="${MMX_NAS:-}"
if [ -z "$USERHOST" ]; then
    ND="${NAS_DEST:-}"
    [ -z "$ND" ] && ND=$(tr '\0' '\n' < /proc/1/environ 2>/dev/null | sed -n 's/^NAS_DEST=//p' | head -1)
    case "$ND" in *@*) USERHOST="${ND%%:*}";; esac
fi
USERHOST="${USERHOST:-alchera@100.81.253.103}"
PROXY="${MMX_NAS_PROXY:-nc -X 5 -x 127.0.0.1:1055 %h %p}"

SSH="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new"
[ "$PROXY" != "none" ] && SSH="$SSH -o ProxyCommand='$PROXY'"

# Boot (--wait): poll until the key exists, the NAS answers AND the share is unlocked — the share
# is normally still locked when the instance comes up and gets unlocked from the vgo dashboard
# later. One line per state change plus a heartbeat every 10 attempts, so the log tail (shown in
# the Library Image node) always says what it is waiting for. Without --wait: one attempt.
TRIES=1; SECS="${MMX_LIBRARY_WAIT_SECS:-60}"
[ "$WAIT" = 1 ] && TRIES="${MMX_LIBRARY_WAIT_TRIES:-120}"
share_state() {
    eval "$SSH" "$USERHOST" "\"if mount | grep -q ' $SHARE type ecryptfs'; then echo mounted; elif [ -d '$SHARE' ]; then echo plain; else echo locked; fi\"" 2>>"$LOG"
}
STATE=""; LASTWHY=""
for i in $(seq 1 $TRIES); do
    if [ ! -f "$KEY" ]; then WHY="NAS key $KEY not present yet"
    elif ! eval "$SSH" "$USERHOST" "\"true\"" >/dev/null 2>&1; then WHY="NAS $USERHOST unreachable"
    else
        STATE=$(share_state)
        case "$STATE" in
            mounted|plain) WHY="";;
            locked) WHY="share $SHARE is LOCKED (unlock it in the vgo dashboard)";;
            *) WHY="could not judge the share state (got '$STATE')";;
        esac
    fi
    [ -z "$WHY" ] && break
    if [ "$i" = "$TRIES" ]; then
        log "gave up after $i attempt(s): $WHY — skipped (existing mirror kept: $(find "$DEST" -type f 2>/dev/null | wc -l) files); press Mirror from NAS once the share is unlocked"
        exit 0
    fi
    if [ "$WHY" != "$LASTWHY" ] || [ $((i % 10)) = 0 ]; then
        log "waiting: $WHY — retry in ${SECS}s (attempt $i/$TRIES)"; LASTWHY="$WHY"
    fi
    sleep "$SECS"
done
log "NAS reachable, share $SHARE $STATE — mirroring $FOLDERS (recursive)"

command -v rsync >/dev/null 2>&1 || apt-get install -y -qq rsync >>"$LOG" 2>&1
ok=1; total=0
for f in $FOLDERS; do
    if ! eval "$SSH" "$USERHOST" "\"test -d '$SHARE/$f'\"" 2>/dev/null; then
        log "$f: not on the share — skipped"; continue
    fi
    mkdir -p "$DEST/$f"
    if eval rsync -rt --partial --delete --max-size="$MAXSIZE" --exclude "'@eaDir'" --exclude "'.*'" --exclude "'Thumbs.db'" \
            -e "\"$SSH\"" "$USERHOST:'$SHARE/$f/'" "'$DEST/$f/'" >>"$LOG" 2>&1; then
        n=$(find "$DEST/$f" -type f | wc -l); total=$((total + n)); log "$f: ok ($n files)"
    else
        ok=0; log "$f: rsync FAILED (rc $?)"
    fi
done
log "done: $total files under $DEST$( [ $ok = 1 ] || echo ' (with failures)')"
exit 0
