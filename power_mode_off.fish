#!/usr/bin/env fish


# ============================================================
# Entorno gráfico (necesario al ejecutar por SSH)
# ============================================================
function import_graphical_env
    # Variables de la sesión gráfica, que systemd --user conoce
    for line in (systemctl --user show-environment 2>/dev/null)
        set -l kv (string split -m1 '=' -- $line)
        if contains -- $kv[1] DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_SESSION_TYPE XDG_RUNTIME_DIR
            set -gx $kv[1] $kv[2]
        end
    end

    if not set -q XDG_RUNTIME_DIR
        set -gx XDG_RUNTIME_DIR /run/user/(id -u)
    end

    # Fallback si no se importó WAYLAND_DISPLAY
    if not set -q WAYLAND_DISPLAY; and test -S "$XDG_RUNTIME_DIR/wayland-0"
        set -gx WAYLAND_DISPLAY wayland-0
    end
end


# Apaga/enciende la señal del monitor. Nunca es fatal.
function screen_power -a state
    if set -q WAYLAND_DISPLAY; and command -q kscreen-doctor
        kscreen-doctor --dpms $state >/dev/null 2>&1; and return 0
    end
    if set -q DISPLAY
        xset dpms force $state >/dev/null 2>&1; and return 0
    end
    return 1
end


import_graphical_env


# ============================================================
# REACTIVAR SEÑAL HDMI
# ============================================================
# Devuelve la energía al puerto HDMI para poder ver la pantalla
screen_power on


# ============================================================
# Restaurar Plasma Shell (Konsole seguirá abierto)
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


# Segunda pasada: Plasma tarda en levantar y a veces la pantalla
# necesita el DPMS después de que el shell esté arriba.
sleep 1
screen_power on

exit 0