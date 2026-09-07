# Build, Install, and Upgrade Evidence — RC-1

- `scripts/build_release.ps1 -Version 2.5.1`: PASS.
- Outputs: PyInstaller desktop package, License Studio, portable ZIP, Inno Setup installer, SHA-256 manifest.
- Manifest verification: all three artifacts matched (`manifest_verification.json`).
- Packaged key scan: zero private-key files; one expected `license_public_key.pem`.
- `scripts/test_installer.ps1 -Version 2.5.1`: technical PASS for install, first smoke, reinstall/update, data preservation, uninstall, and runtime dependencies.
- Clean-install security is FAIL because the smoke confirms `admin/admin123` and no forced change.
- Real artifact upgrade 2.5.0 → 2.5.1: PASS with `QA_RC1_UPGRADE_250_TO_251` client marker, DB integrity `ok`, zero FK violations, and preserved user database after uninstall.

Evidence: `build/installer-smoke/installer-smoke.json`, `build/upgrade-smoke/upgrade-smoke.json`, `build_key_scan.json`, `manifest_verification.json`.
