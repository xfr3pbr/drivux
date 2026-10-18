# Drivux

**On-demand Google Drive for Linux, with a simple interface.**

Drivux shows all your Google Drive files in a folder on your computer, the way
Google Drive for desktop does on Windows. Each file is only downloaded when you open it.
Under the hood it uses [rclone](https://rclone.org); Drivux takes care of the setup
and the user experience.

## Features (v0.1)

- Connects your Google account through the browser, no config files to edit.
- Mounts Drive in a folder (default: `~/GoogleDrive`) that comes up with the computer.
- Opened files are cached (up to 10 GB; removed after 30 days without use).
- At startup it loads the whole folder tree at once, so opening folders is instant afterwards.
- Local changes upload in ~1 s; changes made on Drive show up in ~10 s.
- GTK4/libadwaita interface (same look as Baobab) with status, account storage,
  cache usage, pause, start with the computer and disconnect.
- Desktop notifications showing what is still syncing (↑ uploading, ↓ downloading),
  updated in place, plus a separate alert when something fails.
- Command line: `drivux setup | status | start | stop | remove | notify`.

## Installation

Download the package for your distribution from the
[Releases](https://github.com/xfr3pbr/drivux/releases) page and install it:

```bash
sudo apt install ./drivux_*_all.deb              # Ubuntu, Debian, Mint, Pop!_OS
sudo dnf install ./drivux-*.noarch.rpm           # Fedora
sudo pacman -U ./drivux-*-any.pkg.tar.zst        # Arch, Manjaro, EndeavourOS, Omarchy
```

Dependencies (rclone, FUSE, GTK, libadwaita) are installed along with it. Then open
**Drivux** from your applications menu.

On other distributions with systemd, install the dependencies (table below) and run:

```bash
git clone https://github.com/xfr3pbr/drivux && cd drivux
sudo make install PREFIX=/usr
```

Packaging details, and why there is no Flatpak/AppImage: [packaging/README.md](packaging/README.md).

## Compatibility

Drivux is meant to work on **any Linux distribution** that has:

| Requirement | Why |
|---|---|
| **systemd** (user services) | keeps the folder mounted and starts it with the computer |
| **FUSE 3** | lets the folder show files that live in the cloud |
| **rclone 1.60+** | talks to Google Drive |
| **Python 3.11+, PyGObject, GTK 4, libadwaita 1.5+** | user interface |

Notifications use the freedesktop standard and work on GNOME, KDE, XFCE, Cinnamon,
Hyprland, Sway, etc. Program paths are detected on the system, not hardcoded.

If something is missing, Drivux shows the right install command for your distribution
(apt, dnf, pacman, zypper, apk, xbps, emerge).

| Distribution | Status |
|---|---|
| Arch, Manjaro, EndeavourOS, Omarchy | tested (Omarchy/Hyprland) |
| Fedora 40+, openSUSE Tumbleweed | should work (recent packages) |
| Ubuntu 24.04+, Debian 13+, Mint 22+ | should work; if the repository rclone is too old, Drivux points to the official installer |
| Debian 12, Ubuntu 22.04 and older | libadwaita too old for the interface (`drivux setup` from the terminal works) |
| Without systemd (Alpine, Void, Devuan, Gentoo with OpenRC) | not supported yet |

Installing the dependencies:

```bash
sudo pacman -S rclone fuse3 python-gobject gtk4 libadwaita                       # Arch
sudo dnf install rclone fuse3 python3-gobject gtk4 libadwaita                    # Fedora
sudo apt install rclone fuse3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1             # Ubuntu/Debian
sudo zypper install rclone fuse3 python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1  # openSUSE
```

## Running from source

```bash
PYTHONPATH=src python3 -m drivux          # opens the interface
PYTHONPATH=src python3 -m drivux setup    # sets up from the terminal
PYTHONPATH=src python3 -m drivux status
```

Tests:

```bash
make test
```

## How it works

| Piece | Location |
|---|---|
| Google account (rclone remote `drivux:`) | `~/.config/rclone/rclone.conf` |
| Drivux settings | `~/.config/drivux/config.json` |
| Service that mounts the folder | `~/.config/systemd/user/drivux.service` |
| Notification service | `~/.config/systemd/user/drivux-notify.service` |
| Cache of opened files, and log | `~/.cache/drivux/` |
| rclone control socket (only your user can access it) | `$XDG_RUNTIME_DIR/drivux.sock` |

Disconnecting removes the services, the cache and the account access.
**Nothing is deleted from Google Drive.**

> **Warning:** deleting a file **inside the folder** deletes it from Google Drive too
> (it goes to the Drive trash).

## Known limitations

- Native Google documents (Docs, Sheets, Forms) do not show up in the folder.
- Offline, only files already in the cache can be opened.
- rclone's default Google Drive access (shared client ID) is being retired in 2026.
  To avoid depending on it, create your own client ID in Google Cloud and enter it
  under **Advanced** on the setup screen (or `drivux setup --client-id ... --client-secret ...`).

## Roadmap

- [ ] Wizard to create your own Google Cloud client ID
- [ ] Status icons on files (cloud / available offline) in Nautilus
- [ ] "Keep offline" from the right-click menu
- [ ] Tray icon with progress and error alerts
- [ ] Persist the folder list to disk so it is instant right after boot
- [x] .deb, .rpm and Arch packages, install-tested in CI on Ubuntu, Debian, Fedora and Arch
- [ ] Publish to the AUR (new AUR account registration is temporarily closed)
- [ ] Support for systems without systemd
- [ ] Other services supported by rclone (OneDrive, Dropbox…)

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
