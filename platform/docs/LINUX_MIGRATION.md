# Linux Migration

Move to Linux when you want:
- stronger isolation
- always-on sidecars
- browser lab on a separate host
- rootless Docker or gVisor later

## Included assets

- systemd unit templates
- Linux bootstrap script
- same config overlays
- same companion tooling

## Migration order

1. provision Linux host and non-root user
2. install Docker / rootless Docker
3. copy repo and env contract
4. render systemd units
5. start sidecars
6. start gateway
7. verify baseline
8. move channels last
