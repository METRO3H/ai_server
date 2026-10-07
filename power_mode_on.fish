#!/usr/bin/env fish

set -l verbose 0

if contains -- -v $argv
    set verbose 1
end


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
# Memoria inicial
# ============================================================
set -l ram_before (free -m | awk '/^Mem:/ {print $3}')
set -l ram_total (free -m | awk '/^Mem:/ {print $2}')

set -l vram_before (nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null)
set -l vram_total (nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null)

# Si nvidia-smi falla o no hay gráfica Nvidia activa, ponemos 0 para evitar errores en math
if test -z "$vram_before"; set vram_before 0; end
if test -z "$vram_total"; set vram_total 0; end


# ============================================================
# Detener servicios y aplicaciones
# ============================================================
if test $verbose -eq 1
    echo "Deteniendo servicios y aplicaciones..."
end

# Detener aplicaciones app-*.service, excepto Konsole y Kwrite
for svc in (systemctl --user list-units --type=service --state=running --no-legend 'app-*.service' 2>/dev/null | awk '{print $1}')
    if string match -q 'app-org.kde.konsole@*.service' $svc
        continue
    end
    if string match -q 'app-org.kde.kwrite@*.service' $svc
        continue
    end
    if test $verbose -eq 1
        echo "  Deteniendo $svc"
    end
    systemctl --user stop $svc >/dev/null 2>&1
end

# Procesos pesados conocidos (cierre seguro, sin -9)
if test $verbose -eq 1
    echo "Finalizando procesos pesados..."
end

for proc in dolphin firefox chromium code kwrite localsend
    pkill $proc 2>/dev/null
end


# ============================================================
# Servicios de fondo
# ============================================================
set -l services \
    kde-baloo.service \
    plasma-baloorunner.service \
    plasma-krunner.service \
    xdg-desktop-portal-gtk.service

for svc in $services
    if test $verbose -eq 1
        echo "  Configurando $svc"
    end
    systemctl --user stop $svc >/dev/null 2>&1
    # --runtime: el enmascarado se pierde al reiniciar, así que si algo
    # falla de forma abrupta no queda persistente.
    systemctl --user mask --runtime $svc >/dev/null 2>&1
end


# ============================================================
# Plasma Shell
# ============================================================
if test $verbose -eq 1
    echo "Deteniendo Plasma Shell..."
end
systemctl --user stop plasma-plasmashell.service >/dev/null 2>&1


# ============================================================
# Esperar a que la memoria se libere
# ============================================================
sleep 2


# ============================================================
# Memoria final
# ============================================================
set -l ram_after (free -m | awk '/^Mem:/ {print $3}')

set -l vram_after (nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null)
if test -z "$vram_after"; set vram_after 0; end


# ============================================================
# Cálculos
# ============================================================
set -l ram_freed (math "$ram_before - $ram_after")
set -l vram_freed (math "$vram_before - $vram_after")

# Evitar división por cero si la VRAM inicial ya era cero
set -l ram_pct (math -s1 "100 * $ram_freed / $ram_before")
set -l vram_pct 0
if test $vram_before -gt 0
    set vram_pct (math -s1 "100 * $vram_freed / $vram_before")
end

set -l ram_available_pct (math -s1 "100 * $ram_freed / $ram_total")
set -l vram_available_pct 0
if test $vram_total -gt 0
    set vram_available_pct (math -s1 "100 * $vram_freed / $vram_total")
end


# ============================================================
# Mostrar memoria liberada
# ============================================================
echo ""
set_color brblue
echo "== Memoria liberada =="

set_color normal
echo -n "RAM:  $ram_before MiB -> $ram_after MiB  "
set_color red
echo -n "-$ram_freed MiB, -$ram_pct%"
set_color normal
echo -n "  "
set_color green
echo "+$ram_freed MiB disponibles, +$ram_available_pct%"

set_color normal
echo -n "VRAM: $vram_before MiB -> $vram_after MiB  "
set_color red
echo -n "-$vram_freed MiB, -$vram_pct%"
set_color normal
echo -n "  "
set_color green
echo "+$vram_freed MiB disponibles, +$vram_available_pct%"
set_color normal
echo ""


# ============================================================
# APAGAR SEÑAL HDMI (no fatal)
# ============================================================
if not screen_power off
    if test $verbose -eq 1
        echo "No se pudo apagar la pantalla (se continúa igualmente)."
    end
end

# Éxito explícito: apagar la pantalla es un extra y no debe hacer fallar el script
exit 0