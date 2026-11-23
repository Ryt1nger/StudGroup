#!/bin/sh
set -eu

# sshd supplies the requested command only through this environment variable. Pass it as one
# argument before sudo applies its environment policy; deploy.sh validates the complete value.
exec sudo /usr/local/sbin/studgroup-deploy "${SSH_ORIGINAL_COMMAND:-}"
