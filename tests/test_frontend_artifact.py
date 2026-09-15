"""Offline regression tests for the deployment artifact boundary, no AWS requests."""

import base64
import json
import zipfile
from subprocess import CompletedProcess

import pytest

from scripts.build_amplify_artifact import (
    BuildError,
    build_command,
    frontend_environment,
    main,
    package_dist,
    read_outputs,
)

OUTPUTS = {
    "WebUrl": "https://main.example.amplifyapp.com",
    "ApiUrl": "https://example.execute-api.us-west-2.amazonaws.com",
    "AudioApiUrl": "https://audio.execute-api.us-west-2.amazonaws.com/audio/",
    "ClerkIssuerUrl": "https://example.clerk.accounts.dev",
    "ClerkPublishableKey": "pk_test_"
    + base64.urlsafe_b64encode(b"example.clerk.accounts.dev$").decode().rstrip("="),
    "AuthAudience": "neighborhood-fixer-api",
    "LocationMapName": "NeighborhoodFixer",
}
PUBLIC_ENV = {
    "AWS_REGION": "us-west-2",
    "NF_LOCATION_API_KEY": "restricted-public-test-key",
}


def test_reads_cdk_and_cloudformation_outputs_without_guessing_stack():
    assert read_outputs({"NeighborhoodFixer": OUTPUTS}) == OUTPUTS
    assert (
        read_outputs({"Assets": {"AssetUri": "example"}, "NeighborhoodFixer": OUTPUTS})
        == OUTPUTS
    )
    document = {
        "Stacks": [
            {
                "StackName": "NeighborhoodFixer",
                "Outputs": [
                    {"OutputKey": key, "OutputValue": value}
                    for key, value in OUTPUTS.items()
                ],
            }
        ]
    }
    assert read_outputs(document) == OUTPUTS
    with pytest.raises(BuildError, match="Choose exactly one"):
        read_outputs({"First": OUTPUTS, "Second": OUTPUTS})
    assert read_outputs({"First": OUTPUTS, "Second": OUTPUTS}, "First") == OUTPUTS


def test_sets_exact_callback_and_requires_restricted_key_and_deployed_origins():
    frontend = frontend_environment(OUTPUTS, PUBLIC_ENV)
    assert frontend["VITE_CLERK_PUBLISHABLE_KEY"] == OUTPUTS["ClerkPublishableKey"]
    assert not any("COGNITO" in name for name in frontend)
    assert frontend["VITE_AWS_REGION"] == "us-west-2"
    assert frontend["VITE_AUDIO_API_BASE_URL"] == OUTPUTS["AudioApiUrl"].rstrip("/")
    with pytest.raises(BuildError, match="NF_LOCATION_API_KEY"):
        frontend_environment(OUTPUTS, {"AWS_REGION": "us-west-2"})
    with pytest.raises(BuildError, match="AudioApiUrl"):
        frontend_environment(
            {key: value for key, value in OUTPUTS.items() if key != "AudioApiUrl"},
            PUBLIC_ENV,
        )
    for url in (
        "http://audio.example.com/audio",
        "https://audio.example.com/audio?token=secret",
        "https://user@audio.example.com/audio",
        "https://audio.example.com/audio/../buffered",
    ):
        with pytest.raises(BuildError) as error:
            frontend_environment({**OUTPUTS, "AudioApiUrl": url}, PUBLIC_ENV)
        assert "secret" not in str(error.value)
    for url in (
        "http://localhost:5173",
        "https://localhost",
        "https://a:b@example.com",
        "https://example.com/?key=secret",
    ):
        with pytest.raises(BuildError) as error:
            frontend_environment({**OUTPUTS, "WebUrl": url}, PUBLIC_ENV)
        assert "secret" not in str(error.value)


def test_zip_is_deterministic_and_has_root_index_and_private_permissions(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<script src="/assets/main.js"></script>')
    (dist / "assets/main.js").write_text("console.log('compiled')")
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    assert package_dist(dist, first) == package_dist(dist, second)
    assert first.read_bytes() == second.read_bytes()
    assert first.stat().st_mode & 0o777 == 0o600
    with zipfile.ZipFile(first) as artifact:
        assert artifact.namelist() == ["assets/main.js", "index.html"]
        assert all(
            item.date_time == (1980, 1, 1, 0, 0, 0) for item in artifact.infolist()
        )


def test_external_symlink_is_never_packaged(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("app")
    outside = tmp_path / "private.txt"
    outside.write_text("secret-placeholder")
    (dist / "external.txt").symlink_to(outside)
    with pytest.raises(BuildError, match="symlinked files"):
        package_dist(dist, tmp_path / "frontend.zip")


def test_failed_build_diagnostics_do_not_expose_child_output(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.build_amplify_artifact.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(
            [], 1, "secret-placeholder", "secret-placeholder"
        ),
    )
    with pytest.raises(BuildError) as error:
        build_command(["npm", "run", "build"], PUBLIC_ENV)
    assert "secret-placeholder" not in str(error.value)
    assert not capsys.readouterr().out


def test_main_keeps_aws_secrets_out_of_build_process_and_map_key_out_of_manifest(
    tmp_path, monkeypatch, capsys
):
    dist = tmp_path / "apps/web/dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("compiled frontend")
    source = tmp_path / "outputs.json"
    source.write_text(json.dumps({"NeighborhoodFixer": OUTPUTS}))
    monkeypatch.setattr("scripts.build_amplify_artifact.ROOT", tmp_path)
    monkeypatch.setenv("AWS_REGION", PUBLIC_ENV["AWS_REGION"])
    monkeypatch.setenv("NF_LOCATION_API_KEY", PUBLIC_ENV["NF_LOCATION_API_KEY"])
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret-placeholder")
    monkeypatch.setenv("CLERK_SECRET_KEY", "clerk-secret-placeholder")
    monkeypatch.setenv("NF_AWS_SMOKE_PASSWORD", "account-secret-placeholder")
    monkeypatch.setenv("VITE_API_BASE_URL", "https://wrong.example.com")
    monkeypatch.setenv("VITE_AUDIO_API_BASE_URL", "https://wrong-audio.example.com")
    calls = []
    monkeypatch.setattr(
        "scripts.build_amplify_artifact.build_command",
        lambda command, env: calls.append(env),
    )
    destination = tmp_path / "artifact/frontend.zip"
    assert main(["--outputs", str(source), "--output", str(destination)]) == 0
    assert calls[0]["VITE_API_BASE_URL"] == OUTPUTS["ApiUrl"]
    assert calls[0]["VITE_AUDIO_API_BASE_URL"] == OUTPUTS["AudioApiUrl"].rstrip("/")
    assert calls[0]["VITE_LOCATION_API_KEY"] == PUBLIC_ENV["NF_LOCATION_API_KEY"]
    assert "AWS_SECRET_ACCESS_KEY" not in calls[0]
    assert "NF_AWS_SMOKE_PASSWORD" not in calls[0]
    assert "CLERK_SECRET_KEY" not in calls[0]
    manifest = destination.with_suffix(".manifest.json").read_text()
    logs = capsys.readouterr()
    for value in (
        PUBLIC_ENV["NF_LOCATION_API_KEY"],
        "aws-secret-placeholder",
        "account-secret-placeholder",
        "clerk-secret-placeholder",
    ):
        assert value not in manifest
        assert value not in logs.out + logs.err
    assert json.loads(manifest)["frontend"]["web_url"] == OUTPUTS["WebUrl"]


@pytest.mark.parametrize(
    "key",
    [
        "sk_test_private",
        "pk_test_invalid",
        "pk_live_wrong",
        "pk_test_"
        + base64.urlsafe_b64encode(b"other.clerk.accounts.dev$").decode().rstrip("="),
    ],
)
def test_clerk_configuration_cannot_bind_a_different_instance_or_secret_key(key):
    with pytest.raises(BuildError) as error:
        frontend_environment({**OUTPUTS, "ClerkPublishableKey": key}, PUBLIC_ENV)
    assert key not in str(error.value)
