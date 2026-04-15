import os
import json
import math
import time
import asyncio
import aiohttp
import websockets
from datetime import datetime
from decimal import Decimal
from collections import defaultdict
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.align import Align
from rich import box
from rich.progress import Progress, BarColumn, TextColumn
from rich.prompt import Prompt

console = Console()
CONFIG_FILE = 'config.json'
LOG_FILE = 'trading_signals_log.txt'

# ==========================================
# CONFIGURACIÓN
# ==========================================
def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "symbol": "BTCUSDT",
            "anti_spoofing_ticks": 50,
            "ui_refresh_rate": 1.0,
            "impact_zone_pct": 0.5,  # % de distancia para activar alerta
            "sound_alerts": False    # Apagado por defecto
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)
        console.print(f"[yellow]Archivo {CONFIG_FILE} creado. Modifícalo según tus necesidades.[/yellow]")
        return default_config
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def log_signal(symbol, price, signal_type, reason):
    """Guarda las señales en un archivo local para hacer backtesting (Paper Trading)."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] | PAR: {symbol} | PRECIO: {price} | SEÑAL: {signal_type} | RAZÓN: {reason}\n"
    
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r') as f:
                lines = f.readlines()
                if lines:
                    last_line = lines[-1]
                    if symbol in last_line and signal_type in last_line:
                        last_time_str = last_line.split("]")[0].strip("[")
                        last_time = datetime.strptime(last_time_str, "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - last_time).total_seconds() < 30:
                            return # Ya se registró esta señal recién, evitamos spam
    except Exception:
        pass

    with open(LOG_FILE, 'a') as f:
        f.write(log_entry)

# ==========================================
# UTILIDADES DE FORMATO (DECIMALES DINÁMICOS)
# ==========================================
def get_price_decimals(price):
    """Devuelve la cantidad óptima de decimales para mostrar en pantalla según el valor de la moneda."""
    if price == 0: return 2
    if price >= 1000: return 2   # BTC, ETH -> 2 decimales
    if price >= 0.1: return 4    # SOL, BNB, XRP, ADA -> 4 decimales
    if price >= 0.01: return 5   # Monedas más chicas
    if price >= 0.001: return 6
    if price >= 0.0001: return 7
    return 8                     # Memecoins como SHIB, PEPE

# ==========================================
# GESTOR DE LIQUIDACIONES (REKT STREAM)
# ==========================================
class LiquidationTracker:
    def __init__(self, symbol, window_seconds=60):
        self.symbol = symbol.lower()
        self.liquidations = [] 
        self.window = window_seconds

    async def connect_and_maintain(self):
        ws_url = f"wss://fstream.binance.com/ws/{self.symbol}@forceOrder"
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    async for message in ws:
                        data = json.loads(message)['o']
                        now = time.time()
                        side = data['S'] 
                        qty = float(data['q'])
                        self.liquidations.append((now, side, qty))
                        
                        cutoff = now - self.window
                        self.liquidations = [l for l in self.liquidations if l[0] > cutoff]
            except Exception as e:
                await asyncio.sleep(2)

    def get_recent_summary(self):
        now = time.time()
        self.liquidations = [l for l in self.liquidations if now - l[0] < self.window]
        longs_rekt = sum(l[2] for l in self.liquidations if l[1] == 'SELL')
        shorts_rekt = sum(l[2] for l in self.liquidations if l[1] == 'BUY')
        return longs_rekt, shorts_rekt

# ==========================================
# GESTOR DE FLUJO A MERCADO (THE TAPE & CVD)
# ==========================================
class MarketTrades:
    def __init__(self, symbol, window_seconds=60):
        self.symbol = symbol.lower()
        self.trades = [] 
        self.window = window_seconds
        self.session_cvd = 0.0 

    async def connect_and_maintain(self):
        ws_url = f"wss://stream.binance.com:9443/ws/{self.symbol}@aggTrade"
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    async for message in ws:
                        data = json.loads(message)
                        self.add_trade(data)
            except Exception as e:
                await asyncio.sleep(2)

    def add_trade(self, data):
        now = time.time()
        is_aggressive_sell = data['m'] 
        qty = float(data['q'])
        
        if is_aggressive_sell:
            self.session_cvd -= qty
        else:
            self.session_cvd += qty
            
        self.trades.append((now, not is_aggressive_sell, qty))
        cutoff = now - self.window
        self.trades = [t for t in self.trades if t[0] > cutoff]

    def get_delta(self):
        buy_v = sum(t[2] for t in self.trades if t[1])
        sell_v = sum(t[2] for t in self.trades if not t[1])
        return buy_v, sell_v, buy_v - sell_v, self.session_cvd

# ==========================================
# GESTOR DEL LIBRO LOCAL (ASÍNCRONO)
# ==========================================
class LocalOrderBook:
    def __init__(self, symbol, is_futures=False):
        self.symbol = symbol.upper()
        self.is_futures = is_futures
        self.bids = {}  
        self.asks = {}
        self.last_update_id = 0
        self.is_synced = False
        self.buffer = []
        self.processed_events_count = 0  

    async def fetch_snapshot(self):
        limit = 1000 if self.is_futures else 5000
        url = f"https://fapi.binance.com/fapi/v1/depth?symbol={self.symbol}&limit={limit}" if self.is_futures else f"https://api.binance.com/api/v3/depth?symbol={self.symbol}&limit={limit}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.json()
                self.last_update_id = data.get('lastUpdateId', 0)
                
                for p, v in data.get('bids', []):
                    self.bids[Decimal(p)] = Decimal(v)
                for p, v in data.get('asks', []):
                    self.asks[Decimal(p)] = Decimal(v)

    def _process_event(self, event):
        self.processed_events_count += 1 
        for p_str, v_str in event.get('b', []):
            p, v = Decimal(p_str), Decimal(v_str)
            if v == 0: self.bids.pop(p, None)
            else: self.bids[p] = v

        for p_str, v_str in event.get('a', []):
            p, v = Decimal(p_str), Decimal(v_str)
            if v == 0: self.asks.pop(p, None)
            else: self.asks[p] = v

    async def connect_and_maintain(self):
        stream_name = f"{self.symbol.lower()}@depth@100ms"
        ws_url = f"wss://fstream.binance.com/ws/{stream_name}" if self.is_futures else f"wss://stream.binance.com:9443/ws/{stream_name}"

        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    self.is_synced = False
                    self.buffer = []
                    asyncio.create_task(self.fetch_snapshot())

                    async for message in ws:
                        event = json.loads(message)
                        
                        if not self.is_synced:
                            if self.last_update_id == 0:
                                self.buffer.append(event)
                                continue
                            for buf_ev in self.buffer:
                                ev_final_id = buf_ev.get('u', 0)
                                if ev_final_id > self.last_update_id:
                                    self._process_event(buf_ev)
                            self.buffer = []
                            self.is_synced = True
                        
                        if self.is_synced:
                            self._process_event(event)

            except Exception as e:
                self.is_synced = False
                await asyncio.sleep(2)

    def get_structured_book(self, is_ask):
        book_dict = self.asks if is_ask else self.bids
        return [[p, v] for p, v in sorted(book_dict.items(), key=lambda x: x[0], reverse=True)]

# ==========================================
# ALGORITMO RECURSIVO Y PROMEDIOS
# ==========================================
def get_auto_zoom_levels(price):
    if price <= 0: 
        return [Decimal('1000'), Decimal('100'), Decimal('10'), Decimal('1'), Decimal('0.1'), Decimal('0.01')]
    
    order_of_mag = math.floor(math.log10(price))
    macro = Decimal('10') ** (order_of_mag - 1)
    
    levels = []
    current = macro
    for _ in range(6):
        levels.append(current)
        current /= Decimal('10')
        
    return levels

def recursive_zoom_search(dec_orders, zoom_decimals, top_n=6, ignore_ticks=50):
    filtered_orders = dec_orders[ignore_ticks:] if len(dec_orders) > ignore_ticks else dec_orders
    if not filtered_orders: return []

    macro_size = zoom_decimals[0]
    macro_bins = defaultdict(Decimal)
    for p, q in filtered_orders:
        bin_val = (p // macro_size) * macro_size
        macro_bins[bin_val] += q
        
    sorted_macro = sorted(macro_bins.items(), key=lambda x: x[1], reverse=True)[:top_n]
    results = []
    
    for macro_price, _ in sorted_macro:
        current_orders = [[p, q] for p, q in filtered_orders if macro_price <= p < macro_price + macro_size]
        for step in zoom_decimals[1:]:
            if not current_orders: break
            sub_bins = defaultdict(Decimal)
            for p, q in current_orders:
                bin_val = (p // step) * step
                sub_bins[bin_val] += q
            if not sub_bins: break
            best_sub_price = max(sub_bins.items(), key=lambda x: x[1])[0]
            current_orders = [[p, q] for p, q in current_orders if best_sub_price <= p < best_sub_price + step]

        exact_levels = defaultdict(Decimal)
        for p, q in current_orders:
            exact_levels[p] += q
            
        if exact_levels:
            best_exact_price, best_exact_vol = max(exact_levels.items(), key=lambda x: x[1])
            results.append((float(best_exact_price), float(best_exact_vol)))

    results.sort(key=lambda x: x[0], reverse=True)
    return results

def calculate_averaged_points(points):
    averaged = []
    for i in range(0, len(points) - 1, 2):
        p1, v1 = points[i]
        p2, v2 = points[i+1]
        avg_price = (p1 + p2) / 2.0
        avg_vol = (v1 + v2) / 2.0
        averaged.append((avg_price, avg_vol))
    if len(points) % 2 != 0:
        averaged.append(points[-1])
    return averaged

# ==========================================
# INTERFAZ Y RENDERIZADO MEJORADO
# ==========================================
def generate_proportional_bar(vol, max_vol, is_ask, width=15):
    if max_vol <= 0: return "░" * width
    filled = int((vol / max_vol) * width)
    color = "red" if is_ask else "green"
    return f"[{color}]{'█' * filled}[dim]{'░' * (width - filled)}[/dim][/{color}]"

def create_table(title, bids, asks, current_price, is_synced, tape_delta, alert_pct, decs):
    status = "[bold green]● VIVO[/bold green]" if is_synced else "[bold red]● SINCRONIZANDO[/bold red]"
    
    total_bid_vol = sum(vol for _, vol in bids)
    total_ask_vol = sum(vol for _, vol in asks)
    total_vol = total_bid_vol + total_ask_vol
    
    imbalance = 0

    iman_text = "[dim]Calculando Imán...[/dim]"
    if total_vol > 0:
        imbalance = ((total_bid_vol - total_ask_vol) / total_vol) * 100
        tape_indicator = ""
        if tape_delta > 0: tape_indicator = f"[green]▲ Cinta (+{tape_delta:,.2f})[/green]"
        elif tape_delta < 0: tape_indicator = f"[red]▼ Cinta ({tape_delta:,.2f})[/red]"
            
        if imbalance > 10:
            iman_text = f"[bold green]📈 IMÁN ALCISTA: +{imbalance:.1f}% Pasivo[/bold green] | {tape_indicator}"
        elif imbalance < -10:
            iman_text = f"[bold red]📉 IMÁN BAJISTA: {abs(imbalance):.1f}% Pasivo[/bold red] | {tape_indicator}"
        else:
            iman_text = f"[bold yellow]⚖️ IMÁN LATERAL[/bold yellow] | {tape_indicator}"

    table = Table(
        title=f"{title} | {status}\n{iman_text}", 
        box=box.ROUNDED, 
        expand=True, 
        header_style="bold white"
    )
    
    table.add_column("Tipo", justify="center", width=8)
    table.add_column("Precio Exacto", justify="right", style="bold")
    table.add_column("Dist %", justify="right")
    table.add_column("Volumen", justify="right")
    table.add_column("Densidad", justify="left")

    all_vols = [v for p, v in bids] + [v for p, v in asks]
    max_vol = max(all_vols) if all_vols else 1
    
    trigger_alarm_bid = False
    trigger_alarm_ask = False

    for price, vol in asks:
        dist_pct = (abs(price - current_price) / current_price) * 100 if current_price else 0
        bar = generate_proportional_bar(vol, max_vol, is_ask=True)
        
        if dist_pct <= alert_pct:
            table.add_row(f"[bold red blink]🎯 IMPACTO[/bold red blink]", f"[bold red]{price:,.{decs}f}[/bold red]", f"[bold red]+{dist_pct:.2f}%[/bold red]", f"[bold red]{vol:,.2f}[/bold red]", bar)
            trigger_alarm_ask = True
        else:
            table.add_row("[red]Ask[/red]", f"{price:,.{decs}f}", f"+{dist_pct:.2f}%", f"{vol:,.2f}", bar)
    
    table.add_row("---", "---", "---", "---", "---", style="dim")
    table.add_row("[bold yellow]ACTUAL[/bold yellow]", f"[bold yellow]{current_price:,.{decs}f}[/bold yellow]", "0.00%", "---", "")
    table.add_row("---", "---", "---", "---", "---", style="dim")

    for price, vol in bids:
        dist_pct = (abs(current_price - price) / current_price) * 100 if current_price else 0
        bar = generate_proportional_bar(vol, max_vol, is_ask=False)
        
        if dist_pct <= alert_pct:
            table.add_row(f"[bold green blink]🎯 IMPACTO[/bold green blink]", f"[bold green]{price:,.{decs}f}[/bold green]", f"[bold green]-{dist_pct:.2f}%[/bold green]", f"[bold green]{vol:,.2f}[/bold green]", bar)
            trigger_alarm_bid = True
        else:
            table.add_row("[green]Bid[/green]", f"{price:,.{decs}f}", f"-{dist_pct:.2f}%", f"{vol:,.2f}", bar)

    return table, trigger_alarm_bid, trigger_alarm_ask, imbalance

async def ui_loop(spot_book, futures_book, tape_book, liq_book, config):
    anti_spoof = config.get("anti_spoofing_ticks", 50)
    ui_refresh = config.get("ui_refresh_rate", 1.0)
    alert_pct = config.get("impact_zone_pct", 0.5)
    sound_enabled = config.get("sound_alerts", False)
    
    start_time = datetime.now()
    last_beep_time = 0

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=6), 
        Layout(name="main_tables", ratio=1),
        Layout(name="footer", size=3) 
    )
    layout["main_tables"].split_column(
        Layout(name="row_top", ratio=1),
        Layout(name="row_bottom", ratio=1)
    )
    layout["main_tables"]["row_top"].split_row(
        Layout(name="spot", ratio=1),
        Layout(name="futures", ratio=1)
    )
    layout["main_tables"]["row_bottom"].split_row(
        Layout(name="combined", ratio=1),
        Layout(name="averaged", ratio=1)
    )

    console.clear()

    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(bar_width=None, complete_style="green", finished_style="green"),
        TextColumn("[yellow]⏳ {task.fields[tiempo_restante]}s[/yellow]"),
        expand=True
    )
    task_id = progress.add_task("", total=ui_refresh, tiempo_restante=f"{ui_refresh:.1f}")

    with Live(layout, refresh_per_second=15, screen=True) as live:
        last_spot_events = 0
        last_fut_events = 0

        while True:
            uptime_secs = (datetime.now() - start_time).total_seconds()
            
            if not spot_book.is_synced or not futures_book.is_synced:
                layout["header"].update(Panel(Align.center(f"[bold cyan]LUPA DE LIQUIDEZ HFT[/bold cyan] | Par: [bold yellow]{spot_book.symbol}[/bold yellow]"), style="bold blue"))
                layout["main_tables"]["row_top"]["spot"].update(Panel(Align.center("\n\n[yellow]Construyendo Spot en RAM...[/yellow]")))
                layout["main_tables"]["row_top"]["futures"].update(Panel(Align.center("\n\n[yellow]Construyendo Futuros en RAM...[/yellow]")))
                layout["main_tables"]["row_bottom"]["combined"].update(Panel(Align.center("\n\n[dim]Esperando...[/dim]")))
                layout["main_tables"]["row_bottom"]["averaged"].update(Panel(Align.center("\n\n[dim]Esperando...[/dim]")))
                layout["footer"].update(Panel(Align.center("[red]Conectando y descargando historial a memoria...[/red]"), border_style="red"))
                await asyncio.sleep(0.5)
                continue

            t_buy, t_sell, t_delta, session_cvd = tape_book.get_delta()
            longs_rekt, shorts_rekt = liq_book.get_recent_summary()

            s_bids_raw = spot_book.get_structured_book(is_ask=False)
            s_asks_raw = spot_book.get_structured_book(is_ask=True)
            f_bids_raw = futures_book.get_structured_book(is_ask=False)
            f_asks_raw = futures_book.get_structured_book(is_ask=True)

            current_price = float(s_bids_raw[0][0]) if s_bids_raw else 0.0

            # Identificar dinámicamente cuántos decimales necesita este par
            decs = get_price_decimals(current_price)

            comb_bids_dict = defaultdict(Decimal)
            comb_asks_dict = defaultdict(Decimal)
            for p, v in s_bids_raw + f_bids_raw: comb_bids_dict[p] += v
            for p, v in s_asks_raw + f_asks_raw: comb_asks_dict[p] += v

            c_bids_raw = [[p, v] for p, v in sorted(comb_bids_dict.items(), key=lambda x: x[0], reverse=True)]
            c_asks_raw = [[p, v] for p, v in sorted(comb_asks_dict.items(), key=lambda x: x[0], reverse=True)]

            deepest_ask = float(c_asks_raw[0][0]) if c_asks_raw else current_price
            deepest_bid = float(c_bids_raw[-1][0]) if c_bids_raw else current_price
            
            ask_depth_pct = (deepest_ask - current_price) / current_price * 100 if current_price else 0
            bid_depth_pct = (current_price - deepest_bid) / current_price * 100 if current_price else 0
            max_depth = max(ask_depth_pct, bid_depth_pct)

            if uptime_secs < 30 or max_depth < 1.0:
                confianza = "[bold red]🔴 BAJA (Acumulando)[/bold red]"
                header_style = "bold red"
            elif uptime_secs < 120 or max_depth < 5.0:
                confianza = "[bold yellow]🟡 MEDIA (Aceptable)[/bold yellow]"
                header_style = "bold yellow"
            else:
                confianza = "[bold green]🟢 ALTA (Fiable)[/bold green]"
                header_style = "bold green"

            tape_color = "green" if t_delta > 0 else "red" if t_delta < 0 else "white"
            cvd_color = "green" if session_cvd > 0 else "red" if session_cvd < 0 else "white"
            
            liq_text = ""
            if longs_rekt > 0: liq_text += f" 🔥 [bold red]Longs Liq: {longs_rekt:.2f}[/bold red]"
            if shorts_rekt > 0: liq_text += f" 💥 [bold green]Shorts Liq: {shorts_rekt:.2f}[/bold green]"
            if not liq_text: liq_text = "[dim]0 liquidaciones recientes[/dim]"

            zoom_decimals = get_auto_zoom_levels(current_price)

            spot_bids = recursive_zoom_search(s_bids_raw, zoom_decimals, ignore_ticks=anti_spoof)
            spot_asks = recursive_zoom_search(s_asks_raw, zoom_decimals, ignore_ticks=anti_spoof)
            fut_bids = recursive_zoom_search(f_bids_raw, zoom_decimals, ignore_ticks=anti_spoof)
            fut_asks = recursive_zoom_search(f_asks_raw, zoom_decimals, ignore_ticks=anti_spoof)
            comb_bids = recursive_zoom_search(c_bids_raw, zoom_decimals, ignore_ticks=anti_spoof)
            comb_asks = recursive_zoom_search(c_asks_raw, zoom_decimals, ignore_ticks=anti_spoof)

            avg_bids = calculate_averaged_points(comb_bids)
            avg_asks = calculate_averaged_points(comb_asks)

            # Renderizamos tablas pasando los decimales calculados y capturamos señales
            t_spot, bid_al_sp, ask_al_sp, _ = create_table("1. Mercado Spot", spot_bids, spot_asks, current_price, spot_book.is_synced, t_delta, alert_pct, decs)
            t_fut, bid_al_fu, ask_al_fu, _ = create_table("2. Mercado Futuros", fut_bids, fut_asks, current_price, futures_book.is_synced, t_delta, alert_pct, decs)
            t_comb, bid_al_cb, ask_al_cb, global_imbalance = create_table("3. Unificado (Muros Exactos)", comb_bids, comb_asks, current_price, True, t_delta, alert_pct, decs)
            t_avg, _, _, _ = create_table("4. Unificado (Promedios)", avg_bids, avg_asks, current_price, True, t_delta, alert_pct, decs)

            layout["main_tables"]["row_top"]["spot"].update(Panel(t_spot))
            layout["main_tables"]["row_top"]["futures"].update(Panel(t_fut))
            layout["main_tables"]["row_bottom"]["combined"].update(Panel(t_comb))
            layout["main_tables"]["row_bottom"]["averaged"].update(Panel(t_avg))

            # ==========================================================
            # 🧠 MOTOR DE CONFLUENCIA E INTELIGENCIA DE REBOTES
            # ==========================================================
            is_bid_impact = bid_al_sp or bid_al_fu or bid_al_cb
            is_ask_impact = ask_al_sp or ask_al_fu or ask_al_cb
            
            confluence_signal = "[dim]Monitoreando mercado en busca de confluencias óptimas...[/dim]"
            
            # Condición de Rebote ALCISTA (LONG)
            if is_bid_impact and global_imbalance > 10.0 and (t_delta > 0 or shorts_rekt > 0):
                confluence_signal = "[bold green blink]🚀 SEÑAL LONG CONFIRMADA: Impacto en Muro + Absorción + Imán Alcista[/bold green blink]"
                log_signal(spot_book.symbol, current_price, "LONG", f"Imbalance: +{global_imbalance:.1f}%, Delta: {t_delta:,.1f}, Liq_Shorts: {shorts_rekt:,.1f}")
                
            # Condición de Rebote BAJISTA (SHORT)
            elif is_ask_impact and global_imbalance < -10.0 and (t_delta < 0 or longs_rekt > 0):
                confluence_signal = "[bold red blink]🩸 SEÑAL SHORT CONFIRMADA: Impacto en Muro + Absorción + Imán Bajista[/bold red blink]"
                log_signal(spot_book.symbol, current_price, "SHORT", f"Imbalance: {global_imbalance:.1f}%, Delta: {t_delta:,.1f}, Liq_Longs: {longs_rekt:,.1f}")

            # Header con señales de confirmación integradas
            header_text = (
                f"[bold cyan]HFT ENGINE REBOTES[/bold cyan] | [bold yellow]{spot_book.symbol}[/bold yellow] | Uptime: [white]{int(uptime_secs)}s[/white] | Confianza: {confianza}\n"
                f"[dim]Agresividad (60s) ->[/dim] Delta: [{tape_color}]{t_delta:+,.2f}[/{tape_color}] | CVD Sesión: [{cvd_color}]{session_cvd:+,.2f}[/{cvd_color}] | 🚨 Liq: {liq_text}\n"
                f"🧠 [bold white]GATILLO DE CONFLUENCIA:[/bold white] {confluence_signal}"
            )
            layout["header"].update(Panel(Align.center(header_text), style=header_style))

            # Alarma con enfriamiento
            if sound_enabled and (bid_al_sp or bid_al_fu or bid_al_cb or ask_al_sp or ask_al_fu or ask_al_cb):
                current_timestamp = time.time()
                if current_timestamp - last_beep_time > 10:
                    console.bell()
                    last_beep_time = current_timestamp

            spot_eps = (spot_book.processed_events_count - last_spot_events) / ui_refresh
            fut_eps = (futures_book.processed_events_count - last_fut_events) / ui_refresh
            total_events = spot_book.processed_events_count + futures_book.processed_events_count
            
            last_spot_events = spot_book.processed_events_count
            last_fut_events = futures_book.processed_events_count

            desc = f"[bold green]✅ LISTO[/bold green] | Mapeado: [yellow]{total_events:,} org[/yellow] | RAM: [cyan]{int(spot_eps + fut_eps)} act/s[/cyan]"
            progress.reset(task_id, total=ui_refresh, description=desc)
            layout["footer"].update(Panel(progress, border_style="green"))

            steps = int(ui_refresh * 10) if ui_refresh >= 0.1 else 1
            sleep_interval = ui_refresh / steps
            for i in range(steps):
                time_remaining = ui_refresh - (i * sleep_interval)
                progress.update(task_id, advance=sleep_interval, tiempo_restante=f"{max(0, time_remaining):.1f}")
                await asyncio.sleep(sleep_interval)

async def main():
    config = load_config()
    default_symbol = config.get("symbol", "BTCUSDT")
    
    console.clear()
    console.print(Panel("[bold cyan]=== INICIANDO MOTOR HFT DE LIQUIDEZ Y REBOTES ===[/bold cyan]", expand=False, border_style="cyan"))
    console.print("[dim]Presiona Enter para usar el par por defecto o escribe uno nuevo.[/dim]")
    
    symbol_input = Prompt.ask("\n[bold yellow]Ingrese el par a analizar (Ej: BTCUSDT, ETHUSDT, XRPUSDT, PEPEUSDT)[/bold yellow]", default=default_symbol)
    symbol = symbol_input.strip().upper()
    
    if symbol != default_symbol:
        config["symbol"] = symbol
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
            
    console.print(f"\n[bold green]✓ Preparando motores para el par {symbol}...[/bold green]")
    await asyncio.sleep(1) 
    
    spot_book = LocalOrderBook(symbol, is_futures=False)
    futures_book = LocalOrderBook(symbol, is_futures=True)
    tape_book = MarketTrades(symbol, window_seconds=60)
    liq_book = LiquidationTracker(symbol, window_seconds=60)

    await asyncio.gather(
        spot_book.connect_and_maintain(),
        futures_book.connect_and_maintain(),
        tape_book.connect_and_maintain(),
        liq_book.connect_and_maintain(),
        ui_loop(spot_book, futures_book, tape_book, liq_book, config)
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[bold red]Apagando motores de HFT y vaciando RAM...[/bold red]")
