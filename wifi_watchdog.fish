#!/usr/bin/env fish
#
# Vigila la conexión WiFi del server:
#   - Espera a que WiFi esté conectado y con IP antes de iniciar.
#   - Una vez estable, solo vigila que no se caiga (sin parsing complicado).

set -l wait_timeout 30      # máximo tiempo para esperar a que WiFi esté listo
set -l check_interval 15    # segundos entre chequeos una vez conectado
set -l reconnect_threshold 5 # chequeos fallidos antes de reconectar


function log_wifi
    echo (date "+[%H:%M:%S,%3N][wifi]") $argv
end


if not command -q nmcli
    log_wifi "nmcli no disponible — vigilancia desactivada."
    exit 0
end

# Detectar el adaptador WiFi
set -l devs (nmcli -t -f DEVICE,TYPE device status 2>/dev/null | string replace -rf '^([^:]+):wifi$' '$1')
if test (count $devs) -eq 0
    log_wifi "Sin adaptador WiFi — vigilancia desactivada."
    exit 0
end
set -l dev $devs[1]

log_wifi "Esperando a que WiFi esté listo en $dev..."

# ============================================================
# FASE 1: Esperar a que WiFi esté conectado
# ============================================================
set -l elapsed 0
while test $elapsed -lt $wait_timeout
    set -l state (nmcli -g GENERAL.STATE device show $dev 2>/dev/null)
    set -l ip (nmcli -g IP4.ADDRESS device show $dev 2>/dev/null | head -n 1)
    
    # Simplificar: solo chequear que NetworkManager dice "conectado" y hay IP
    if string match -q '100*' -- "$state"; and test -n "$ip"
        log_wifi "✓ WiFi listo (IP: $ip)"
        break
    end
    
    set elapsed (math $elapsed + 1)
    sleep 1
end

if test $elapsed -ge $wait_timeout
    log_wifi "⚠ Timeout esperando WiFi ($wait_timeout s). Continuando..."
end


# ============================================================
# FASE 2: Vigilar conexión (simple y robusto)
# ============================================================

set -l last_state connected
set -l fail_count 0
set -l reconnect_count 0

while true
    set -l state connected
    set -l fails 0
    
    # Chequeo 1: ¿NetworkManager dice que estamos conectados?
    set -l nm_state (nmcli -g GENERAL.STATE device show $dev 2>/dev/null)
    if not string match -q '100*' -- "$nm_state"
        set fails (math $fails + 1)
    end
    
    # Chequeo 2: ¿Tenemos IP?
    set -l has_ip (nmcli -g IP4.ADDRESS device show $dev 2>/dev/null | head -n 1)
    if test -z "$has_ip"
        set fails (math $fails + 1)
    end
    
    # Chequeo 3: ¿Tenemos ruta por defecto?
    set -l gw (ip -4 route show default dev $dev 2>/dev/null | head -n 1)
    if test -z "$gw"
        set fails (math $fails + 1)
    end
    
    # Si falla más de un chequeo, es caída real
    if test $fails -gt 1
        set state disconnected
        set fail_count (math $fail_count + 1)
    else
        # Pequeña prueba: ping rápido (sin bloquear)
        if test -n "$gw"
            if timeout 2 ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1
                set fail_count 0
            else
                set fail_count (math $fail_count + 1)
            end
        else
            set fail_count 0
        end
    end
    
    # Reportar cambios de estado
    if test "$state" != "$last_state"
        if test "$state" = connected
            log_wifi "✓ Reconectado"
            set reconnect_count 0
        else
            log_wifi "✗ Desconectado (fallos: $fail_count)"
        end
        set last_state $state
    end
    
    # Reconectar si hay demasiados fallos seguidos
    if test $fail_count -ge $reconnect_threshold
        set reconnect_count (math $reconnect_count + 1)
        
        if test (math "$reconnect_count % 3") -eq 0
            log_wifi "🔄 Intento $reconnect_count: reiniciando radio..."
            nmcli radio wifi off >/dev/null 2>&1
            sleep 3
            nmcli radio wifi on >/dev/null 2>&1
            sleep 3
        else
            log_wifi "🔄 Intento $reconnect_count: reconectando..."
            nmcli device disconnect $dev >/dev/null 2>&1
            sleep 1
            timeout 20 nmcli device connect $dev >/dev/null 2>&1
        end
        
        set fail_count 0
    end
    
    sleep $check_interval
end