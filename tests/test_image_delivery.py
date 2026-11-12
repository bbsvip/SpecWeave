"""Pre-built GHCR images preserve the runtime contract of local-build Compose."""
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # Test-only dependency; CI installs it, the product does not need it.
    yaml = None

ROOT = Path(__file__).resolve().parents[1]


def _yaml(path):
    if yaml is None:
        pytest.skip('PyYAML is a CI/test-only dependency')
    # BaseLoader avoids YAML 1.1 treating the GitHub Actions `on` key as boolean.
    return yaml.load((ROOT / path).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


@pytest.mark.parametrize('build,prebuilt', [
    ('compose.external-db.yaml', 'compose.external-db.images.yaml'),
    ('compose.yaml', 'compose.images.yaml'),
])
def test_prebuilt_compose_matches_build_compose_except_build_image(build, prebuilt):
    baseline = _yaml(build)
    release = _yaml(prebuilt)
    assert baseline['name'] == release['name']  # Preserve Docker volume namespace.
    assert baseline.get('volumes') == release.get('volumes')
    assert baseline.get('networks') == release.get('networks')
    assert baseline['services'].keys() == release['services'].keys()
    for service, before in baseline['services'].items():
        after = release['services'][service]
        assert 'build' not in after, f'{service} must never rebuild on the server'
        if 'build' not in before:
            assert before == after
            continue
        original = {k: v for k, v in before.items() if k != 'build'}
        assert {k: v for k, v in after.items() if k != 'image'} == original
        expected_suffix = '-pi:' if service == 'pi-runner' else '-web:'
        assert after['image'].startswith('${SPECWEAVE_IMAGE_REPO:?Set SPECWEAVE_IMAGE_REPO in .env}')
        assert expected_suffix in after['image']
        assert after['image'].endswith('${SPECWEAVE_IMAGE_TAG:-main}')


def test_publish_workflow_has_two_images_and_release_tags():
    workflow = _yaml('.github/workflows/docker-publish.yml')
    assert 'workflow_dispatch' in workflow['on']
    assert workflow['on']['push']['tags'] == ['v*']
    assert 'main' in workflow['on']['push']['branches']
    assert workflow['jobs']['publish']['needs'] == 'validate'
    publish = workflow['jobs']['publish']
    assert publish['permissions']['packages'] == 'write'
    include = publish['strategy']['matrix']['include']
    assert {row['component'] for row in include} == {'web', 'pi'}
    assert {row['dockerfile'] for row in include} == {'Dockerfile', 'Dockerfile.runner'}
    steps = publish['steps']
    build = next(x for x in steps if x.get('uses', '').startswith('docker/build-push-action@'))
    assert build['with']['platforms'] == 'linux/amd64'
    assert build['with']['push'] == 'true'
    assert 'cache-from' in build['with'] and 'cache-to' in build['with']
    metadata = next(x for x in steps if x.get('uses', '').startswith('docker/metadata-action@'))
    assert 'type=ref,event=tag' in metadata['with']['tags']
    assert 'type=sha,format=short' in metadata['with']['tags']


def test_deploy_script_forbids_local_build():
    script = (ROOT / 'scripts/deploy-images.sh').read_text(encoding='utf-8')
    assert 'docker compose "${args[@]}" pull' in script
    assert 'docker compose "${args[@]}" up -d --no-build' in script
    assert 'compose.external-db.images.yaml' in script
    assert 'compose.images.yaml' in script
