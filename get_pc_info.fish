echo "=== Sesión / compositor ==="
echo $XDG_SESSION_TYPE
plasmashell --version
kwin_wayland --version 2>/dev/null
kwin_x11 --version 2>/dev/null

echo "=== GPU(s) presentes (¿hay iGPU además de la 1650?) ==="
lspci | grep -Ei 'vga|3d|display'

echo "=== Qué proceso(s) está(n) usando VRAM en este momento ==="
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
nvidia-smi

echo "=== Procesos por RAM ==="
ps aux --sort=-%mem | head -25

echo "=== Servicios systemd de usuario corriendo ==="
systemctl --user list-units --type=service --state=running --no-pager

echo "=== Servicios systemd de sistema corriendo ==="
systemctl list-units --type=service --state=running --no-pager

echo "=== CPU governor ==="
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors

echo "=== Gestor de energía instalado ==="
which powerprofilesctl tlp auto-cpufreq 2>/dev/null
powerprofilesctl list 2>/dev/null
