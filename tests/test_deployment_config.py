from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentConfigTests(unittest.TestCase):
    def test_linux_installer_supports_interactive_domain_and_renewal(self):
        script = (ROOT / "install-linux.sh").read_text(encoding="utf-8")
        self.assertIn("prompt_domain", script)
        self.assertIn("certbot", script)
        self.assertIn("renewal-hooks/deploy/sgcc-platform-nginx", script)
        self.assertIn("return 301 https://\\$host\\$request_uri", script)

    def test_docker_tls_overlay_uses_caddy_and_keeps_backend_local(self):
        base = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        tls = (ROOT / "docker-compose.tls.yml").read_text(encoding="utf-8")
        installer = (ROOT / "install-docker.sh").read_text(encoding="utf-8")
        self.assertIn("PLATFORM_BIND", base)
        self.assertIn("caddy:2.10-alpine", tls)
        self.assertIn('write_env_value PLATFORM_BIND 127.0.0.1', installer)
        self.assertIn("docker-compose.tls.yml", installer)
        self.assertIn("prompt_domain", installer)

    def test_installers_use_requested_default_credentials(self):
        linux = (ROOT / "install-linux.sh").read_text(encoding="utf-8")
        docker = (ROOT / "install-docker.sh").read_text(encoding="utf-8")
        legacy = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("ADMIN_PASSWORD=admin", linux)
        self.assertIn("write_env_value ADMIN_PASSWORD admin", docker)
        self.assertIn("ADMIN_PASSWORD=admin", legacy)


if __name__ == "__main__":
    unittest.main()
