# HFT Engine Rebotes — Explorador de liquidez de mercado

Proyecto personal de **análisis experimental de microestructura** desarrollado en Python. Utiliza información pública de Binance para observar órdenes, trades y liquidaciones, con una interfaz interactiva en consola.

**Este proyecto no implementa un sistema de high-frequency trading institucional ni promete latencia cero, detección infalible de liquidez o precios exactos de reversión.** El comportamiento depende de datos y conectividad de terceros.

## Componentes presentes

- Captura asíncrona de datos mediante `aiohttp` y `websockets`.
- Análisis local de concentraciones en el libro de órdenes y actividad del mercado.
- Seguimiento de eventos de liquidaciones disponibles en Binance Futures.
- Panel de terminal basado en Rich y almacenamiento local de señales para revisión.
- Configuración mediante archivo JSON local y parámetros para alertas.

## Instalación

Python 3.10 o superior:

```bash
git clone https://github.com/augustogjmm/HFT-ENGINE-REBOTES.git
cd HFT-ENGINE-REBOTES
python -m venv .venv
# Activar .venv; en Windows: .venv\Scripts\activate
pip install -r requirements.txt
python rebotes.py
```

Si el programa crea `config.json`, mantenerlo fuera del control de versiones. No publicar tokens, credenciales, sesiones ni archivos de ejecución.

## Restricciones y validación

El registro local de señales permite estudio y revisiones posteriores; no demuestra que una señal sea predictiva ni rentable. La latencia real no es nula, los eventos pueden llegar con retraso y las órdenes visibles pueden cancelarse sin ejecutar. Los filtros heurísticos de spoofing no identifican con certeza una manipulación.

No envía órdenes financieras reales en el flujo documentado. No constituye asesoramiento financiero.

**Autor:** [Augusto García Juárez Matorras](https://www.linkedin.com/in/augustog/) · [Portfolio ejecutivo](https://github.com/augustogjmm/Radar/blob/main/PORTFOLIO.md)
