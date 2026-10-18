# Installs Drivux in a layout that does not depend on the Python version:
#   /usr/bin/drivux                launcher
#   /usr/share/drivux/drivux/      code
#   /usr/share/applications/       menu entry
#   /usr/share/nautilus-python/extensions/   right-click menu in Nautilus (optional)
# Used by the packages (.deb, .rpm, Arch): make install DESTDIR=... PREFIX=/usr

PREFIX ?= /usr/local
DESTDIR ?=
PYTHON ?= /usr/bin/python3
LIBDIR = $(PREFIX)/share/drivux
APP_ID = io.github.xfr3pbr.Drivux

.PHONY: install uninstall test

install:
	install -d $(DESTDIR)$(LIBDIR)/drivux
	install -m 644 src/drivux/*.py $(DESTDIR)$(LIBDIR)/drivux/
	install -d $(DESTDIR)$(PREFIX)/bin
	sed -e 's|@LIBDIR@|$(LIBDIR)|' -e 's|@PYTHON@|$(PYTHON)|' data/drivux.in > $(DESTDIR)$(PREFIX)/bin/drivux
	chmod 755 $(DESTDIR)$(PREFIX)/bin/drivux
	install -Dm 644 data/$(APP_ID).desktop $(DESTDIR)$(PREFIX)/share/applications/$(APP_ID).desktop
	install -Dm 644 data/nautilus/drivux_offline.py $(DESTDIR)$(PREFIX)/share/nautilus-python/extensions/drivux_offline.py
	install -Dm 644 LICENSE $(DESTDIR)$(PREFIX)/share/licenses/drivux/LICENSE

uninstall:
	rm -rf $(DESTDIR)$(LIBDIR) $(DESTDIR)$(PREFIX)/share/licenses/drivux
	rm -f $(DESTDIR)$(PREFIX)/bin/drivux $(DESTDIR)$(PREFIX)/share/applications/$(APP_ID).desktop
	rm -f $(DESTDIR)$(PREFIX)/share/nautilus-python/extensions/drivux_offline.py

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests
