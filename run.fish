#!/usr/bin/env fish

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
    exit 1
end

set -gx LD_LIBRARY_PATH "$cuda_libs" $LD_LIBRARY_PATH

exec $python -m mediator.main $argv