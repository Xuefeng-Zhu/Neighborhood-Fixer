"""Build a static Amplify ZIP from deployed stack outputs; never deploy anything.

Required: --outputs <CDK outputs.json or AWS describe-stacks.json>, AWS_REGION,
and NF_LOCATION_API_KEY in private environment (a referrer-restricted PUBLIC map key).
Use --stack to select one stack when the file contains several. Outputs must contain
WebUrl, ApiUrl, AudioApiUrl, ClerkIssuerUrl, ClerkPublishableKey, AuthAudience,
and LocationMapName.
--web-origin may override WebUrl only after the same origin is registered in Clerk,
the backend authorized-party allowlist, API CORS, and the
Location key restriction. --install runs npm ci first; otherwise use installed pinned
dependencies. The manifest can configure scripts/aws_browser_smoke.mjs.

The ZIP intentionally contains the public restricted map key in its browser bundle.
The key is never included in logs or the manifest. Keep deployment artifacts private.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


class BuildError(Exception):
    """An error safe to display; never include external output or secret values."""


def read_outputs(document, stack_name=None):
    """Accept CDK outputs, describe-stacks, or one flat OutputKey/value mapping."""
    if not isinstance(document, dict):
        raise BuildError("Stack outputs must be a JSON object.")
    if "Stacks" in document:
        stacks = document["Stacks"]
        if stack_name:
            stacks = [item for item in stacks if item.get("StackName") == stack_name]
        if len(stacks) != 1:
            raise BuildError("Choose exactly one deployed stack with --stack.")
        return {
            item["OutputKey"]: item["OutputValue"]
            for item in stacks[0].get("Outputs", [])
        }
    if "ApiUrl" in document:
        return document
    if stack_name:
        selected = document.get(stack_name)
        if not isinstance(selected, dict):
            raise BuildError("The selected stack is absent from the outputs file.")
        return selected
    candidates = [
        item
        for item in document.values()
        if isinstance(item, dict) and "ApiUrl" in item
    ]
    if len(candidates) != 1:
        raise BuildError("Choose exactly one deployed stack with --stack.")
    return candidates[0]


def origin(value, name):
    try:
        url = urlparse(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
            or url.hostname in ("localhost", "127.0.0.1", "::1")
        ):
            raise ValueError()
        return f"https://{url.netloc}".rstrip("/")
    except (ValueError, TypeError, AttributeError):
        raise BuildError(f"{name} must be a deployed HTTPS origin.") from None


def deployed_base_url(value, name):
    """Validate a public HTTPS API base while preserving a safe stage path."""
    try:
        url = urlparse(value)
        segments = [segment for segment in url.path.split("/") if segment]
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.hostname in ("localhost", "127.0.0.1", "::1")
            or not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*/?", url.path)
            or any(segment in (".", "..") for segment in segments)
        ):
            raise ValueError()
        return f"https://{url.netloc}{url.path}".rstrip("/")
    except (ValueError, TypeError, AttributeError):
        raise BuildError(f"{name} must be a deployed HTTPS API base URL.") from None


def frontend_environment(outputs, env, web_origin=None):
    values = {}
    for key in (
        "WebUrl",
        "ApiUrl",
        "AudioApiUrl",
        "ClerkIssuerUrl",
        "ClerkPublishableKey",
        "AuthAudience",
        "LocationMapName",
    ):
        value = web_origin if key == "WebUrl" and web_origin else outputs.get(key)
        if not isinstance(value, str) or not value:
            raise BuildError(f"Stack outputs are missing {key}.")
        values[key] = value
    region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION")
    if not region or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
        raise BuildError("Set AWS_REGION to the deployed stack region.")
    key = env.get("NF_LOCATION_API_KEY")
    if not key or key.strip() != key or any(char.isspace() for char in key):
        raise BuildError(
            "Set NF_LOCATION_API_KEY privately to the restricted public map key."
        )
    issuer = origin(values["ClerkIssuerUrl"], "ClerkIssuerUrl")
    if not re.fullmatch(r"https://[a-zA-Z0-9-]+\.clerk\.accounts\.dev", issuer):
        raise BuildError(
            "ClerkIssuerUrl must identify the configured development instance."
        )
    clerk_key = values["ClerkPublishableKey"]
    try:
        if not re.fullmatch(r"pk_test_[A-Za-z0-9_-]+", clerk_key):
            raise ValueError()
        encoded = clerk_key.removeprefix("pk_test_")
        host = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        if host != urlparse(issuer).hostname + "$":
            raise ValueError()
    except (ValueError, UnicodeError):
        raise BuildError(
            "Clerk publishable key must match the configured issuer."
        ) from None
    if values["AuthAudience"] != "neighborhood-fixer-api":
        raise BuildError("AuthAudience must match the configured resident API.")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", values["LocationMapName"]):
        raise BuildError("LocationMapName is invalid.")
    origin(values["WebUrl"], "WebUrl")
    return {
        "VITE_API_BASE_URL": origin(values["ApiUrl"], "ApiUrl"),
        "VITE_AUDIO_API_BASE_URL": deployed_base_url(
            values["AudioApiUrl"], "AudioApiUrl"
        ),
        "VITE_AWS_REGION": region,
        "VITE_CLERK_PUBLISHABLE_KEY": clerk_key,
        "VITE_LOCATION_MAP_NAME": values["LocationMapName"],
        "VITE_LOCATION_API_KEY": key,
    }


def package_dist(dist, destination):
    """Root index.html, sorted paths, stable ZIP metadata, no enclosing dist directory."""
    if not (dist / "index.html").is_file():
        raise BuildError("Vite output is missing index.html.")
    paths = sorted(path for path in dist.rglob("*") if path.is_file())
    if any(
        path.is_symlink() or dist.resolve() not in path.resolve().parents
        for path in paths
    ):
        raise BuildError("Refusing to package a build containing symlinked files.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for path in paths:
                relative = path.relative_to(dist).as_posix()
                entry = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, path.read_bytes())
        temporary.chmod(0o600)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "files": len(paths),
    }


def build_command(command, env):
    # Build failures can echo source/config values. Return only fixed diagnostics,
    # never subprocess output, even when npm/Vite exits unsuccessfully.
    try:
        result = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BuildError(
            "Build command could not finish; verify Node/npm and pinned dependencies."
        ) from None
    if result.returncode:
        raise BuildError(
            "Frontend build failed; diagnostics suppressed to protect configuration."
        )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--outputs", required=True, type=Path)
    parser.add_argument("--stack")
    parser.add_argument("--web-origin")
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".local/amplify/frontend.zip"
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install exact lockfile dependencies with npm ci first.",
    )
    args = parser.parse_args(argv)
    try:
        outputs = read_outputs(json.loads(args.outputs.read_text()), args.stack)
        frontend = frontend_environment(outputs, os.environ, args.web_origin)
        # Frontend processes need no AWS credentials. Replace inherited VITE values
        # so the stack configuration takes precedence over ambient shell settings.
        child_env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("AWS_", "NF_", "VITE_", "CLERK_"))
        }
        child_env.update(frontend)
        if args.install:
            build_command(["npm", "ci"], child_env)
        build_command(["npm", "run", "build", "--workspace", "apps/web"], child_env)
        metadata = package_dist(ROOT / "apps/web/dist", args.output)
        manifest = {
            "schema_version": 1,
            "artifact": {"filename": args.output.name, **metadata},
            "frontend": {
                "web_url": origin(args.web_origin or outputs["WebUrl"], "WebUrl"),
                "api_url": frontend["VITE_API_BASE_URL"],
                "audio_api_url": frontend["VITE_AUDIO_API_BASE_URL"],
                "aws_region": frontend["VITE_AWS_REGION"],
                "clerk_issuer": origin(outputs["ClerkIssuerUrl"], "ClerkIssuerUrl"),
                "clerk_publishable_key": frontend["VITE_CLERK_PUBLISHABLE_KEY"],
                "auth_audience": outputs["AuthAudience"],
                "location_map_name": frontend["VITE_LOCATION_MAP_NAME"],
            },
        }
        manifest_path = args.output.with_suffix(".manifest.json")
        with open(
            manifest_path, "w", opener=lambda path, flags: os.open(path, flags, 0o600)
        ) as file:
            json.dump(manifest, file, indent=2)
            file.write("\n")
        manifest_path.chmod(0o600)
        print(
            "Built Amplify ZIP with index.html at archive root; no deployment performed."
        )
        print(f"Files: {metadata['files']}; SHA-256: {metadata['sha256']}")
        print(
            "Artifact and smoke-test manifest written to the requested output location."
        )
        return 0
    except BuildError as error:
        print(f"Build stopped: {error}", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "Build stopped: outputs or artifact files could not be processed.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
