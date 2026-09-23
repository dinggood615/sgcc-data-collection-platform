from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentConfigTests(unittest.TestCase):
    def test_linux_installer_uses_direct_http_without_a_proxy(self):
        script = (ROOT / "install-linux.sh").read_text(encoding="utf-8")
        self.assertIn("--host 0.0.0.0 --port $PORT", script)
        self.assertNotIn("certbot", script)
        self.assertNotIn(" nginx ", script)
        self.assertNotIn("certbot", script)

    def test_docker_installer_uses_direct_http_without_a_tls_overlay(self):
        base = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        installer = (ROOT / "install-docker.sh").read_text(encoding="utf-8")
        self.assertIn("PLATFORM_BIND", base)
        self.assertIn('write_env_value PLATFORM_BIND 0.0.0.0', installer)
        self.assertNotIn("docker-compose.tls.yml", installer)

    def test_installers_use_requested_default_credentials(self):
        linux = (ROOT / "install-linux.sh").read_text(encoding="utf-8")
        docker = (ROOT / "install-docker.sh").read_text(encoding="utf-8")
        legacy = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("ADMIN_PASSWORD=admin", linux)
        self.assertIn("write_env_value ADMIN_PASSWORD admin", docker)
        self.assertIn("ADMIN_PASSWORD=admin", legacy)


if __name__ == "__main__":
    unittest.main()
