#!/usr/bin/env fish

argparse --ignore-unknown 'no-power-mode' -- $argv
or exit 1

set -g script_dir (dirname (status --current-filename))
set -g power_mode_enabled 1
set -g server_pid
set -g cleanup_done 0
set -g terminal_echoctl 1

if set -q _flag_no_power_mode
    set -g power_mode_enabled 0
end


function log_power
    echo (date "+[%H:%M:%S,%3N][power]") $argv
end


function log_server
    echo (date "+[%H:%M:%S,%3N][server]") $argv
end


function disable_ctrl_c_echo
    stty -echoctl 2>/dev/null
    set -g terminal_echoctl 0
end


function restore_ctrl_c_echo
    if test $terminal_echoctl -eq 0
        stty echoctl 2>/dev/null
        set -g terminal_echoctl 1
    end
end


function restore_power_mode
    if test $cleanup_done -eq 1
        restore_ctrl_c_echo
        return
    end

    set -g cleanup_done 1

    if test $power_mode_enabled -eq 1
        log_power "Server detenido — restaurando modo normal..."

        $script_dir/power_mode_off.fish

        if test $status -ne 0
            log_power "Advertencia: no se pudo restaurar completamente el modo normal."
        else
            log_power "Modo normal restaurado."
        end
    end

    restore_ctrl_c_echo
end


function handle_sigint --on-signal INT
    log_server "Interrupción recibida — deteniendo server..."

    if test -n "$server_pid"
        kill -INT $server_pid 2>/dev/null

        for i in (seq 1 15)
            if not kill -0 $server_pid 2>/dev/null
                break
            end

            sleep 0.2
        end

        if kill -0 $server_pid 2>/dev/null
            log_server "El servidor no respondió a tiempo — forzando cierre."
            kill -9 $server_pid 2>/dev/null
        end
    end
end


clear


if test $power_mode_enabled -eq 1
    log_power "Activando modo ahorro de energía..."
    echo ""

    $script_dir/power_mode_on.fish

    if test $status -ne 0
        log_power "Error: no se pudo activar el modo ahorro de energía."
        exit 1
    end
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
    log_server "Error: no se pudieron localizar las librerías CUDA."
    restore_power_mode
    exit 1
end

set -gx LD_LIBRARY_PATH "$cuda_libs" $LD_LIBRARY_PATH


disable_ctrl_c_echo

log_server "Iniciando servidor..."

$python -m mediator.main $argv &
set -g server_pid $last_pid

wait $server_pid
set -l server_status $status

restore_power_mode

exit $server_status