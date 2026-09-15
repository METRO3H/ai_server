#!/usr/bin/env fish


# ============================================================
# Restaurar Plasma Shell
# ============================================================

systemctl --user start plasma-plasmashell.service >/dev/null 2>&1


# ============================================================
# Desenmascarar servicios
# ============================================================

set -l services \
    kde-baloo.service \
    plasma-baloorunner.service \
    plasma-krunner.service \
    xdg-desktop-portal-gtk.service

for svc in $services
    systemctl --user unmask $svc >/dev/null 2>&1
    systemctl --user start $svc >/dev/null 2>&1
end


# ============================================================
# Restaurar servicios de autostart
# ============================================================

set -l autostart_services \
    app-org.kde.kdeconnect.daemon@autostart.service \
    app-org.kde.discover.notifier@autostart.service \
    app-geoclue-demo-agent@autostart.service \
    app-org.kde.xwaylandvideobridge@autostart.service

for svc in $autostart_services
    systemctl --user start $svc >/dev/null 2>&1
end


exit 0

