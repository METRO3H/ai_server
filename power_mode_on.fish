#!/usr/bin/env fish

set -l verbose 0

if contains -- -v $argv
    set verbose 1
end


# ============================================================
# Memoria inicial
# ============================================================

set -l ram_before (free -m | awk '/^Mem:/ {print $3}')
set -l ram_total (free -m | awk '/^Mem:/ {print $2}')

set -l vram_before (
    nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null
)

set -l vram_total (
    nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null
)


# ============================================================
# Detener servicios y aplicaciones
# ============================================================

if test $verbose -eq 1
    echo "Deteniendo servicios y aplicaciones..."
end


# Detener aplicaciones app-*.service, excepto Konsole
for svc in (systemctl --user list-units --type=service --state=running --no-legend 'app-*.service' 2>/dev/null | awk '{print $1}')
    if string match -q 'app-org.kde.konsole@*.service' $svc
        continue
    end

    if test $verbose -eq 1
        echo "  Deteniendo $svc"
    end

    systemctl --user stop $svc >/dev/null 2>&1
end


# Procesos pesados conocidos
if test $verbose -eq 1
    echo "Finalizando procesos pesados..."
end

for proc in dolphin firefox chromium code kwrite localsend
    pkill -9 $proc 2>/dev/null
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
    systemctl --user mask $svc >/dev/null 2>&1
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

sleep 1


# ============================================================
# Memoria final
# ============================================================

set -l ram_after (free -m | awk '/^Mem:/ {print $3}')

set -l vram_after (
    nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null
)

# ============================================================
# Cálculos
# ============================================================

set -l ram_freed (math "$ram_before - $ram_after")
set -l vram_freed (math "$vram_before - $vram_after")

set -l ram_pct (math -s1 "100 * $ram_freed / $ram_before")
set -l vram_pct (math -s1 "100 * $vram_freed / $vram_before")

set -l ram_available_pct (math -s1 "100 * $ram_freed / $ram_total")
set -l vram_available_pct (math -s1 "100 * $vram_freed / $vram_total")


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
echo ""
