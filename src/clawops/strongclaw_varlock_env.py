"""Manage the repo-local Varlock env contract for StrongClaw."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from typing import Final, cast

from clawops.cli_roots import add_asset_root_argument, resolve_asset_root_argument
from clawops.strongclaw_runtime import (
    READINESS_VARLOCK_ENV_MODES,
    CommandError,
    VarlockEnvMode,
    clear_env_assignment,
    generate_secret_value,
    load_env_assignments,
    merge_env_template,
    profile_requires_hypermemory_backend,
    resolve_profile,
    resolve_varlock_bin,
    run_command,
    set_env_assignment,
    use_varlock_env_mode,
    value_is_effective,
    varlock_env_dir,
    varlock_env_template_file,
    varlock_local_env_file,
    varlock_plugin_env_file,
    write_env_assignments,
)

DEFAULT_VARLOCK_PLUGIN_VERSIONS: Final[dict[str, str]] = {
    "1password": "0.3.0",
    "aws": "0.0.5",
    "azure": "0.0.5",
    "bitwarden": "0.0.5",
    "gcp": "0.2.0",
    "infisical": "0.0.5",
}
PROVIDER_ENV_DEFAULTS: Final[tuple[tuple[str, str], ...]] = (
    ("OPENAI_API_KEY", "openai/gpt-5.4"),
    ("ANTHROPIC_API_KEY", "anthropic/claude-opus-4-6"),
    ("ZAI_API_KEY", "zai/glm-5"),
)
LOCAL_SECRET_MIN_LENGTHS: Final[dict[str, int]] = {
    "OPENCLAW_GATEWAY_TOKEN": 40,
    "NEO4J_PASSWORD": 16,
    "LITELLM_MASTER_KEY": 24,
    "LITELLM_DB_PASSWORD": 16,
}
OLLAMA_MIN_CONTEXT_WINDOW: Final[int] = 16_000
OLLAMA_CONTEXT_LENGTH_RE = re.compile(r"context length\s+(\d+)", re.IGNORECASE)
PREFERRED_OLLAMA_MODEL_PREFIXES: Final[tuple[str, ...]] = ("deepseek-r1",)


def _interactive_mode(*, check_only: bool, non_interactive: bool) -> bool:
    """Return whether the current execution may prompt the user."""
    return not check_only and not non_interactive and os.isatty(0) and os.isatty(1)


def _prompt_value(prompt: str, default: str = "", *, secret: bool = False) -> str:
    """Prompt for one value."""
    rendered_prompt = f"{prompt}: " if not default else f"{prompt} [{default}]: "
    stream = sys.stderr
    stream.write(rendered_prompt)
    stream.flush()
    if secret:
        import getpass as secret_prompt

        answer = secret_prompt.getpass("", stream=stream)
    else:
        answer = input()
    answer = answer.strip()
    return default if not answer else answer


def _prompt_yes_no(prompt: str, *, default_yes: bool) -> bool:
    """Prompt for a yes/no choice."""
    suffix = "[Y/n]" if default_yes else "[y/N]"
    while True:
        answer = _prompt_value(f"{prompt} {suffix}").strip().casefold()
        if not answer:
            return default_yes
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer y or n.", file=sys.stderr)


def _save_plugin_overlay(path: pathlib.Path, content: str) -> None:
    """Write the managed backend overlay with strict permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)

    # lgtm[py/clear-text-storage-sensitive-data]
    # Backend overlay secrets are intentionally written to a user-owned local file and immediately restricted to 0600.
    path.write_text(content.strip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def _remove_plugin_overlay(path: pathlib.Path) -> None:
    """Remove the plugin overlay when switching to local mode."""
    path.unlink(missing_ok=True)


def _provider_key_for_model_ref(model_ref: str) -> str:
    """Return the provider env key for one model reference."""
    provider = model_ref.split("/", 1)[0]
    return {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "zai": "ZAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "moonshot": "MOONSHOT_API_KEY",
        "ollama": "OLLAMA_API_KEY",
    }.get(provider, "")


def _configured_model_chain(values: dict[str, str]) -> list[str]:
    """Return the configured model chain."""
    model_chain: list[str] = []
    default_model = values.get("OPENCLAW_DEFAULT_MODEL", "").strip()
    fallback_csv = values.get("OPENCLAW_MODEL_FALLBACKS", "").strip()
    if default_model:
        model_chain.append(default_model)
    if fallback_csv:
        for candidate in fallback_csv.split(","):
            normalized = candidate.strip()
            if normalized and normalized not in model_chain:
                model_chain.append(normalized)
    return model_chain


def _configured_provider_keys(values: dict[str, str]) -> list[str]:
    """Return the provider env keys implied by the model chain."""
    provider_keys: list[str] = []
    model_chain = _configured_model_chain(values)
    if not model_chain:
        for env_key, default_model in PROVIDER_ENV_DEFAULTS:
            if value_is_effective(values.get(env_key)):
                model_chain.append(default_model)
        if value_is_effective(values.get("OPENROUTER_API_KEY")):
            model_chain.append("openrouter/auto")
        if value_is_effective(values.get("MOONSHOT_API_KEY")):
            model_chain.append("moonshot/default")
        ollama_model = values.get("OPENCLAW_OLLAMA_MODEL", "").strip()
        if value_is_effective(values.get("OLLAMA_API_KEY")) and ollama_model:
            model_chain.append(f"ollama/{ollama_model}")
    for model_ref in model_chain:
        provider_key = _provider_key_for_model_ref(model_ref)
        if provider_key and provider_key not in provider_keys and provider_key != "OLLAMA_API_KEY":
            provider_keys.append(provider_key)
    return provider_keys


def _ensure_required_defaults(
    repo_root: pathlib.Path,
    *,
    check_only: bool,
) -> int:
    """Ensure locally-managed secrets and defaults are present."""
    env_file = varlock_local_env_file(repo_root)
    values = load_env_assignments(env_file)
    updated = 0
    required_defaults = {
        "APP_ENV": "local",
        "OPENCLAW_VERSION": os.environ.get("OPENCLAW_VERSION", "2026.3.13"),
        "VARLOCK_SECRET_BACKEND": "local",
        "OPENCLAW_CONTROL_USER": values.get("OPENCLAW_CONTROL_USER", "openclawsvc")
        or "openclawsvc",
        "OPENCLAW_STATE_DIR": values.get("OPENCLAW_STATE_DIR", "~/.openclaw") or "~/.openclaw",
        "NEO4J_USERNAME": values.get("NEO4J_USERNAME", "neo4j") or "neo4j",
        "HYPERMEMORY_EMBEDDING_MODEL": "ollama/nomic-embed-text",
        "HYPERMEMORY_EMBEDDING_BASE_URL": "http://127.0.0.1:4000/v1",
        "HYPERMEMORY_QDRANT_URL": "http://127.0.0.1:6333",
        "WHATSAPP_SESSION_DIR": "~/.openclaw/channels/whatsapp",
    }

    # lgtm[py/clear-text-storage-sensitive-data]
    # Locally generated bootstrap secrets are intentionally written to the user-owned env contract, and write_env_assignments enforces 0600 permissions.
    generated_secret_defaults = {
        "OPENCLAW_GATEWAY_TOKEN": generate_secret_value(),
        "LITELLM_MASTER_KEY": generate_secret_value(),
        "LITELLM_DB_PASSWORD": generate_secret_value(),
        "NEO4J_PASSWORD": generate_secret_value(),
    }
    required_defaults.update(generated_secret_defaults)
    for key, default_value in required_defaults.items():
        current = values.get(key)
        if key in LOCAL_SECRET_MIN_LENGTHS:
            minimum_length = LOCAL_SECRET_MIN_LENGTHS[key]
            current_text = "" if current is None else current.strip()
            invalid_reason: str | None = None
            if not value_is_effective(current_text):
                invalid_reason = "blank or uses a placeholder"
            elif len(current_text) < minimum_length:
                invalid_reason = f"shorter than the required minimum length ({minimum_length})"
            if invalid_reason is None:
                continue
        else:
            invalid_reason = None if value_is_effective(current) else "blank or uses a placeholder"
            if invalid_reason is None:
                continue
        if check_only:
            raise CommandError(f"Required Varlock key {key} is {invalid_reason} in {env_file}.")
        set_env_assignment(env_file, key, default_value)
        values[key] = default_value
        updated += 1
    return updated


def _local_provider_credentials_present(values: dict[str, str]) -> bool:
    """Return whether local provider auth is already configured."""
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ZAI_API_KEY",
        "OPENROUTER_API_KEY",
        "MOONSHOT_API_KEY",
        "OLLAMA_API_KEY",
        "OPENCLAW_OLLAMA_MODEL",
    ):
        if value_is_effective(values.get(key)):
            return True
    return False


def _ollama_listed_models(output: str) -> list[str]:
    """Return model names from `ollama list` output."""
    models: list[str] = []
    for line in output.splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        model_name = stripped.split(maxsplit=1)[0].strip()
        if model_name:
            models.append(model_name)
    return models


def _ollama_model_context_window(model_name: str) -> int:
    """Return the parsed Ollama context window for one local model."""
    result = run_command(["ollama", "show", model_name], timeout_seconds=15)
    if not result.ok:
        return 0
    match = OLLAMA_CONTEXT_LENGTH_RE.search(result.stdout)
    if match is None:
        return 0
    return int(match.group(1))


def _ensure_non_interactive_model_chain(
    repo_root: pathlib.Path,
    *,
    check_only: bool,
    non_interactive: bool,
) -> None:
    """Seed a usable local Ollama model chain for non-interactive local setup."""
    if check_only or not non_interactive:
        return
    env_file = varlock_local_env_file(repo_root)
    values = load_env_assignments(env_file)
    if _configured_model_chain(values):
        return
    ollama_result = run_command(["ollama", "list"], timeout_seconds=15)
    if not ollama_result.ok:
        return
    candidates = _ollama_listed_models(ollama_result.stdout)
    if not candidates:
        return
    ordered_candidates = sorted(
        candidates,
        key=lambda name: (
            0 if any(name.startswith(prefix) for prefix in PREFERRED_OLLAMA_MODEL_PREFIXES) else 1,
            name,
        ),
    )
    for candidate in ordered_candidates:
        if _ollama_model_context_window(candidate) < OLLAMA_MIN_CONTEXT_WINDOW:
            continue
        if not value_is_effective(values.get("OLLAMA_API_KEY")):
            set_env_assignment(env_file, "OLLAMA_API_KEY", "ollama-local")
        set_env_assignment(env_file, "OPENCLAW_OLLAMA_MODEL", candidate)
        set_env_assignment(env_file, "OPENCLAW_DEFAULT_MODEL", f"ollama/{candidate}")
        return


def _prompt_model_chain(repo_root: pathlib.Path) -> None:
    """Interactively configure the default model chain and local credentials."""
    env_file = varlock_local_env_file(repo_root)
    values = load_env_assignments(env_file)
    if value_is_effective(values.get("OPENCLAW_DEFAULT_MODEL")):
        return
    print("Choose the primary provider:", file=sys.stderr)
    print("  1. OpenAI", file=sys.stderr)
    print("  2. Anthropic", file=sys.stderr)
    print("  3. Z.AI / GLM", file=sys.stderr)
    print("  4. OpenRouter", file=sys.stderr)
    print("  5. Moonshot", file=sys.stderr)
    print("  6. Ollama local model", file=sys.stderr)
    print("  7. Skip", file=sys.stderr)
    selection = _prompt_value("Selection", "7")
    primary_model = ""
    if selection == "1":
        primary_model = "openai/gpt-5.4"
    elif selection == "2":
        primary_model = "anthropic/claude-opus-4-6"
    elif selection == "3":
        primary_model = "zai/glm-5"
    elif selection == "4":
        primary_model = _prompt_value("OpenRouter primary model ref")
    elif selection == "5":
        primary_model = _prompt_value("Moonshot primary model ref")
    elif selection == "6":
        ollama_model = _prompt_value(
            "Ollama primary model", values.get("OPENCLAW_OLLAMA_MODEL", "")
        )
        if ollama_model:
            primary_model = f"ollama/{ollama_model}"
            set_env_assignment(env_file, "OPENCLAW_OLLAMA_MODEL", ollama_model)
            if not value_is_effective(values.get("OLLAMA_API_KEY")):
                set_env_assignment(env_file, "OLLAMA_API_KEY", "ollama-local")
    if not primary_model:
        return
    set_env_assignment(env_file, "OPENCLAW_DEFAULT_MODEL", primary_model)
    fallback_models = _prompt_value(
        "Optional fallback model refs (comma-separated)",
        values.get("OPENCLAW_MODEL_FALLBACKS", ""),
    )
    set_env_assignment(env_file, "OPENCLAW_MODEL_FALLBACKS", fallback_models)
    provider_key = _provider_key_for_model_ref(primary_model)
    if provider_key == "OPENAI_API_KEY" and not value_is_effective(values.get(provider_key)):
        set_env_assignment(env_file, provider_key, _prompt_value("OpenAI API key", secret=True))
    elif provider_key == "ANTHROPIC_API_KEY" and not value_is_effective(values.get(provider_key)):
        set_env_assignment(env_file, provider_key, _prompt_value("Anthropic API key", secret=True))
    elif provider_key == "ZAI_API_KEY" and not value_is_effective(values.get(provider_key)):
        set_env_assignment(env_file, provider_key, _prompt_value("Z.AI API key", secret=True))
    elif provider_key == "OPENROUTER_API_KEY" and not value_is_effective(values.get(provider_key)):
        set_env_assignment(env_file, provider_key, _prompt_value("OpenRouter API key", secret=True))
    elif provider_key == "MOONSHOT_API_KEY" and not value_is_effective(values.get(provider_key)):
        set_env_assignment(env_file, provider_key, _prompt_value("Moonshot API key", secret=True))


def _prompt_secret_backend(repo_root: pathlib.Path) -> None:
    """Interactively configure the provider secret backend."""
    env_file = varlock_local_env_file(repo_root)
    plugin_file = varlock_plugin_env_file(repo_root)
    values = load_env_assignments(env_file)
    backend = values.get("VARLOCK_SECRET_BACKEND", "local").strip() or "local"
    if backend != "local" and plugin_file.exists():
        if not _prompt_yes_no("Review or change the configured secret backend?", default_yes=False):
            return
    elif _local_provider_credentials_present(values):
        if not _prompt_yes_no(
            "Provider auth is already configured locally. Switch to a managed secret backend instead?",
            default_yes=False,
        ):
            return
    print("Choose the provider secret backend:", file=sys.stderr)
    print("  1. Repo-local .env.local", file=sys.stderr)
    print("  2. 1Password", file=sys.stderr)
    print("  3. AWS Secrets Manager", file=sys.stderr)
    print("  4. AWS Parameter Store", file=sys.stderr)
    print("  5. Azure Key Vault", file=sys.stderr)
    print("  6. Bitwarden Secrets Manager", file=sys.stderr)
    print("  7. Google Secret Manager", file=sys.stderr)
    print("  8. Infisical", file=sys.stderr)
    selection = _prompt_value("Selection", "1")
    backend_map = {
        "1": "local",
        "2": "1password",
        "3": "aws-secrets-manager",
        "4": "aws-parameter-store",
        "5": "azure-key-vault",
        "6": "bitwarden",
        "7": "google-secret-manager",
        "8": "infisical",
    }
    selected_backend = backend_map.get(selection, "local")
    set_env_assignment(env_file, "VARLOCK_SECRET_BACKEND", selected_backend)
    if selected_backend == "local":
        clear_env_assignment(env_file, "VARLOCK_SECRET_BACKEND_MODE")
        clear_env_assignment(env_file, "VARLOCK_SECRET_BACKEND_AUTH")
        _remove_plugin_overlay(plugin_file)
        return
    provider_keys = _configured_provider_keys(load_env_assignments(env_file))
    _save_plugin_overlay(plugin_file, _backend_overlay(selected_backend, provider_keys))


def _backend_overlay(backend: str, provider_keys: list[str]) -> str:
    """Return the plugin overlay template for one backend."""
    if backend == "1password":
        environment_id = _prompt_value("1Password Environment ID")
        use_desktop = _prompt_yes_no(
            "Use desktop app auth via the local op CLI instead?", default_yes=False
        )
        account = _prompt_value("Optional 1Password account shorthand", "")
        token = "" if use_desktop else _prompt_value("1Password service account token", secret=True)
        allow_app_auth = "true" if use_desktop else "false"
        header = (
            f"# @plugin(@varlock/1password-plugin@{DEFAULT_VARLOCK_PLUGIN_VERSIONS['1password']})\n"
            f"# @initOp(token=$OP_TOKEN, allowAppAuth={allow_app_auth}"
            f"{', account=' + account if account else ''})\n"
            f"# @setValuesBulk(opLoadEnvironment({environment_id}))\n# ---\n# @type=opServiceAccountToken\nOP_TOKEN={token}"
        )
        return "\n".join([header, *[f"{key}=" for key in provider_keys]])
    if backend in {"aws-secrets-manager", "aws-parameter-store"}:
        region = _prompt_value("AWS region", "us-east-1")
        name_prefix = _prompt_value("Optional secret name prefix", "")
        use_profile = _prompt_yes_no(
            "Use a named AWS profile from ~/.aws/credentials?", default_yes=True
        )
        profile = _prompt_value("AWS profile name", "default") if use_profile else ""
        access_key_id = "" if use_profile else _prompt_value("AWS access key ID")
        secret_access_key = (
            "" if use_profile else _prompt_value("AWS secret access key", secret=True)
        )
        session_token = (
            "" if use_profile else _prompt_value("Optional AWS session token", secret=True)
        )
        resolver = "awsSecret" if backend == "aws-secrets-manager" else "awsParam"
        header = (
            f"# @plugin(@varlock/aws-secrets-plugin@{DEFAULT_VARLOCK_PLUGIN_VERSIONS['aws']})\n"
            f"# @initAws(region={region}"
            f"{', namePrefix=' + name_prefix if name_prefix else ''}"
            f"{', profile=' + profile if profile else ''}"
            f"{', accessKeyId=$AWS_ACCESS_KEY_ID' if access_key_id else ''}"
            f"{', secretAccessKey=$AWS_SECRET_ACCESS_KEY' if secret_access_key else ''}"
            f"{', sessionToken=$AWS_SESSION_TOKEN' if session_token else ''})\n# ---"
        )
        lines = [header]
        if access_key_id:
            lines.extend(
                [
                    "# @type=awsAccessKey",
                    f"AWS_ACCESS_KEY_ID={access_key_id}",
                    "# @type=awsSecretKey",
                    f"AWS_SECRET_ACCESS_KEY={secret_access_key}",
                ]
            )
            if session_token:
                lines.append(f"AWS_SESSION_TOKEN={session_token}")
        lines.extend(f"{key}={resolver}()" for key in provider_keys)
        return "\n".join(lines)
    if backend == "azure-key-vault":
        vault_url = _prompt_value("Azure Key Vault URL")
        use_service_principal = _prompt_yes_no("Use a service principal?", default_yes=True)
        tenant_id = _prompt_value("Azure tenant ID") if use_service_principal else ""
        client_id = _prompt_value("Azure client ID") if use_service_principal else ""
        client_secret = (
            _prompt_value("Azure client secret", secret=True) if use_service_principal else ""
        )
        header = (
            f"# @plugin(@varlock/azure-key-vault-plugin@{DEFAULT_VARLOCK_PLUGIN_VERSIONS['azure']})\n"
            f'# @initAzure(vaultUrl="{vault_url}"'
            f"{', tenantId=$AZURE_TENANT_ID' if tenant_id else ''}"
            f"{', clientId=$AZURE_CLIENT_ID' if client_id else ''}"
            f"{', clientSecret=$AZURE_CLIENT_SECRET' if client_secret else ''})\n# ---"
        )
        lines = [header]
        if tenant_id:
            lines.extend(
                [
                    "# @type=azureTenantId",
                    f"AZURE_TENANT_ID={tenant_id}",
                    "# @type=azureClientId",
                    f"AZURE_CLIENT_ID={client_id}",
                    "# @type=azureClientSecret",
                    f"AZURE_CLIENT_SECRET={client_secret}",
                ]
            )
        lines.extend(f"{key}=azureSecret()" for key in provider_keys)
        return "\n".join(lines)
    if backend == "google-secret-manager":
        project_id = _prompt_value("Google Cloud project ID")
        service_account_json = ""
        if _prompt_yes_no("Use a service account JSON instead of ADC?", default_yes=False):
            service_account_json = _prompt_value("GCP service account JSON", secret=True)
        header = (
            f"# @plugin(@varlock/google-secret-manager-plugin@{DEFAULT_VARLOCK_PLUGIN_VERSIONS['gcp']})\n"
            f"# @initGsm(projectId={project_id}"
            f"{', credentials=$GCP_SA_KEY' if service_account_json else ''})\n# ---"
        )
        lines = [header]
        if service_account_json:
            lines.extend(["# @type=gcpServiceAccountJson", f"GCP_SA_KEY={service_account_json}"])
        lines.extend(f"{key}=gsm()" for key in provider_keys)
        return "\n".join(lines)
    if backend == "infisical":
        project_id = _prompt_value("Infisical project ID")
        environment_name = _prompt_value("Infisical environment", "dev")
        client_id = _prompt_value("Infisical client ID")
        client_secret = _prompt_value("Infisical client secret", secret=True)
        site_url = _prompt_value("Optional Infisical site URL", "")
        secret_path = _prompt_value("Optional default secret path", "/")
        header = (
            f"# @plugin(@varlock/infisical-plugin@{DEFAULT_VARLOCK_PLUGIN_VERSIONS['infisical']})\n"
            f"# @initInfisical(projectId={project_id}, environment={environment_name}, clientId=$INFISICAL_CLIENT_ID, clientSecret=$INFISICAL_CLIENT_SECRET"
            f"{', siteUrl=' + site_url if site_url else ''}"
            f"{', secretPath=' + secret_path if secret_path else ''})\n"
            "# @setValuesBulk(infisicalBulk())\n# ---"
        )
        lines = [
            header,
            "# @type=infisicalClientId",
            f"INFISICAL_CLIENT_ID={client_id}",
            "# @type=infisicalClientSecret",
            f"INFISICAL_CLIENT_SECRET={client_secret}",
        ]
        lines.extend(f"{key}=" for key in provider_keys)
        return "\n".join(lines)
    if backend == "bitwarden":
        access_token = _prompt_value("Bitwarden machine account access token", secret=True)
        api_url = _prompt_value("Optional Bitwarden API URL", "")
        identity_url = _prompt_value("Optional Bitwarden identity URL", "")
        header = (
            f"# @plugin(@varlock/bitwarden-plugin@{DEFAULT_VARLOCK_PLUGIN_VERSIONS['bitwarden']})\n"
            f"# @initBitwarden(accessToken=$BITWARDEN_ACCESS_TOKEN"
            f"{', apiUrl=' + api_url if api_url else ''}"
            f"{', identityUrl=' + identity_url if identity_url else ''})\n# ---"
        )
        lines = [
            header,
            "# @type=bitwardenAccessToken",
            f"BITWARDEN_ACCESS_TOKEN={access_token}",
        ]
        for key in provider_keys:
            secret_uuid = _prompt_value(f"Bitwarden secret UUID for {key}")
            lines.append(f'{key}=bitwarden("{secret_uuid}")')
        return "\n".join(lines)
    raise CommandError(f"unsupported secret backend: {backend}")


def _ensure_hypermemory_embedding_model(
    repo_root: pathlib.Path,
    *,
    check_only: bool,
    non_interactive: bool,
) -> None:
    """Require an embedding model when the hypermemory profile is active."""
    profile = resolve_profile()
    if not profile_requires_hypermemory_backend(profile):
        return
    env_file = varlock_local_env_file(repo_root)
    values = load_env_assignments(env_file)
    if value_is_effective(values.get("HYPERMEMORY_EMBEDDING_MODEL")):
        return
    runtime_embedding_model = os.environ.get("HYPERMEMORY_EMBEDDING_MODEL", "").strip()
    if value_is_effective(runtime_embedding_model):
        set_env_assignment(env_file, "HYPERMEMORY_EMBEDDING_MODEL", runtime_embedding_model)
        return
    if not check_only and non_interactive:
        ollama_result = run_command(["ollama", "list"], timeout_seconds=15)
        if ollama_result.ok and "nomic-embed-text" in ollama_result.stdout:
            set_env_assignment(env_file, "HYPERMEMORY_EMBEDDING_MODEL", "ollama/nomic-embed-text")
            set_env_assignment(
                env_file, "HYPERMEMORY_EMBEDDING_API_BASE", "http://host.docker.internal:11434"
            )
            return
    if (
        check_only
        or non_interactive
        or not _interactive_mode(check_only=False, non_interactive=non_interactive)
    ):
        raise CommandError(
            f"HYPERMEMORY_EMBEDDING_MODEL is required when OPENCLAW_CONFIG_PROFILE={profile}."
        )
    set_env_assignment(
        env_file,
        "HYPERMEMORY_EMBEDDING_MODEL",
        _prompt_value("Embedding model ref for LiteLLM route hypermemory-embedding"),
    )


def _validate_secret_backend_configuration(
    repo_root: pathlib.Path,
    *,
    check_only: bool,
    non_interactive: bool,
) -> None:
    """Validate local-vs-plugin secret backend wiring."""
    env_file = varlock_local_env_file(repo_root)
    plugin_file = varlock_plugin_env_file(repo_root)
    values = load_env_assignments(env_file)
    backend = values.get("VARLOCK_SECRET_BACKEND", "local").strip() or "local"
    provider_keys = _configured_provider_keys(values)
    if backend == "local":
        if plugin_file.exists():
            if check_only or non_interactive:
                raise CommandError(f"VARLOCK_SECRET_BACKEND=local, but {plugin_file} still exists.")
            _remove_plugin_overlay(plugin_file)
        return
    if not plugin_file.exists():
        if (
            check_only
            or non_interactive
            or not _interactive_mode(check_only=check_only, non_interactive=non_interactive)
        ):
            raise CommandError(f"VARLOCK_SECRET_BACKEND={backend}, but {plugin_file} is missing.")
        _save_plugin_overlay(plugin_file, _backend_overlay(backend, provider_keys))
        return
    plugin_text = plugin_file.read_text(encoding="utf-8")
    for key in provider_keys:
        if f"{key}=" not in plugin_text:
            raise CommandError(
                f"{plugin_file} does not define resolver entries for the configured provider model chain."
            )


def _validate_with_varlock(repo_root: pathlib.Path, *, check_only: bool) -> bool:
    """Validate the env contract through Varlock when available."""
    env_dir = varlock_env_dir(repo_root)
    varlock_bin = resolve_varlock_bin()
    if varlock_bin is None:
        if check_only:
            raise CommandError("Varlock is required to validate the env contract.")
        return False
    result = run_command([str(varlock_bin), "load", "--path", str(env_dir)], timeout_seconds=30)
    if not result.ok:
        detail = result.stderr.strip() or result.stdout.strip() or "Varlock env validation failed"
        raise CommandError(detail, result=None)
    return True


def configure_varlock_env(
    repo_root: pathlib.Path,
    *,
    check_only: bool,
    non_interactive: bool,
) -> dict[str, object]:
    """Create, normalize, and validate the StrongClaw env contract."""
    env_file = varlock_local_env_file(repo_root)
    env_dir = varlock_env_dir(repo_root)
    template_file = varlock_env_template_file(repo_root)
    created = False
    merged_keys: list[str] = []
    if not env_file.exists():
        if check_only:
            raise CommandError(f"Varlock local env contract not found at {env_file}.")
        if not template_file.exists():
            raise CommandError(f"Varlock env template not found at {template_file}.")
        env_dir.mkdir(parents=True, exist_ok=True)
        write_env_assignments(env_file, load_env_assignments(template_file))
        env_file.chmod(0o600)
        created = True
    merged_values, merged_keys = merge_env_template(
        target_path=env_file, template_path=template_file
    )
    del merged_values
    autofilled = _ensure_required_defaults(repo_root, check_only=check_only)
    if _interactive_mode(check_only=check_only, non_interactive=non_interactive):
        _prompt_secret_backend(repo_root)
        _prompt_model_chain(repo_root)
    _ensure_non_interactive_model_chain(
        repo_root,
        check_only=check_only,
        non_interactive=non_interactive,
    )
    _ensure_hypermemory_embedding_model(
        repo_root,
        check_only=check_only,
        non_interactive=non_interactive,
    )
    _validate_secret_backend_configuration(
        repo_root,
        check_only=check_only,
        non_interactive=non_interactive,
    )
    varlock_validated = _validate_with_varlock(repo_root, check_only=check_only)
    return {
        "ok": True,
        "checkOnly": check_only,
        "envFile": str(env_file),
        "pluginFile": str(varlock_plugin_env_file(repo_root)),
        "created": created,
        "mergedKeys": merged_keys,
        "autofilledValues": autofilled,
        "varlockValidated": varlock_validated,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the varlock-env CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_asset_root_argument(parser)
    parser.add_argument(
        "--env-mode",
        choices=READINESS_VARLOCK_ENV_MODES,
        default="managed",
        help="Varlock env source for readiness checks (default: managed).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    configure_parser = subparsers.add_parser("configure")
    configure_parser.add_argument("--non-interactive", action="store_true")
    subparsers.add_parser("check")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for env-contract management."""
    args = parse_args(argv)
    env_mode = cast(VarlockEnvMode, str(args.env_mode))
    with use_varlock_env_mode(env_mode):
        payload = configure_varlock_env(
            resolve_asset_root_argument(args, command_name="clawops varlock-env"),
            check_only=args.command == "check",
            non_interactive=bool(getattr(args, "non_interactive", False)),
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
