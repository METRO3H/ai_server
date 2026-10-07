#!/bin/bash
#
# Wrapper que lanza run.fish dentro de tmux.
# Si ya estás en tmux, lo ejecuta directamente.
# Si no, crea una sesión de tmux.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="ai-server"

# ¿Estamos ya en tmux?
if [ -z "$TMUX" ]; then
    # No estamos en tmux. Crear/adjuntar sesión.
    
    # ¿La sesión ya existe?
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        # Sesión existe: adjuntarse
        exec tmux attach-session -t "$SESSION_NAME"
    else
        # Crear nueva sesión y lanzar run.fish
        exec tmux new-session -s "$SESSION_NAME" -c "$SCRIPT_DIR" "fish run.fish"
    fi
else
    # Ya estamos en tmux: ejecutar run.fish directamente
    cd "$SCRIPT_DIR"
    exec fish run.fish "$@"
fi