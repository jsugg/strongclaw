# Telegram Pairing Guidance

Telegram/WhatsApp channel overlay merged. Configure owner IDs before production use.

The overlay also sets `commands.ownerAllowFrom` to `telegram:<owner id>`. OpenClaw 2026.4.29+
no longer treats the channel `allowFrom` list as command owners, so owner-only commands and
exec approvals need this entry.

Approve pairings with: `openclaw pairing list telegram && openclaw pairing approve telegram <CODE>`
