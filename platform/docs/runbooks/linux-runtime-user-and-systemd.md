# Linux runtime user and user-level systemd

Create a dedicated non-admin account for the OpenClaw runtime and keep it separate from your daily admin shell.

Preferred pattern:
1. create `openclawsvc`
2. grant Docker access through rootless Docker or a tightly scoped `docker` group
3. enable linger if user services must survive logout
4. run the gateway and sidecars as that user
5. keep your admin account separate from the runtime account

Host-aware entrypoint:

```bash
Create the dedicated runtime user with your platform-native user-management tooling
```

Then switch into the runtime shell with:

```bash
sudo -iu openclawsvc
```

If you use the rendered service definitions, activate them with:

```bash
systemctl --user daemon-reload
systemctl --user enable --now openclaw-sidecars.service
systemctl --user enable --now openclaw-gateway.service
```
