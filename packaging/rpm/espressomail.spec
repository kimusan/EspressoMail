Name:           espressomail
Version:        %{pkg_version}
Release:        1%{?dist}
Summary:        Modern terminal email client built on Espresso TUI
License:        MIT
URL:            https://github.com/kimusan/EspressoMail
BuildArch:      x86_64
AutoReqProv:    no

%description
EspressoMail is a full-featured terminal email client built on The Elm
Architecture (TEA). It features multi-account IMAP/SMTP, an offline SQLite
store with fuzzy full-text search, Sieve filter management, CardDAV address
book sync, PGP/GPG cryptography, inline image rendering (chafa/sixel/kitty),
and responsive multi-pane terminal navigation.

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}/usr/bin
mkdir -p %{buildroot}/usr/share/applications
mkdir -p %{buildroot}/usr/share/icons/hicolor/scalable/apps
mkdir -p %{buildroot}/usr/share/doc/espressomail
mkdir -p %{buildroot}/usr/share/licenses/espressomail

install -m 755 %{_sourcedir}/espressomail %{buildroot}/usr/bin/espressomail
install -m 644 %{_sourcedir}/espressomail.desktop %{buildroot}/usr/share/applications/espressomail.desktop
install -m 644 %{_sourcedir}/espressomail.svg %{buildroot}/usr/share/icons/hicolor/scalable/apps/espressomail.svg
install -m 644 %{_sourcedir}/README.md %{buildroot}/usr/share/doc/espressomail/README.md
install -m 644 %{_sourcedir}/LICENSE %{buildroot}/usr/share/licenses/espressomail/LICENSE

%files
/usr/bin/espressomail
/usr/share/applications/espressomail.desktop
/usr/share/icons/hicolor/scalable/apps/espressomail.svg
/usr/share/doc/espressomail/README.md
/usr/share/licenses/espressomail/LICENSE

%changelog
* Wed Oct 28 2026 Kim Schulz <kim@schulz.dk> - %{pkg_version}-1
- Release version %{pkg_version}
