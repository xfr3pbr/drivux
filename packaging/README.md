# Packaging Drivux

| Format | Distributions | How it is built |
|---|---|---|
| `.deb` | Ubuntu 24.04+, Debian 13+, Mint 22+, Pop!_OS | nFPM (`packaging/nfpm.yaml`), in GitHub Actions |
| `.rpm` | Fedora 40+ | nFPM (`packaging/nfpm.yaml`), in GitHub Actions |
| `.pkg.tar.zst` | Arch, Manjaro, EndeavourOS, Omarchy | `packaging/arch/PKGBUILD` (makepkg), in GitHub Actions |
| AUR | Arch and derivatives | same `PKGBUILD`, published by hand (see below) |

All of them use the same layout, created by `make install`, which does not depend
on the distribution's Python version:

```
/usr/bin/drivux                 launcher
/usr/share/drivux/drivux/       code
/usr/share/applications/        menu entry
/usr/share/licenses/drivux/     license
```

## Releasing a new version

1. Bump the version in `src/drivux/__init__.py`, `pyproject.toml` and
   `packaging/arch/PKGBUILD` (`pkgver`, and `pkgrel=1`).
2. Commit and tag:
   ```bash
   git tag v0.2.0 && git push && git push --tags
   ```
3. The **Packages** workflow runs the tests, builds the `.deb`, `.rpm` and Arch
   packages, installs them on Ubuntu, Debian, Fedora and Arch and, if everything
   passes, publishes them on the Releases page. The tag must match `__version__`,
   otherwise the workflow stops.

## Publishing to the AUR

> As of 2026-10-18, new AUR account registration was temporarily closed.
> Meanwhile, the Arch package is published ready-made on the Releases page.

First time only: create an account at https://aur.archlinux.org, add your SSH
public key to your profile and clone the empty repository:

```bash
git clone ssh://aur@aur.archlinux.org/drivux.git ~/aur/drivux
```

For each version (after the tag exists on GitHub):

```bash
cp packaging/arch/PKGBUILD ~/aur/drivux/
cd ~/aur/drivux
updpkgsums                          # replaces SKIP with the sha256 of the tag tarball
makepkg -f                          # builds and runs the tests
makepkg --printsrcinfo > .SRCINFO
git add PKGBUILD .SRCINFO
git commit -m "drivux 0.2.0"
git push
```

After `updpkgsums`, copy `sha256sums` back into `packaging/arch/PKGBUILD` in the
Drivux repository.

## Why no Flatpak or AppImage (for now)

Drivux is not a self-contained app: it **creates systemd services**, **mounts
folders with FUSE** and calls the **system rclone**.

- **Flatpak** runs in a sandbox that blocks exactly that. The sandbox could be
  bypassed with `flatpak-spawn --host`, but that defeats the point of Flatpak, and
  Flathub usually rejects such apps.
- **AppImage** changes path on every run (`/tmp/.mount_xxxx`), and the notification
  service points at the Drivux executable, so that path would break. It would also
  need to bundle GTK 4 and libadwaita, making it large and fragile.

Native packages cover the vast majority of distributions with systemd. On the others,
install from source with `sudo make install PREFIX=/usr`.
