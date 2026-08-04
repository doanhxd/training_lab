(() => {
  'use strict';
  const manifest = window.BACKTEST_REPORT_MANIFEST || {runs: []};
  const state = {runs: [], active: null, compare: false, query: '', sort: 'newest', side: 'all', exit: 'all', tradeQuery: '', selectedMonth: null, selectedDay: null};
  const $ = id => document.getElementById(id);
  const n = (v, fallback = NaN) => { const x = Number(v); return Number.isFinite(x) ? x : fallback; };
  const fmt = (v, d = 2) => Number.isFinite(n(v)) ? n(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d}) : '—';
  const money = (v, d = 2) => Number.isFinite(n(v)) ? `${n(v) < 0 ? '−' : ''}$${Math.abs(n(v)).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d})}` : '—';
  const int = v => Number.isFinite(n(v)) ? Math.round(n(v)).toLocaleString('en-US') : '—';
  const pct = v => { const x = n(v); if (!Number.isFinite(x)) return '—'; return `${(Math.abs(x) <= 1 ? x * 100 : x).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}%`; };
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const cfg = r => r?.backtest_config || {};
  const metrics = r => r?.metrics_summary || {};
  const factor = r => n(cfg(r).volume_lots, 0) * n(cfg(r).price_value_per_lot, 100);
  const monthlyRows = r => Array.isArray(r?.monthly) ? r.monthly : [];
  const dailyRows = r => Array.isArray(r?.daily_drawdown?.daily_rows) ? r.daily_drawdown.daily_rows : [];
  const monthPnl = row => n(row?.pnl, n(row?.net_pnl, n(row?.net_pnl_usd, 0)));
  const net = r => {
    const m = metrics(r), f = factor(r);
    return n(m.net_pnl_usd, n(m.net_pnl, NaN)) || (Number.isFinite(n(m.net_pnl_price)) ? n(m.net_pnl_price) * f : (Number.isFinite(n(m.net_return)) ? n(m.net_return) * n(cfg(r).initial_equity, 0) : NaN));
  };
  // Total Profit is the full-period realized P&L, not the last monthly row or trade_sample.
  const totalProfit = r => monthlyRows(r).length ? monthlyRows(r).reduce((sum, row) => sum + monthPnl(row), 0) : net(r);
  const gross = r => { const m = metrics(r), f = factor(r); return n(m.gross_profit_usd, n(m.gross_profit, NaN)) || (Number.isFinite(n(m.gross_profit_price)) ? n(m.gross_profit_price) * f : NaN); };
  const loss = r => { const m = metrics(r), f = factor(r); return n(m.gross_loss_usd, n(m.gross_loss, NaN)) || (Number.isFinite(n(m.gross_loss_price)) ? n(m.gross_loss_price) * f : NaN); };
  const dd = r => { const m = metrics(r), f = factor(r); return n(m.max_drawdown_usd, NaN) || (Number.isFinite(n(m.max_drawdown_price)) ? n(m.max_drawdown_price) * f : (Number.isFinite(n(m.max_drawdown)) ? n(m.max_drawdown) * n(cfg(r).initial_equity, 0) : NaN)); };
  const trades = r => n(metrics(r).total_trades, n(r?.trade_rows, 0));
  const moneyContract = r => { const c = cfg(r); const valuePerLot = n(c.price_value_per_lot, 100); const volume = n(c.volume_lots, 0); const sl = Number.isFinite(n(c.risk_usd)) ? n(c.risk_usd) : (Number.isFinite(n(c.sl_price)) ? Math.abs(n(c.sl_price)) * volume * valuePerLot : NaN); const tp = Number.isFinite(n(c.reward_usd)) ? n(c.reward_usd) : (Number.isFinite(n(c.tp_price)) ? Math.abs(n(c.tp_price)) * volume * valuePerLot : NaN); const parts = []; if (Number.isFinite(sl)) parts.push(`SL ${money(sl, 0)}`); if (Number.isFinite(tp)) parts.push(`TP ${money(tp, 0)}`); return parts.join(' · '); };
  const runLabel = r => { const c = cfg(r), parts = []; if (c.volume_lots != null) parts.push(`${fmt(c.volume_lots, 2)} lot`); const contract = moneyContract(r); if (contract) parts.push(contract); return parts.join(' · ') || String(r.strategy_name || 'Backtest run'); };
  const period = r => { const c = r?.data_coverage || {}; return `${String(c.first_timestamp || '').slice(0, 10)} → ${String(c.last_timestamp || '').slice(0, 10)}`; };

  function sortedRuns() {
    let rows = [...state.runs];
    const q = state.query.toLowerCase();
    if (q) rows = rows.filter(r => JSON.stringify(r).toLowerCase().includes(q));
    if (state.sort === 'net') rows.sort((a, b) => totalProfit(b) - totalProfit(a));
    else if (state.sort === 'pf') rows.sort((a, b) => n(metrics(b).profit_factor) - n(metrics(a).profit_factor));
    else if (state.sort === 'dd') rows.sort((a, b) => dd(a) - dd(b));
    else rows.sort((a, b) => n(b.modified_ts) - n(a.modified_ts));
    return rows;
  }
  function renderRuns() {
    const rows = sortedRuns();
    $('runCount').textContent = rows.length;
    $('runList').innerHTML = rows.map(r => `<button class="run-item ${state.active?.run_id === r.run_id ? 'active' : ''}" data-run="${esc(r.run_id)}"><div class="run-id">${esc(r.run_id)}</div><div class="run-name">${esc(runLabel(r))}</div><div class="run-meta"><span>${esc(period(r))}</span><span class="run-profit">${money(totalProfit(r), 0)}</span></div></button>`).join('') || '<div class="muted" style="padding:12px">Không có run phù hợp.</div>';
    document.querySelectorAll('[data-run]').forEach(b => b.onclick = () => selectRun(b.dataset.run));
  }
  function selectRun(id) {
    state.active = state.runs.find(r => r.run_id === id) || state.runs[0];
    if (!state.active) return;
    state.selectedMonth = monthlyRows(state.active).at(-1)?.month || null;
    state.selectedDay = dailyRows(state.active).at(-1)?.day || null;
    $('emptyState').hidden = true;
    $('runView').hidden = false;
    renderRuns();
    renderAll();
  }
  function renderAll() {
    const r = state.active, c = cfg(r), m = metrics(r);
    $('runTitle').textContent = runLabel(r);
    $('runSubtitle').textContent = `${r.strategy_name || 'GoldQuantV1'} · ${period(r)} · ${r.dataset_id || 'embedded dataset'}`;
    const execution = String(r.execution_enabled ?? c.execution_enabled ?? false).toLowerCase() === 'true';
    $('executionBadge').textContent = execution ? 'EXECUTION ON' : 'EXECUTION OFF';
    $('executionBadge').className = `badge ${execution ? 'warn' : 'safe'}`;
    $('coverageBadge').textContent = `${int(r.data_coverage?.row_count ?? r.data_coverage?.bars ?? r.equity_rows)} M5 bars`;
    $('mNet').textContent = money(totalProfit(r));
    $('mGross').textContent = `from ${String(r.data_coverage?.first_timestamp || 'start').slice(0, 10)} → ${String(r.data_coverage?.last_timestamp || 'end').slice(0, 10)}`;
    $('mPf').textContent = Number.isFinite(n(m.profit_factor)) ? fmt(m.profit_factor, 3) : '—';
    $('mTrades').textContent = `${int(trades(r))} trades`;
    $('mDd').textContent = money(dd(r));
    $('mDdPct').textContent = `${pct(m.max_drawdown)} peak-to-trough`;
    $('mWr').textContent = pct(m.win_rate);
    $('mWins').textContent = `${int(n(m.wins, n(m.total_trades) * n(m.win_rate)))} wins`;
    const initial = n(c.initial_equity, 0);
    $('mEquity').textContent = money(initial + net(r));
    $('mInitial').textContent = `initial ${money(initial)}`;
    renderContract(r); renderEquity(r); renderMonthly(r); renderDaily(r); renderTrades(r); renderCompare();
  }
  function cell(label, value) { return `<div class="contract-cell"><label>${esc(label)}</label><strong>${value}</strong></div>`; }
  function renderContract(r) {
    const c = cfg(r), block = (c.no_trade_start_hour_gmt7 === 24 && c.no_trade_end_hour_gmt7 === 24) ? 'Không block' : `${fmt(c.no_trade_start_hour_gmt7, 0)}:00–${fmt(c.no_trade_end_hour_gmt7, 0)}:00 GMT+7`;
    $('contractGrid').innerHTML = [cell('Symbol', esc(c.symbol || 'XAUUSD')), cell('Timeframe', esc(c.timeframe || 'M5')), cell('Volume', `${fmt(c.volume_lots, 2)} lot`), cell('TP distance', `${fmt(c.tp_price, 2)} giá`), cell('SL distance', `${fmt(c.sl_price, 2)} giá`), cell('Trailing', c.trailing_enabled === false ? 'OFF' : `+$${fmt(c.trailing_trigger_usd)} → +$${fmt(c.trailing_lock_usd)}`), cell('Trailing step', c.trailing_enabled === false ? '—' : `+$${fmt(c.trailing_step_usd)}`), cell('OPEN block', block), cell('Entry', `RSI<${esc(c.rsi_entry_long ?? 55)} / RSI>${esc(c.rsi_entry_short ?? 45)}`), cell('Gradient / warmup', `${esc(c.gradient_periods ?? 30)} / ${esc(c.warmup_bars ?? 120)} bars`), cell('Costs', `spread ${fmt(c.max_spread, 3)} · slip ${fmt(c.slippage_per_side, 3)}`), cell('Commission', money(c.commission_per_trade_usd ?? 0))].join('');
  }
  function sampleEquity(r) { return Array.isArray(r.equity_sample) ? r.equity_sample.map(x => n(x.equity ?? x.value ?? x)) : []; }
  function renderEquity(r) {
      const c = cfg(r); const vals = sampleEquity(r); $('equityMeta').textContent = `${int(vals.length)} embedded points`; const initialBalance = n(c.initial_equity, n(c.initial_balance, NaN)); $('initialBalanceMeta').textContent = `Init balance ${money(initialBalance)}`;
    if (vals.length < 2) { $('equityChart').innerHTML = '<div class="chart-empty">Chưa có equity sample.</div>'; return; }
    const w = 900, h = 270, p = 26, min = Math.min(...vals), max = Math.max(...vals), range = max - min || 1;
    const pts = vals.map((v, i) => [p + i * (w - 2 * p) / (vals.length - 1), h - p - (v - min) * (h - 2 * p) / range]);
    const path = pts.map((q, i) => (i ? 'L' : 'M') + q[0].toFixed(1) + ' ' + q[1].toFixed(1)).join(' ');
    $('equityChart').innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="eq" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#e7b65c" stop-opacity=".28"/><stop offset="1" stop-color="#e7b65c" stop-opacity="0"/></linearGradient></defs><path d="M${p} ${h-p} ${path.slice(1)} L${w-p} ${h-p} Z" fill="url(#eq)"/><path d="${path}" fill="none" stroke="#e7b65c" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><text x="${p}" y="16" fill="var(--muted)" font-size="11">${money(max, 0)}</text><text x="${p}" y="${h-3}" fill="var(--muted)" font-size="11">${money(min, 0)}</text><text x="${w-p}" y="16" fill="var(--muted)" font-size="11" text-anchor="end">${money(vals.at(-1), 0)}</text></svg>`;
  }
  function renderMonthly(r) {
      const rows = monthlyRows(r);
      if (!rows.length) { $('monthlyChart').innerHTML = '<div class="chart-empty">Chưa có monthly ledger.</div>'; $('monthDetail').innerHTML = ''; return; }
      const max = Math.max(...rows.map(x => Math.abs(monthPnl(x))), 1);
      $('monthlyChart').innerHTML = rows.map(x => {
        const v = monthPnl(x), key = x.month || x.period;
        return `<button class="month-bar-wrap ${key === state.selectedMonth ? 'active' : ''}" data-month="${esc(key)}" title="${esc(key)} · ${money(v)}"><span class="month-value ${v < 0 ? 'neg' : 'pos'}">${money(v, 0)}</span><span class="month-bar ${v < 0 ? 'loss' : ''}" style="height:${Math.max(4, Math.abs(v) / max * 118)}px"></span><span class="month-label">${esc(String(key || '').slice(0, 7))}</span></button>`;
      }).join('');
      document.querySelectorAll('[data-month]').forEach(b => b.onclick = () => {
        state.selectedMonth = b.dataset.month;
        state.selectedDay = dailyRows(r).filter(x => String(x.day).startsWith(state.selectedMonth)).at(-1)?.day || state.selectedDay;
        renderMonthly(r); renderDaily(r);
      });
      const selected = rows.find(x => (x.month || x.period) === state.selectedMonth) || rows.at(-1);
      $('monthDetail').innerHTML = selected ? `<div class="detail-title">Selected month · ${esc(selected.month || selected.period)}</div><div class="detail-grid"><span>Trades<strong>${int(selected.trades)}</strong></span><span>Win rate<strong>${pct(selected.winRate)}</strong></span><span>Total PnL<strong class="${monthPnl(selected) >= 0 ? 'pos' : 'neg'}">${money(monthPnl(selected))}</strong></span><span>PF<strong>${Number.isFinite(n(selected.profitFactor)) ? fmt(selected.profitFactor, 2) : '—'}</strong></span><span>DD<strong>${money(selected.drawdownUsd)} / ${pct(selected.drawdownPct)}</strong></span><span>Max daily DD<strong>${money(selected.maxDailyDrawdownUsd)} / ${pct(selected.maxDailyDrawdownPct)}</strong></span></div>` : '';
    }
    function renderDaily(r) {
        const all = dailyRows(r), rows = state.selectedMonth ? all.filter(x => String(x.day || '').startsWith(state.selectedMonth)) : all;
    $('dailyFilterLabel').textContent = state.selectedMonth ? `Month ${state.selectedMonth}` : 'All days';
    if (!rows.length) { $('dailyChart').innerHTML = '<div class="chart-empty">Chưa có daily ledger.</div>'; $('dailyDetail').innerHTML = '<div class="chart-empty">Chọn một ngày để xem chi tiết.</div>'; $('dailyTable').innerHTML = ''; return; }
    const max = Math.max(...rows.map(x => Math.abs(n(x.pnl, 0))), 1);
    $('dailyChart').innerHTML = rows.map(x => { const v = n(x.pnl, 0), key = x.day; return `<button class="daily-bar ${key === state.selectedDay ? 'active' : ''}" data-day="${esc(key)}" title="${esc(key)} · ${money(v)}"><span class="daily-value ${v < 0 ? 'neg' : 'pos'}">${money(v, 0)}</span><span class="daily-bar-fill ${v < 0 ? 'loss' : ''}" style="height:${Math.max(5, Math.abs(v) / max * 150)}px"></span><span>${esc(String(key).slice(5, 10))}</span></button>`; }).join('');
    document.querySelectorAll('[data-day]').forEach(b => b.onclick = () => { state.selectedDay = b.dataset.day; renderDaily(r); });
    const selected = rows.find(x => x.day === state.selectedDay) || rows.at(-1); state.selectedDay = selected.day;
    $('dailyDetail').innerHTML = `<div class="detail-title">Selected day · ${esc(selected.day)}</div><div class="detail-grid"><span>Trades<strong>${int(selected.trades ?? selected.trade_count)}</strong></span><span>Win rate<strong>${pct(selected.winRate)}</strong></span><span>Day PnL<strong class="${n(selected.pnl) >= 0 ? 'pos' : 'neg'}">${money(selected.pnl)}</strong></span><span>PF<strong>${Number.isFinite(n(selected.profitFactor)) ? fmt(selected.profitFactor, 2) : '—'}</strong></span><span>Daily DD<strong>${money(selected.daily_drawdown_usd)} / ${pct(selected.daily_drawdown)}</strong></span><span>Long / Short<strong>${int(selected.long)} / ${int(selected.short)}</strong></span><span>Start equity<strong>${money(selected.start_equity)}</strong></span><span>End equity<strong>${money(selected.end_equity)}</strong></span></div>`;
    $('dailyTable').innerHTML = `<table><thead><tr><th>Day</th><th>Trades</th><th>PF</th><th>WR</th><th>Net PnL</th><th>Expectancy</th><th>Daily DD $ / %</th><th>Long / Short</th><th>End equity</th></tr></thead><tbody>${rows.map(x => `<tr class="${x.day === state.selectedDay ? 'active' : ''}" data-day="${esc(x.day)}"><td>${esc(x.day)}</td><td>${int(x.trades ?? x.trade_count)}</td><td>${Number.isFinite(n(x.profitFactor)) ? fmt(x.profitFactor, 2) : '—'}</td><td>${pct(x.winRate)}</td><td class="${n(x.pnl) >= 0 ? 'pos' : 'neg'}">${money(x.pnl)}</td><td>${money(x.expectancy)}</td><td>${money(x.daily_drawdown_usd)} / ${pct(x.daily_drawdown)}</td><td>${int(x.long)} / ${int(x.short)}</td><td>${money(x.end_equity)}</td></tr>`).join('')}</tbody></table>`;
    document.querySelectorAll('#dailyTable [data-day]').forEach(row => row.onclick = () => { state.selectedDay = row.dataset.day; renderDaily(r); });
  }
  function tradeRows(r) { return Array.isArray(r?.trade_sample) ? r.trade_sample : []; }
  function renderTrades(r) {
    let rows = tradeRows(r); const q = state.tradeQuery.toLowerCase();
    rows = rows.filter(x => (state.side === 'all' || String(x.side || '').toUpperCase() === state.side) && (state.exit === 'all' || String(x.exit_reason || x.exit || '').toLowerCase() === state.exit) && (!q || JSON.stringify(x).toLowerCase().includes(q)));
    $('tradeMeta').textContent = `Embedded sample ${rows.length}/${tradeRows(r).length} · full ledger: ${int(r.trade_rows)} trades`;
    $('tradeTable').innerHTML = rows.length ? `<table><thead><tr><th>Side</th><th>Entry</th><th>Exit</th><th>Entry price</th><th>Exit price</th><th>PnL</th><th>Bars</th></tr></thead><tbody>${rows.map(x => { const p = n(x.pnl, n(x.pnl_usd)); return `<tr><td>${esc(x.side || '—')}</td><td>${esc(x.entry_time || '—')}</td><td>${esc(x.exit_time || '—')}</td><td>${fmt(x.entry_price ?? x.entry)}</td><td>${fmt(x.exit_price ?? x.exit)}</td><td class="${p >= 0 ? 'pos' : 'neg'}">${money(p)}</td><td>${int(x.bars_held ?? x.holding_bars)}</td></tr>`; }).join('')}</tbody></table>` : '<div class="muted">Không có trade phù hợp trong embedded sample.</div>';
  }
  function renderCompare() {
    const box = $('compareStrip'); if (!state.compare) { box.hidden = true; return; }
    box.hidden = false; box.innerHTML = sortedRuns().slice(0, 6).map(r => `<div class="compare-chip"><strong>${esc(r.run_id)}</strong><span>Total Profit ${money(totalProfit(r))} · PF ${fmt(metrics(r).profit_factor, 2)}</span><span>DD ${money(dd(r))} · ${int(trades(r))} trades</span></div>`).join('');
  }
  function init() {
    state.runs = Array.isArray(manifest.runs) ? manifest.runs : [];
    $('manifestStatus').textContent = `${state.runs.length} embedded runs`; $('manifestStatus').classList.add('ok'); $('dataStamp').textContent = manifest.generated_at ? `Snapshot ${String(manifest.generated_at).slice(0, 10)}` : 'Embedded snapshot';
    $('runSearch').oninput = e => { state.query = e.target.value; renderRuns(); }; $('sortRuns').onchange = e => { state.sort = e.target.value; renderRuns(); }; $('compareBtn').onclick = () => { state.compare = !state.compare; $('compareBtn').classList.toggle('active', state.compare); renderCompare(); }; $('sideFilter').onchange = e => { state.side = e.target.value; renderTrades(state.active); }; $('exitFilter').onchange = e => { state.exit = e.target.value; renderTrades(state.active); }; $('tradeSearch').oninput = e => { state.tradeQuery = e.target.value; renderTrades(state.active); }; $('themeBtn').onclick = () => document.body.classList.toggle('light');
    renderRuns(); if (state.runs.length) selectRun(state.runs[0].run_id);
  }
  window.addEventListener('DOMContentLoaded', init);
})();
