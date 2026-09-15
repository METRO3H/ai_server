#!/usr/bin/env fish
#
# Arranque del server. Por defecto, activa el modo ahorro de energía
# (power_mode_on.fish) ANTES de levantar el server, y lo restaura
# (power_mode_off.fish) cuando el server termina — sea por Ctrl+C,
# error, o cierre normal.
#
# power_mode_on.fish / power_mode_off.fish son scripts independientes
# (no se mezcla su lógica acá) — este archivo solo los invoca.
#
# Para arrancar SIN pasar por el modo ahorro (útil en desarrollo, para
# no matar VS Code/Dolphin/etc. en cada reinicio del server):
#   ./run.fish --no-power-mode
#   ./run.fish --no-power-mode --debug   (combinable con otras flags)
#
# Limitación conocida: si se mata este proceso (run.fish) con
# "kill -9" en vez de Ctrl+C, no hay forma de que ningún script corra
# la restauración — para ese caso, correr power_mode_off.fish a mano.

argparse --ignore-unknown 'no-power-mode' -- $argv
or exit 1

set -l script_dir (dirname (status --current-filename))
set -l power_mode_enabled 1
if set -q _flag_no_power_mode
    set power_mode_enabled 0
end

if test $power_mode_enabled -eq 1
    $script_dir/power_mode_on.fish
end

set -l python ".venv/bin/python"

set -l cuda_libs (
    $python -c "
import os
import nvidia.cublas
import nvidia.cudnn

print(
    os.path.join(nvidia.cublas.__path__[0], 'lib')
    + ':'
    + os.path.join(nvidia.cudnn.__path__[0], 'lib')
)
"
)

if test $status -ne 0
    echo "Error: no se pudieron localizar las librerías CUDA."
    if test $power_mode_enabled -eq 1
        $script_dir/power_mode_off.fish
    end
    exit 1
end

set -gx LD_LIBRARY_PATH "$cuda_libs" $LD_LIBRARY_PATH

# Sin "exec" a propósito: necesitamos que fish siga vivo después de que
# el server termine, para poder restaurar el modo normal a continuación.
$python -m mediator.main $argv

if test $power_mode_enabled -eq 1
    echo ""
    echo "Server detenido — restaurando modo normal..."
    $script_dir/power_mode_off.fish
end