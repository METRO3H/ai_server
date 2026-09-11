#!/usr/bin/env fish
# Activa el venv y corre el mediador en un solo paso, para no tener que
# acordarse de los dos comandos por separado.
#
#   ./run.fish --debug
#
source (dirname (status --current-filename))/.venv/bin/activate.fish
python -m mediator.main $argv
