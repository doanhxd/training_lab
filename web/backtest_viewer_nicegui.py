from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from nicegui import ui

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'backtest_report_manifest.js'


def load_manifest() -> dict[str, Any]:
    if not MANIFEST.exists():
        return {'runs': [], 'count': 0}
    text = MANIFEST.read_text(encoding='utf-8')
    prefix = 'window.BACKTEST_REPORT_MANIFEST = '
    if not text.startswith(prefix):
        raise ValueError(f'Unsupported manifest format: {MANIFEST}')
    return json.loads(text[len(prefix):].strip().rstrip(';'))


def money(value: Any) -> str:
    try:
        return f'${float(value):,.2f}'
    except (TypeError, ValueError):
        return '—'


def pct(value: Any) -> str:
    try:
        number = float(value)
        if abs(number) <= 1:
            number *= 100
        return f'{number:.2f}%'
    except (TypeError, ValueError):
        return '—'


def number(value: Any) -> str:
    try:
        return f'{float(value):,.2f}'
    except (TypeError, ValueError):
        return '—'


def short_label(run: dict[str, Any]) -> str:
    raw = str(run.get('strategy_name') or 'unknown')
    return f"{run.get('modified_at', '')[:19].replace('T', ' ')} · {run.get('run_id', '')} · {raw[:48]}"


def metric_card(title: str, value: str, subtitle: str, color: str = 'text-green-300') -> None:
    with ui.card().classes('metric-card'):
        ui.label(title).classes('text-xs uppercase tracking-wide text-slate-400')
        ui.label(value).classes(f'text-2xl font-bold {color}')
        ui.label(subtitle).classes('text-xs text-slate-500')


def detail_table(title: str, rows: list[tuple[str, str]]) -> None:
    with ui.card().classes('detail-card'):
        ui.label(title).classes('text-base font-bold text-slate-100 mb-2')
        with ui.column().classes('w-full gap-0'):
            for label, value in rows:
                with ui.row().classes('w-full justify-between items-start border-b border-slate-800 py-2 gap-4'):
                    ui.label(label).classes('text-xs text-slate-400')
                    ui.label(value).classes('text-xs text-slate-200 text-right')


def create_app() -> None:
    manifest = load_manifest()
    runs = manifest.get('runs') or []
    options = {run['run_id']: short_label(run) for run in runs}
    selected = runs[0] if runs else None

    ui.add_head_html('''
    <style>
      :root { color-scheme: dark; }
      body { margin: 0; background: radial-gradient(circle at top, #152019 0%, #0d1311 42%); color: #ebf3ee; font-family: Segoe UI, Arial, sans-serif; }
      .page { width: min(1600px, calc(100% - 12px)); max-width: 1600px; margin: 0 auto; padding: 18px 0 46px; }
      .metric-card, .detail-card, .chart-card { background: linear-gradient(180deg, rgba(255,255,255,.02), rgba(255,255,255,.01)) !important; background-color: #161f1b !important; border: 1px solid #2b3932; border-radius: 18px; box-shadow: 0 18px 40px rgba(0,0,0,.28) !important; }
      .metric-card { min-width: 0; padding: 12px; }
      .detail-card { padding: 14px; }
      .chart-card { padding: 16px; }
      .q-card { box-shadow: none; }
      .q-field--outlined .q-field__control { border-radius: 12px; background: #101814; }
      .q-field--outlined .q-field__control:before { border-color: #3d4e45; }
      .q-field--outlined .q-field__control:hover:before { border-color: #87e7a5; }
      .q-table__container { background: #161f1b; border: 1px solid #2b3932; border-radius: 12px; }
      .q-table thead, .q-table th { background: #1d2722; color: #9db0a4; }
      .q-table tbody td { color: #ebf3ee; border-color: #2b3932; }
      .q-table tbody tr:hover { background: rgba(135,231,165,.08); }
      .q-btn--outline { border-color: #3d4e45; color: #87e7a5; }
      .summary-grid { grid-template-columns: 320px minmax(0, 1fr); }
      .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); align-content: start; }
      .detail-grid { grid-template-columns: minmax(0, 1.08fr) minmax(0, .92fr); }
      @media (max-width: 1050px) {
        .summary-grid { grid-template-columns: 1fr; }
        .metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
        .detail-grid { grid-template-columns: 1fr; }
      }
      @media (max-width: 640px) {
        .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      }
    </style>
    ''')

    dark_mode = ui.dark_mode(value=True)
    with ui.column().classes('page w-full p-4 gap-3'):
        with ui.row().classes('w-full items-start justify-between'):
            with ui.column().classes('gap-1'):
                ui.label('BACKTEST REPORT VIEWER · NICEGUI').classes('text-xs tracking-[.18em] text-green-300 font-bold')
                ui.label('Backtest Report Viewer').classes('text-3xl font-bold')
                ui.label('Chọn run từ manifest, xem KPI, contract, execution stats và Monthly detail trong một giao diện Python web.').classes('text-sm text-slate-400')
            ui.switch('Dark mode', value=True, on_change=lambda event: dark_mode.enable() if event.value else dark_mode.disable()).props('color=green').classes('mt-1')

        with ui.row().classes('w-full items-end gap-3'):
            run_select = ui.select(options=options, value=selected['run_id'] if selected else None, label='Backtest run').classes('grow')
            reload_button = ui.button('Reload manifest', icon='refresh').props('outline')
        status = ui.label(f'{len(runs)} run trong manifest · mới nhất ở trên').classes('text-xs text-slate-500')

        hero = ui.card().classes('chart-card w-full p-4')
        with hero:
            hero_title = ui.label('Chưa có run').classes('text-xl font-bold')
            hero_meta = ui.label('').classes('text-xs text-slate-400')

        summary_grid = ui.element('div').classes('summary-grid grid gap-2 w-full')
        with summary_grid:
            metrics_grid = ui.element('div').classes('metric-grid grid gap-2 w-full')
            details_row = ui.element('div').classes('detail-grid grid gap-2 w-full')
        equity_card = ui.card().classes('chart-card w-full p-4')
        monthly_card = ui.card().classes('chart-card w-full p-4')

        def render(run: dict[str, Any] | None) -> None:
            metrics_grid.clear()
            details_row.clear()
            equity_card.clear()
            monthly_card.clear()
            if not run:
                hero_title.text = 'Chưa có run trong manifest'
                hero_meta.text = 'Hãy chạy scripts/build_backtest_viewer_manifest.py trước.'
                return
            config = run.get('backtest_config') or {}
            metrics = run.get('metrics_summary') or {}
            validation = run.get('validation_results') or {}
            signals = run.get('signal_counts') or {}
            monthly = run.get('monthly') or []
            final_equity = metrics.get('final_equity') or metrics.get('ending_equity') or (monthly[-1].get('endEquity') if monthly else None)
            with metrics_grid:
                metric_card('Vốn cuối', money(final_equity), 'ending equity')
                metric_card('Tổng lãi', money(final_equity), 'End equity kỳ cuối')
                metric_card('Win rate', pct(metrics.get('win_rate')), f"{metrics.get('wins', '—')} wins / {metrics.get('total_trades', '—')} trades")
                metric_card('Max DD', pct(metrics.get('max_drawdown_pct', metrics.get('max_drawdown'))), 'overall max DD', 'text-red-300')
                metric_card('Max Daily DD', pct(run.get('daily_drawdown', {}).get('max_daily_drawdown')), f"worst {run.get('daily_drawdown', {}).get('worst_day', '—')}", 'text-amber-300')
                metric_card('Tổng lệnh', number(metrics.get('total_trades', run.get('trade_rows'))), f"PF {number(metrics.get('profit_factor'))}")
            hero_title.text = short_label(run)
            hero_meta.text = f"Dataset: {run.get('dataset_id') or '—'} · Modified: {run.get('modified_at') or '—'} · Validation: {'PASSED' if validation.get('passed') else '—'}"
            with details_row:
                detail_table('Contract & Validation', [
                    ('Initial equity', money(config.get('initial_equity'))),
                    ('Risk / Reward USD', f"{money(config.get('risk_usd'))} / {money(config.get('reward_usd'))}"),
                    ('R:R ratio', str(config.get('risk_reward_ratio') or config.get('rr') or '—')),
                    ('Volume lots', number(config.get('volume_lots'))),
                    ('Backtest period', f"{run.get('data_coverage', {}).get('start', '—')} → {run.get('data_coverage', {}).get('end', '—')}"),
                    ('Validation', 'PASSED' if validation.get('passed') else '—'),
                ])
                detail_table('Signal / Execution Stats', [
                    ('Long signals', number(signals.get('long_signal'))),
                    ('Short signals', number(signals.get('short_signal'))),
                    ('Entered long', number(signals.get('entered_long'))),
                    ('Entered short', number(signals.get('entered_short'))),
                    ('Expectancy', number(metrics.get('expectancy'))),
                    ('Exposure time', pct(metrics.get('exposure_time'))),
                ])
            with equity_card:
                ui.label('Equity Curve').classes('text-base font-bold text-slate-100')
                equity_sample = run.get('equity_sample') or []
                if not equity_sample:
                    ui.label('Chưa có equity_curve.csv trong manifest.').classes('text-sm text-slate-500 mt-3')
                else:
                    ui.echart({
                        'backgroundColor': 'transparent',
                        'tooltip': {'trigger': 'axis'},
                        'grid': {'left': 55, 'right': 20, 'top': 20, 'bottom': 35},
                        'xAxis': {'type': 'category', 'boundaryGap': False, 'data': [point.get('index') for point in equity_sample], 'axisLabel': {'color': '#9db0a4'}},
                        'yAxis': {'type': 'value', 'axisLabel': {'color': '#9db0a4'}, 'splitLine': {'lineStyle': {'color': '#2b3932', 'type': 'dashed'}}},
                        'series': [{'type': 'line', 'smooth': False, 'showSymbol': False, 'data': [point.get('equity') for point in equity_sample], 'lineStyle': {'color': '#87e7a5', 'width': 3}, 'itemStyle': {'color': '#87e7a5'}, 'areaStyle': {'color': 'rgba(135,231,165,.06)'}}],
                    }).classes('w-full h-96')
            with monthly_card:
                ui.label('Monthly Net P&L').classes('text-base font-bold text-slate-100')
                if not monthly:
                    ui.label('Chưa có monthly data trong manifest.').classes('text-sm text-slate-500 mt-3')
                else:
                    chart = ui.echart({
                        'backgroundColor': 'transparent',
                        'tooltip': {'trigger': 'axis'},
                        'xAxis': {'type': 'category', 'data': [row.get('month') for row in monthly]},
                        'yAxis': {'type': 'value'},
                        'series': [{'type': 'bar', 'data': [row.get('pnl', 0) for row in monthly], 'itemStyle': {'color': '#87e7a5'}}],
                    }).classes('w-full h-64')
                    detail = ui.label('Chọn một tháng để xem detail.').classes('text-sm text-slate-400 mt-2')
                    table = ui.table(columns=[
                        {'name': 'month', 'label': 'Month', 'field': 'month'},
                        {'name': 'trades', 'label': 'Trades', 'field': 'trades'},
                        {'name': 'winRate', 'label': 'WR', 'field': 'winRate'},
                        {'name': 'pnl', 'label': 'Net P&L', 'field': 'pnl'},
                        {'name': 'endEquity', 'label': 'End equity', 'field': 'endEquity'},
                    ], rows=[{**row, 'winRate': pct(row.get('winRate')), 'pnl': money(row.get('pnl')), 'endEquity': money(row.get('endEquity'))} for row in monthly], row_key='month').classes('w-full mt-3')
                    initial_month = monthly[-1]
                    with ui.card().classes('detail-card w-full md:w-96 mt-3'):
                        ui.label('Selected Month Detail').classes('text-base font-bold text-slate-100')
                        month_detail = ui.label('').classes('text-sm text-slate-300 whitespace-pre-line')
                    month_detail.text = (
                        f"Month: {initial_month.get('month', '—')}\n"
                        f"Trades (Win / Losses): {initial_month.get('trades', '—')} ({initial_month.get('wins', '—')} / {initial_month.get('losses', '—')})\n"
                        f"Win rate: {pct(initial_month.get('winRate'))}\n"
                        f"Net P&L: {money(initial_month.get('pnl'))}\n"
                        f"Profit factor: {number(initial_month.get('profitFactor'))}\n"
                        f"DD: {money(initial_month.get('drawdownUsd'))} / {pct(initial_month.get('drawdownPct'))}\n"
                        f"Max DD: {money(initial_month.get('maxDailyDrawdownUsd'))} / {pct(initial_month.get('maxDailyDrawdownPct'))}\n"
                        f"End equity: {money(initial_month.get('endEquity'))}"
                    )
                    def select_month(event: Any) -> None:
                        row = event.args.get('rows', [None])[0] if isinstance(event.args, dict) else None
                        if row:
                            detail.text = f"{row.get('month')}: {row.get('trades')} trades · {row.get('winRate')} WR · {row.get('pnl')} · End equity {row.get('endEquity')}"
                            month_detail.text = (
                                f"Month: {row.get('month', '—')}\n"
                                f"Trades: {row.get('trades', '—')}\n"
                                f"Win rate: {row.get('winRate', '—')}\n"
                                f"Net P&L: {row.get('pnl', '—')}\n"
                                f"End equity: {row.get('endEquity', '—')}"
                            )
                    table.on('rowClick', select_month)

        def on_select(event: Any) -> None:
            run = next((item for item in runs if item.get('run_id') == event.value), None)
            render(run)

        def reload_manifest() -> None:
            nonlocal runs, options
            fresh = load_manifest()
            runs = fresh.get('runs') or []
            options = {run['run_id']: short_label(run) for run in runs}
            run_select.options = options
            run_select.update()
            status.text = f'{len(runs)} run trong manifest · mới nhất ở trên'
            render(runs[0] if runs else None)

        run_select.on_value_change(on_select)
        reload_button.on_click(reload_manifest)
        render(selected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='NiceGUI backtest report viewer')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    create_app()
    ui.run(host=args.host, port=args.port, title='Backtest Report Viewer · NiceGUI', reload=False)
