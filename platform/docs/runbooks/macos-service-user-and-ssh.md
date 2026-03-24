# macOS runtime user and loopback SSH

Create a dedicated standard user for the OpenClaw runtime and keep it separate
from your daily admin account.

Preferred pattern:
1. create `openclawsvc`
2. enable FileVault-aware login if needed
3. allow loopback SSH
4. run the gateway as that user
5. keep your daily admin user separate

Host-aware entrypoint:

```bash
Create the dedicated runtime user with your platform-native user-management tooling
```

You can then manage the runtime with:
`ssh openclawsvc@localhost`
