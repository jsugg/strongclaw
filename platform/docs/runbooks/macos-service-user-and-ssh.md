# macOS service user and SSH loopback

Create a dedicated standard user for the OpenClaw runtime.

Preferred pattern:
1. create `openclawsvc`
2. enable FileVault-aware login if needed
3. allow loopback SSH
4. run the gateway as that user
5. keep your daily admin user separate

You can then manage the runtime with:
`ssh openclawsvc@localhost`
