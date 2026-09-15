#!/usr/bin/env fish
# Revierte power_mode_on.fish: reinicia plasmashell y los servicios de
# fondo/autostart de KDE.
#
# Las apps de usuario que se mataron (Dolphin, VS Code, KWrite,
# LocalSend, lo que fuera) NO se reabren solas — no son "servicios de
# fondo", son apps que abrís vos cuando las necesitás.
#
# Nota: los 4 servicios "app-*@autostart" de abajo (KDE Connect,
# Discover, Geoclue, Xwayland Video Bridge) normalmente se relanzan
# solo al iniciar sesión. Si "systemctl --user start" no los revive
# acá (puede pasar si systemd ya descartó la unidad transitoria tras
# el SIGKILL), la forma segura de recuperarlos es cerrar sesión y
# volver a entrar — no es necesario reiniciar toda la PC.
#
# Uso: ./power_mode_off.fish

echo "== Reiniciando plasmashell =="
systemctl --user start plasma-plasmashell.service

echo "== Reiniciando servicios de fondo de Plasma/KDE =="
echo "   (unmask primero — power_mode_on.fish los enmascaró para que"
echo "   no los revivicara D-Bus solo; sin desenmascarar, start falla)"
set -l kde_background \
    kde-baloo.service \
    plasma-baloorunner.service \
    plasma-krunner.service \
    xdg-desktop-portal-gtk.service

for svc in $kde_background
    echo "  unmask + iniciando $svc"
    systemctl --user unmask $svc
    systemctl --user start $svc
end

echo "== Reiniciando servicios autostart (KDE Connect, Discover, Geoclue, Xwayland Video Bridge) =="
set -l kde_autostart \
    app-org.kde.kdeconnect.daemon@autostart.service \
    app-org.kde.discover.notifier@autostart.service \
    app-geoclue-demo-agent@autostart.service \
    app-org.kde.xwaylandvideobridge@autostart.service

for svc in $kde_autostart
    echo "  iniciando $svc"
    systemctl --user start $svc
end

echo ""
echo "Listo. Las apps de usuario que hayas tenido abiertas (Dolphin, VS Code, etc.) no se reabren solas."