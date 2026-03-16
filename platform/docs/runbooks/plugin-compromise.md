# Plugin compromise runbook

1. remove plugin from `plugins.allow`
2. stop the gateway
3. preserve logs and skill/plugin files
4. rotate secrets accessible to the plugin
5. re-run audits and harness
