🔍 Lupa de Liquidez HFT (High-Frequency Trading)

Un motor analítico de micro-estructura de mercado para la terminal, diseñado para operar Scalping de Reversión (Rebotes) en criptomonedas. El sistema se conecta directamente a la infraestructura de Binance mediante WebSockets para construir un libro de órdenes hiperprofundo en memoria local (RAM) con cero latencia.

En lugar de depender de indicadores rezagados (RSI, MACD), este motor rastrea en tiempo real el posicionamiento de liquidez institucional (Muros), el agotamiento de la agresividad a mercado (La Cinta) y las liquidaciones forzadas para encontrar puntos de rebote exactos al centavo.

✨ Características Principales

Local Order Book (En Memoria): Descarga el límite máximo de la API REST (5000 niveles) y lo mantiene vivo milisegundo a milisegundo mediante los streams @depth de Spot y Futuros simultáneamente.

Algoritmo de Zoom Recursivo: Encuentra el "centro de masa" de la liquidez y luego hace un zoom descendente para darte el precio exacto del muro institucional, evitando promedios falsos.

Filtro Anti-Spoofing: Ignora dinámicamente las órdenes ruidosas de los bots HFT (Alta Frecuencia) pegadas al precio actual para revelar la verdadera liquidez profunda.

Motor de Confluencia (Gatillo): Analiza el cruce de:

Impacto inminente contra un muro (Order Book).

Absorción agresiva a mercado / Agotamiento (Cinta / AggTrades).

Liquidaciones en cadena en contra del movimiento (ForceOrder).

Paper Trading Automático: Cuando detecta una confluencia perfecta, registra automáticamente la señal (Long/Short) en un archivo trading_signals_log.txt para backtesting.

Decimales Dinámicos: Escala matemáticamente la precisión visual dependiendo de si operas BTC ($65,000) o Memecoins ($0.0001).

⚙️ Requisitos e Instalación

Requiere Python 3.8 o superior.

Clona o descarga el archivo liquidity_magnifier.py en tu entorno local o VPS.

Instala las dependencias asíncronas y de interfaz gráfica:

pip install aiohttp websockets rich


Ejecuta el motor:

python liquidity_magnifier.py


🛠️ Configuración (config.json)

Al ejecutar el script por primera vez, se generará automáticamente un archivo config.json en el mismo directorio. Puedes ajustarlo para afinar el comportamiento del motor:

{
    "symbol": "BTCUSDT",
    "anti_spoofing_ticks": 50,
    "ui_refresh_rate": 1.0,
    "impact_zone_pct": 0.5,
    "sound_alerts": false
}


anti_spoofing_ticks: Cantidad de "centavos" pegados al precio actual que el algoritmo ignorará (Filtro de ruido).

ui_refresh_rate: Cada cuántos segundos se repinta la terminal (por defecto 1 segundo). Nota: El procesamiento interno de datos siempre funciona en tiempo real en segundo plano.

impact_zone_pct: La distancia en porcentaje (ej: 0.5 = 0.5%) a la que el precio debe acercarse a un muro para activar la alerta roja/verde de 🎯 IMPACTO.

sound_alerts: Cambia a true si quieres un pitido de la consola (con cooldown de 10s) cuando el precio entra en zona de impacto.

📊 Cómo leer la Terminal

La pantalla está dividida en un Dashboard analítico:

1. El Header (Inteligencia de Mercado)

Uptime & Confianza: Indica cuánto tiempo lleva el bot absorbiendo datos. Una confianza "🟢 ALTA" significa que la memoria local ha acumulado suficiente historia de liquidez.

Agresividad (Cinta): Muestra el Delta de los trades ejecutados a mercado en los últimos 60 segundos. Si es muy rojo, los vendedores agresivos tienen el control a corto plazo.

CVD Sesión: Volumen Delta Acumulado desde que encendiste el programa.

Liquidaciones: Muestra fuego (🔥) o explosiones (💥) si se han liquidado Longs o Shorts masivamente en los últimos 60 segundos.

2. Cuadrícula de Liquidez (Los 4 Paneles)

Mercado Spot / Futuros: Los muros exactos detectados en sus respectivos libros.

Unificado (Muros Exactos): La suma matemática de ambos libros para ver el peso institucional real combinado.

Unificado (Promedios Suavizados): Agrupa los muros unificados de a pares para encontrar "Súper Clústeres" o grandes bloques de contención.

El Imán de Órdenes (En el título de cada tabla): Mide el desbalance pasivo entre compras (Bids) y ventas (Asks) para indicarte la dirección probabilística del mercado.

3. Log de Señales

Revisa el archivo trading_signals_log.txt para ver un registro histórico de las señales de confluencia detectadas mientras no estabas frente a la pantalla.

Disclaimer: Este software es una herramienta de visualización y análisis de datos de mercado. No realiza operaciones financieras reales ni constituye consejo de inversión.
