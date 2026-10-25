#!/bin/sh
# Container entrypoint: make the upload directory writable, then run the
# command as the unprivileged user `app` (uid 10001).
#
# Fly volumes (and fresh Docker volumes mounted over a path the image does
# not own) are root-owned, so the app user could not create its upload
# directory there. This script starts as root only to create that directory
# and hand it to `app`, then drops privileges with setpriv (util-linux, part
# of python:3.11-slim). Started as any other user, it just runs the command.
set -eu

if [ "$(id -u)" = "0" ]; then
    upload_dir="${RULESMITH_UPLOAD_DIR:-}"
    # Only absolute paths: a relative default resolves under the app's home,
    # which the app user already owns and creates itself.
    case "$upload_dir" in
        /*)
            mkdir -p "$upload_dir"
            # Not recursive: files below were written by `app` already, and a
            # recursive chown would slow every start as the volume grows.
            chown -h app:app "$upload_dir"
            ;;
    esac
    export HOME=/home/app
    exec setpriv --reuid=app --regid=app --init-groups -- "$@"
fi

exec "$@"
