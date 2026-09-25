from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentConfigTests(unittest.TestCase):
    def test_linux_installer_uses_direct_http_without_a_proxy(self):
        script = (ROOT / "install-linux.sh").read_text(encoding="utf-8")
        self.assertIn("--host 0.0.0.0 --port $PORT", script)
        self.assertIn("remove_legacy_proxy_config", script)
        self.assertIn("/etc/nginx/sites-enabled/sgcc-platform", script)
        self.assertNotIn("certbot", script)
        self.assertNotIn("certbot", script)

    def test_docker_installer_uses_direct_http_without_a_tls_overlay(self):
        base = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        installer = (ROOT / "install-docker.sh").read_text(encoding="utf-8")
        self.assertIn("PLATFORM_BIND", base)
        self.assertIn('write_env_value PLATFORM_BIND 0.0.0.0', installer)
        self.assertIn("remove_legacy_caddy", installer)

    def test_installers_do_not_create_default_login_credentials(self):
        linux = (ROOT / "install-linux.sh").read_text(encoding="utf-8")
        docker = (ROOT / "install-docker.sh").read_text(encoding="utf-8")
        self.assertNotIn("ADMIN_PASSWORD", linux)
        self.assertNotIn("ADMIN_PASSWORD", docker)


if __name__ == "__main__":
    unittest.main()
