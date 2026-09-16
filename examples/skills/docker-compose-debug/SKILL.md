---
name: docker-compose-debug
description: >-
  Diagnose docker compose problems on this machine in the right order, avoiding
  the failure modes that have already cost hours here. Use whenever a container
  will not start, a port conflict appears, config changes seem to have no
  effect, or a compose command reports no configuration file.
---

# Docker compose debugging

Work from evidence, not theories. Read logs before proposing a cause.

## Always pin the project

Every compose command needs both flags, because the project identity otherwise
depends on the shell's current directory, and running from elsewhere silently
creates a different project:

    docker compose -f <compose file> --project-directory <its folder> <command>

"no configuration file provided: not found" means the command ran from the wrong
directory, not that anything is broken.

## Order of investigation

1. `docker compose ... ps` - is the container running, restarting or absent?
   A container missing from `ps` has exited; that is the whole answer to a
   connection-refused error.
2. `docker compose ... logs <service> --tail=40` - read the actual error. Do not
   guess before this step.
3. Only then form a hypothesis, and say what it is before acting on it.

## Known failure modes on this machine

**Port already allocated, naming a service that does not use that port.**
Some other container holds the name or the port. Find the owner:

    docker inspect <name> --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'

A stray compose file elsewhere on disk can own a container with the same
`container_name`. Remove it directly with `docker rm -f <name>`; that works
regardless of which project claims it.

**Config edits appear to have no effect.**
Check what the container actually reads, not what is on the host:

    docker compose ... exec <service> cat /etc/<app>/config.yml

If it differs from the host file, the write failed. Check the directory owner
with `ls -ld` - a container running as its own UID can own a mounted config
directory, which makes the host user unable to create files in it even though
the files inside look owned by them. Fix the directory, not the file.

Mounting a single config file read-only stops the container regenerating it:

    - ./app/config.yml:/etc/app/config.yml:ro

**Model or data file not found inside the container.**
Paths in the command are container paths, not host paths. A volume mapping
`/host/models:/models` means the flag must say `/models/file.gguf`. Check the
real filenames with `ls` on the host before editing the compose file; mixed case
and hyphenation are frequent culprits.

**Container starts but the service is unreachable.**
Check what it bound to in the logs. A service listening on `:::8080` (IPv6 only)
will refuse IPv4 connections forwarded by Docker, which shows as a connection
reset rather than a refusal.

## Reporting

Say which command produced which output. If a step was skipped, say so. Do not
claim a fix worked until a command result shows it working.
