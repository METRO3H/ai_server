#!/usr/bin/env fish
# v2 — la primera versión asumía que la lista de apps abiertas en el
# momento del diagnóstico (VS Code, KWrite, LocalSend) era exhaustiva.
# No lo es: cualquier app puede estar abierta cuando se corre esto
# (Dolphin, un navegador, lo que sea). Esta versión mata TODO lo que
# esté envuelto como "app-*.service" —así es como Plasma 6 envuelve
# CUALQUIER app lanzada desde el launcher/taskbar/Dolphin/etc.— salvo
# konsole, sin importar qué app sea ni si estaba corriendo cuando se
# escribió este script.
#
# Además, en vez de "systemctl stop" (negocia un cierre prolijo y
# puede tardar — por eso VS Code se demoraba en cerrar), se usa
# "systemctl kill --signal=SIGKILL": mata los procesos del momento,
# sin esperar nada.
#
# NO TOCA (rompería la sesión o konsole):
#   kwin_wayland, dbus-broker, dconf, systemd-logind, NetworkManager,
#   power-profiles-daemon.
#
# NO toca CPU governor ni power-profile (a pedido explícito: no bajar
# performance bajo ninguna circunstancia).
#
# Uso: ./power_mode_on.fish

# ── snapshot ANTES, para poder mostrar cuánto se liberó al final ────
set -l ram_before (free -m | awk '/^Mem:/ {print $3}')
set -l vram_before (nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)

echo "== Matando TODAS las apps de usuario abiertas (excepto konsole) =="
for unit in (systemctl --user list-units --type=service --state=running --no-legend --plain | string match -r '^app-\S*\.service')
    if string match -qr '^app-org\.kde\.konsole@' -- $unit
        echo "  (dejando viva: $unit)"
        continue
    end
    echo "  SIGKILL a $unit"
    systemctl --user kill --signal=SIGKILL $unit
end

echo "== Red de seguridad: por si alguna app no quedó envuelta en un app-*.service =="
echo "   (ej. abierta directo desde una terminal, sin pasar por el launcher de Plasma)"
pkill -9 -x dolphin 2>/dev/null
pkill -9 -x firefox 2>/dev/null
pkill -9 -x chromium 2>/dev/null
pkill -9 -f '/usr/share/code/code' 2>/dev/null
pkill -9 -x kwrite 2>/dev/null
pkill -9 -x localsend 2>/dev/null

echo "== Parando servicios de fondo de Plasma/KDE que no son 'app-*' =="
echo "   (mask, no solo kill: baloorunner/krunner/portal-gtk son"
echo "   dbus-activatable — si solo se matan, cualquier llamada a su"
echo "   interfaz de D-Bus los vuelve a levantar solos, sin pasar por"
echo "   el mecanismo de Restart= de systemd. mask bloquea eso.)"
set -l kde_background \
    kde-baloo.service \
    plasma-baloorunner.service \
    plasma-krunner.service \
    xdg-desktop-portal-gtk.service

for svc in $kde_background
    echo "  mask + SIGKILL a $svc"
    systemctl --user mask $svc
    systemctl --user kill --signal=SIGKILL $svc
end

echo "== Parando plasmashell (panel/escritorio) =="
echo "   konsole no depende de esto — es un cliente de kwin_wayland aparte"
echo "   (stop, no kill: plasmashell tiene Restart=on-failure — kill lo"
echo "   mata sin avisarle a systemd que fue intencional, y systemd lo"
echo "   resucita solo. stop sí cuenta como parada intencional.)"
systemctl --user stop plasma-plasmashell.service

echo ""
echo "Listo. Esto quedó corriendo en la GPU:"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv

# ── snapshot DESPUÉS + diferencia ────────────────────────────────────
# sleep corto: darle un instante al kernel/driver para que termine de
# reclamar la memoria de los procesos recién matados antes de medir.
sleep 1
set -l ram_after (free -m | awk '/^Mem:/ {print $3}')
set -l vram_after (nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)
set -l ram_freed (math $ram_before - $ram_after)
set -l vram_freed (math $vram_before - $vram_after)

echo ""
echo "== Memoria liberada =="
echo "  RAM:  $ram_before MiB -> $ram_after MiB   (liberados: $ram_freed MiB)"
echo "  VRAM: $vram_before MiB -> $vram_after MiB   (liberados: $vram_freed MiB)"