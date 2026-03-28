# AGENTS — admin

Purpose: privileged orchestration for the trusted operator only.

Rules:
- never expose gateway internals, secrets, or control-plane URLs to untrusted chats
- use ACP workers for repo mutation when possible
- require reviewer sign-off on auth, infra, secret, or production changes
- do not enable browser automation on this host
