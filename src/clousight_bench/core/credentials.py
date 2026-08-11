"""Credential resolution + provider registry (convenience layer).

Philosophy: never make a user mint a *new* secret just for a benchmark. Reuse
the cloud's own default credential chain (env vars / CLI profile files / roles),
exactly what `aws`, `aliyun`, etc. already read. This module only *inspects*
where credentials would come from -- it never reads or stores the secret value.
Real adapters still hand off to the official SDK's chain at call time; this
layer powers `csbench init` / `csbench doctor` and adapter self-reporting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# provider -> where its default credential chain looks (non-secret metadata).
PROVIDER_CREDENTIALS: dict[str, dict[str, Any]] = {
    "aws": {
        "std_env": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        "profile_env": "AWS_PROFILE",
        "cred_files": ["~/.aws/credentials", "~/.aws/config"],
        "sdk_module": "boto3",
        "docs": "https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html",
    },
    "aliyun": {
        "std_env": ["ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET"],
        "profile_env": "ALIBABA_CLOUD_PROFILE",
        "cred_files": ["~/.alibabacloud/credentials", "~/.aliyun/config.json"],
        "sdk_module": "alibabacloud_credentials",
        "docs": "https://help.aliyun.com/zh/sdk/developer-reference/configure-credentials",
    },
    "huawei": {
        "std_env": ["HUAWEICLOUD_SDK_AK", "HUAWEICLOUD_SDK_SK"],
        "profile_env": "",
        "cred_files": [],
        "sdk_module": "huaweicloudsdkcore",
        "docs": "https://support.huaweicloud.com/devg-apisign/api-sign-sdk.html",
    },
    "volcengine": {
        "std_env": ["VOLC_ACCESSKEY", "VOLC_SECRETKEY"],
        "profile_env": "",
        "cred_files": ["~/.volc/config"],
        "sdk_module": "volcengine",
        "docs": "https://www.volcengine.com/docs/6291/65568",
    },
}


def infer_provider(target: dict[str, Any], platform: str | None = None) -> str | None:
    """Provider from explicit target['provider'] or a platform name prefix
    (e.g. 'aliyun-agentrun' -> 'aliyun', 'aws-emr' -> 'aws')."""
    if target.get("provider"):
        return str(target["provider"])
    name = platform or ""
    for provider in PROVIDER_CREDENTIALS:
        if name.startswith(provider):
            return provider
    return None


@dataclass
class CredentialResolution:
    provider: str | None
    ok: bool
    source: str  # "auth_env" | "profile" | "std_env" | "cred_file" | "none" | "unknown-provider"
    identity_hint: str = ""  # non-secret hint (var names / profile / file), never a secret
    remediation: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


def _all_env_set(names: list[str]) -> bool:
    return bool(names) and all(os.environ.get(n) for n in names)


def resolve_credentials(target: dict[str, Any], platform: str | None = None) -> CredentialResolution:
    """Report where credentials *would* come from. Order:
    explicit auth_env -> CLI profile -> standard env vars -> credential file.
    """
    provider = infer_provider(target, platform)
    if provider is None:
        return CredentialResolution(
            provider=None,
            ok=False,
            source="unknown-provider",
            remediation="set target.provider to one of: " + ", ".join(PROVIDER_CREDENTIALS),
        )
    spec = PROVIDER_CREDENTIALS[provider]

    # 1) explicit escape hatch: auth_env maps logical names -> env var names.
    auth_env = target.get("auth_env") or {}
    if auth_env:
        env_names = [str(v) for v in auth_env.values()]
        if _all_env_set(env_names):
            return CredentialResolution(
                provider,
                True,
                "auth_env",
                identity_hint="env:" + ",".join(env_names),
            )
        missing = [n for n in env_names if not os.environ.get(n)]
        return CredentialResolution(
            provider,
            False,
            "auth_env",
            identity_hint="env:" + ",".join(env_names),
            remediation=f"export the missing env var(s): {', '.join(missing)}",
            detail={"missing_env": missing},
        )

    # 2) CLI profile explicitly requested.
    profile = target.get("profile") or (os.environ.get(spec["profile_env"]) if spec["profile_env"] else None)
    if profile:
        return CredentialResolution(
            provider,
            True,
            "profile",
            identity_hint=f"profile:{profile}",
            detail={"profile": profile},
        )

    # 3) standard env vars of the provider's default chain.
    if _all_env_set(spec["std_env"]):
        return CredentialResolution(
            provider,
            True,
            "std_env",
            identity_hint="env:" + ",".join(spec["std_env"]),
        )

    # 4) credential file on disk (profile "default" assumed by the SDK).
    for cf in spec["cred_files"]:
        if Path(cf).expanduser().exists():
            return CredentialResolution(
                provider,
                True,
                "cred_file",
                identity_hint=f"file:{cf}",
                detail={"cred_file": cf},
            )

    return CredentialResolution(
        provider,
        False,
        "none",
        remediation=(
            f"provide {provider} credentials via any of: "
            f"export {' & '.join(spec['std_env'])}; or set target.profile; "
            f"or run the provider CLI login. Docs: {spec['docs']}"
        ),
        detail={"std_env": spec["std_env"], "docs": spec["docs"]},
    )
