"""Integration test for IP Ban Manager."""

import os
import shutil
import subprocess
import time
from importlib.util import find_spec
from pathlib import Path

import requests
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from urllib3.exceptions import ProtocolError

env = Environment(loader=FileSystemLoader("config"), autoescape=select_autoescape())


def docker_compose_command() -> list[str]:
    """Return the available Docker Compose command."""
    if shutil.which("docker") is not None:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return ["docker", "compose"]

    if shutil.which("docker-compose") is not None:
        return ["docker-compose"]

    raise RuntimeError("Docker Compose is required for integration tests")


DOCKER_COMPOSE = docker_compose_command()

config_folder = Path("config")
ban_ip_path = config_folder.joinpath("ip_bans.yaml")
deps_folder = config_folder.joinpath("deps")

root = Path(__file__).parent.parent
new_custom_components = config_folder.joinpath("custom_components", "ip_ban_manager")
if new_custom_components.exists():
    shutil.rmtree(new_custom_components)

shutil.copytree(
    root.joinpath("custom_components", "ip_ban_manager"), new_custom_components
)


def prepare_dependency(package_name: str) -> None:
    """Copy a test dependency into Home Assistant's config deps folder."""
    spec = find_spec(package_name)
    if spec is None or spec.origin is None:
        raise RuntimeError(f"{package_name} is required for integration tests")

    package_path = Path(spec.origin).parent
    deps_folder.mkdir(exist_ok=True)
    target_path = deps_folder / package_path.name
    if target_path.exists():
        shutil.rmtree(target_path)
    shutil.copytree(package_path, target_path)

    for dist_info in package_path.parent.glob(f"{package_name}-*.dist-info"):
        target_dist_info = deps_folder / dist_info.name
        if target_dist_info.exists():
            shutil.rmtree(target_dist_info)
        shutil.copytree(dist_info, target_dist_info)


prepare_dependency("maxminddb")


def wait_for_http(port: int, host: str = "localhost", timeout: float = 20.0):
    """Wait for a particular HTTP host/port to start responding to GETs."""
    start_time = time.time()
    print(f"Waiting for http://{host}:{port}")
    while True:
        try:
            res = requests.get(f"http://{host}:{port}")
            res.raise_for_status()
            break
        except requests.exceptions.ConnectionError as ex:
            if not isinstance(ex.args[0], ProtocolError):
                print("Waiting", ex.args)
            if time.time() - start_time >= timeout:
                logs = subprocess.check_output(
                    [*DOCKER_COMPOSE, "logs"], encoding="utf-8"
                )
                print("logs")
                print(logs)
                raise TimeoutError(
                    "Waited too long for the port {} on host {} to start accepting "
                    "connections.".format(port, host)
                ) from ex
            time.sleep(0.1)
        except requests.exceptions.HTTPError as he:
            print(he)
            time.sleep(0.1)
            if he.response.status_code == 404:
                continue
            else:
                raise
        except Exception:
            logs = subprocess.check_output([*DOCKER_COMPOSE, "logs"], encoding="utf-8")
            print("logs")
            print(logs)
            raise


def wait_for_log_line(pattern: str, timeout: float = 20.0) -> None:
    """Wait for Docker Compose logs to contain a setup marker."""
    start_time = time.time()
    while True:
        logs = subprocess.check_output([*DOCKER_COMPOSE, "logs"], encoding="utf-8")
        if pattern in logs:
            return
        if time.time() - start_time >= timeout:
            print("logs")
            print(logs)
            raise TimeoutError(f"Timed out waiting for log line: {pattern}")
        time.sleep(0.1)


def configure_ha(allowlist: list[str], ip_ban_enabled: bool = True) -> None:
    """Configure home-assistant with a particular allowlist."""
    configuration_template = env.get_template("configuration.yaml.j2")
    with config_folder.joinpath("configuration.yaml").open("w") as config_out:
        config_out.write(
            configuration_template.render(
                ALLOWLIST=allowlist, IP_BAN_ENABLED=ip_ban_enabled
            )
        )

    subprocess.check_call([*DOCKER_COMPOSE, "down"])

    for dirpath, dirnames, filenames in config_folder.walk(top_down=True):
        if "custom_components" in dirnames:
            dirnames.remove("custom_components")
        if "deps" in dirnames:
            dirnames.remove("deps")
        keep_filenames = {
            "configuration.yaml.j2",
            "configuration.yaml",
            "home-assistant.log",
            ".gitignore",
            "auth",
            "auth_provider.homeassistant",
            "core.restore_state",
            "onboarding",
            "person",
        }
        delete_files = set(filenames) - keep_filenames
        if len(delete_files) == 0:
            continue
        print("deleting", delete_files)
        for filename in delete_files:
            delete_path = dirpath.joinpath(filename)
            delete_path.unlink()

    subprocess.check_call(
        [*DOCKER_COMPOSE, "up", "-d"],
        env={**os.environ, "UID": str(os.getuid()), "GID": str(os.getgid())},
    )
    wait_for_http(8123)
    wait_for_log_line("Home Assistant initialized")


def check_res(expected_results: list[int]):
    """
    Check that talking to HA gets a particular set of statuses.

    This is so we can check banning happens on later requests.
    """
    try:
        for index in range(len(expected_results)):
            # Tried less terrible URLs and they don't seem to reliably work
            # Or like /api/ just always give 403s
            # This one to a generally non-existant login flow, seems to work reliably
            res = requests.post(
                "http://localhost:8123/auth/login_flow/b4b20b5004a6baa2a1d903de46886ed2",
                json={"client_id": "http://localhost:8123/"},
            )
            assert res.ok is False, (res, res.text)
            assert res.status_code == expected_results[index], (
                res.status_code,
                expected_results[index],
                res.text,
            )
    finally:
        subprocess.check_call([*DOCKER_COMPOSE, "down"])


def check_icon_route(timeout: float = 10.0) -> None:
    """Check the notification icon route serves the integration icon."""
    start_time = time.time()
    last_response: requests.Response | None = None
    while True:
        res = requests.get("http://localhost:8123/api/ip_ban_manager/icon.png")
        last_response = res
        if (
            res.ok
            and res.headers["content-type"].startswith("image/png")
            and res.content
        ):
            return
        if time.time() - start_time >= timeout:
            assert last_response is not None
            assert last_response.ok, (last_response.status_code, last_response.text)
            assert last_response.headers["content-type"].startswith("image/png")
            assert len(last_response.content) > 0
        time.sleep(0.1)


def check_logs() -> None:
    """Check the logs for errors."""
    log_path = Path(__file__).parent.joinpath("config", "home-assistant.log")
    if not log_path.exists():
        return
    data = log_path.open().read()
    assert "ERROR" not in data, data


# Disable banning entirely first, and make sure plugin works with an IP set
configure_ha(["1.1.1.1"], ip_ban_enabled=False)
check_res([404, 404])
check_logs()

# Enable banning
configure_ha([])
check_icon_route()
check_res([404, 403])  # Second is after banning
check_logs()

ban_ip_file = ban_ip_path.open()
ban_ips: dict[str, object] = yaml.safe_load(ban_ip_file)
assert len(ban_ips) == 1, ban_ips
ban_ip = list(ban_ips.keys())[0]
print(f"Banned ip is {ban_ip}")

configure_ha([ban_ip])
check_icon_route()
check_res([404, 404])
check_logs()

assert not ban_ip_path.exists()
