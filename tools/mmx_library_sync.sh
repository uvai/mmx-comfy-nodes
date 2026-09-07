#!/usr/bin/env bash
# mmx_library_sync.sh — mirror the NAS library (Subjects / VideoRef / Sets on /volume1/subgenula)
# into /workspace/mmx/library for the MMX Library Image node. Copied to /root by
# additional_params.sh (section 3c) and run detached at boot; the node's "Mirror from NAS"
# button re-runs it. Same ssh path as the nas_worker (mmx_nas_key + SOCKS via userspace
# tailscaled). A locked share, a missing key or an unreachable NAS is a logged skip, exit 0.
#
#   mmx_library_sync.sh [--wait]     --wait: give the nas_worker up to 10 min to bring up
#                                    tailscale + the key before giving up (boot use)
# Env: MMX_NAS (user@host, default from NAS_DEST), MMX_NAS_KEY, MMX_NAS_SHARE, MMX_LIBRARY,
#      MMX_LIBRARY_FOLDERS (default "Subjects VideoRef Sets"), MMX_LIBRARY_MAX_SIZE (rsync
#      --max-size, default 1500m), MMX_NAS_PROXY (set to "none" to skip the SOCKS hop)
set -u
LOG="${MMX_LIBRARY_SYNC_LOG:-/workspace/mmx_library_sync.log}"
DEST="${MMX_LIBRARY:-/workspace/mmx/library}"
FOLDERS="${MMX_LIBRARY_FOLDERS:-Subjects VideoRef Sets}"
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

# wait for the nas_worker to have put the key + tailscale in place (boot), or bail
tries=1; [ "$WAIT" = 1 ] && tries=60
for i in $(seq 1 $tries); do
    if [ -f "$KEY" ] && eval "$SSH" "$USERHOST" "\"true\"" >/dev/null 2>&1; then break; fi
    if [ "$i" = "$tries" ]; then
        [ -f "$KEY" ] && log "NAS $USERHOST unreachable — skipped (existing mirror kept: $(find "$DEST" -type f 2>/dev/null | wc -l) files)" \
                      || log "NAS key $KEY not present — skipped"
        exit 0
    fi
    sleep 10
done

# locked share = the ecryptfs mount is absent (same probe as the runner / preset store)
STATE=$(eval "$SSH" "$USERHOST" "\"if mount | grep -q ' $SHARE type ecryptfs'; then echo mounted; elif [ -d '$SHARE' ]; then echo plain; else echo locked; fi\"" 2>>"$LOG")
case "$STATE" in
    mounted|plain) ;;
    locked) log "share $SHARE is LOCKED — skipped (unlock it in the vgo dashboard, then press Mirror from NAS)"; exit 0 ;;
    *) log "could not judge the share state (got '$STATE') — skipped"; exit 0 ;;
esac

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
