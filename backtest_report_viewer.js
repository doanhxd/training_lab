    const manifest = window.BACKTEST_REPORT_MANIFEST || { runs: [], count: 0 };

    const state = {
      report: null,
      trades: [],
      equity: [],
      monthly: [],
      daily: [],
      selectedDay: null,
      selectedMonth: null,
      loadedFiles: { report: null, trades: null, equity: null },
      activeRunId: null,
      statusMessage: '',
    };

    const $ = (id) => document.getElementById(id);

    const fmtMoney = (n) => Number.isFinite(n) ? `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';
    const fmtPct = (n) => Number.isFinite(n) ? `${(n * 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%` : '—';
    const fmtNum = (n, digits = 2) => Number.isFinite(n) ? n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
    const fmtInt = (n) => Number.isFinite(n) ? Math.round(n).toLocaleString('en-US') : '—';

    function setStatus(lines) {
      state.statusMessage = Array.isArray(lines) ? lines.join('\n') : String(lines);
    }

    function esc(text) {
      return String(text ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    }

    async function readFileText(file) {
      return await file.text();
    }

    function parseJson(text) {
      return JSON.parse(text);
    }

    function parseCsv(text) {
      const clean = text.replace(/^\uFEFF/, '').trim();
      if (!clean) return [];
      const lines = clean.split(/\r?\n/);
      const headers = splitCsvLine(lines[0]);
      return lines.slice(1).filter(Boolean).map((line) => {
        const cols = splitCsvLine(line);
        const row = {};
        headers.forEach((header, idx) => row[header] = cols[idx] ?? '');
        return row;
      });
    }

    function splitCsvLine(line) {
      const out = [];
      let cur = '';
      let quote = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (ch === '"') {
          if (quote && line[i + 1] === '"') {
            cur += '"';
            i++;
          } else {
            quote = !quote;
          }
        } else if (ch === ',' && !quote) {
          out.push(cur);
          cur = '';
        } else {
          cur += ch;
        }
      }
      out.push(cur);
      return out;
    }

    function normalizeTrades(rows) {
      return rows.map((row) => ({
        side: row.side,
        entry_time: row.entry_time,
        exit_time: row.exit_time,
        entry_price: Number(row.entry_price),
        exit_price: Number(row.exit_price),
        quantity: Number(row.quantity),
        pnl: Number(row.pnl),
        bars_held: Number(row.bars_held),
      })).filter((row) => row.entry_time && Number.isFinite(row.pnl));
    }

    function normalizeEquity(rows) {
      return rows.map((row, idx) => ({ index: idx, equity: Number(row.equity) })).filter((row) => Number.isFinite(row.equity));
    }

    function monthKey(iso) {
      return String(iso).slice(0, 7);
    }

    function buildMonthly(trades) {
      const map = new Map();
      trades.forEach((trade) => {
        const key = monthKey(trade.exit_time || trade.entry_time);
        if (!map.has(key)) {
          map.set(key, { month: key, trades: 0, wins: 0, losses: 0, pnl: 0, avgBars: 0, grossWin: 0, grossLoss: 0 });
        }
        const bucket = map.get(key);
        bucket.trades += 1;
        bucket.pnl += trade.pnl;
        bucket.avgBars += Number.isFinite(trade.bars_held) ? trade.bars_held : 0;
        if (trade.pnl >= 0) {
          bucket.wins += 1;
          bucket.grossWin += trade.pnl;
        } else {
          bucket.losses += 1;
          bucket.grossLoss += Math.abs(trade.pnl);
        }
      });
      let running = deriveInitialEquity();
      return Array.from(map.values()).sort((a, b) => a.month.localeCompare(b.month)).map((item) => {
        item.avgBars = item.trades ? item.avgBars / item.trades : 0;
        item.winRate = item.trades ? item.wins / item.trades : 0;
        item.profitFactor = item.grossLoss > 0 ? item.grossWin / item.grossLoss : (item.grossWin > 0 ? Infinity : 0);
        running += item.pnl;
        item.endEquity = running;
        return item;
      });
    }

    function deriveInitialEquity() {
      return Number(state.report?.backtest_config?.initial_equity ?? state.report?.run_config?.builtin?.initial_equity ?? 0);
    }

    function deriveNetPnl(metrics) {
      const initial = deriveInitialEquity();
      if (!Number.isFinite(initial) || !Number.isFinite(metrics?.net_return)) return NaN;
      return initial * metrics.net_return;
    }

    function deriveRiskRewardRatio() {
      const cfg = state.report?.backtest_config || {};
      const risk = Number(cfg.risk_usd);
      const reward = Number(cfg.reward_usd);
      if (!Number.isFinite(risk) || !Number.isFinite(reward) || risk <= 0) return '—';
      return `1:${fmtNum(reward / risk, 2)}`;
    }

    function getBacktestCoverage() {
      const coverage = state.report?.data_coverage || {};
      return {
        start: String(coverage.first_timestamp || '').replace('T', ' ').replace('+00:00', ' UTC'),
        end: String(coverage.last_timestamp || '').replace('T', ' ').replace('+00:00', ' UTC'),
      };
    }

    function formatCoverageDate(value) {
      if (!value) return '—';
      return value.replace(' UTC', '').slice(0, 16);
    }

    function compactRunTitle(report) {
      const cfg = report?.backtest_config || {};
      const name = String(report?.strategy_name || '').toLowerCase();
      const variant = String(cfg.variant || (name.includes('final') ? 'FINAL' : name.includes('neg') ? 'NEG' : name.includes('ori') ? 'ORI' : 'RSIQUI')).toUpperCase();
      const tf = String(cfg.timeframe || '').toUpperCase().replace('5M', 'M5') || (name.includes('m5') ? 'M5' : '');
      const volume = Number(cfg.volume_lots);
      const risk = Number(cfg.risk_usd);
      const reward = Number(cfg.reward_usd);
      const period = String(report?.data_coverage?.first_timestamp || '').slice(0, 7) || (name.includes('2026_07') ? '2026-07' : '');
      const parts = [`${variant}${tf ? ' ' + tf : ''}`];
      if (Number.isFinite(volume)) parts.push(`${fmtNum(volume, 2)} lot`);
      if (Number.isFinite(risk) && Number.isFinite(reward)) parts.push(`SL $${fmtNum(risk, 0)} / TP $${fmtNum(reward, 0)}`);
      if (period) parts.push(period);
      return parts.join(' · ');
    }

    function getDailyDrawdownInfo() {
      return state.report?.daily_drawdown || {};
    }

    function deriveMaxDrawdownUsd() {
      if (!state.equity.length) return NaN;
      let peak = -Infinity;
      let maxDrop = 0;
      for (const row of state.equity) {
        const value = Number(row.equity);
        if (!Number.isFinite(value)) continue;
        peak = Math.max(peak, value);
        maxDrop = Math.max(maxDrop, peak - value);
      }
      return maxDrop;
    }

    function shortPath(path) {
      const value = String(path || '');
      if (!value) return '—';
      const parts = value.split(/[\\/]+/).filter(Boolean);
      if (parts.length >= 3 && value.toLowerCase().includes('outputs')) {
        return parts.slice(-3).join(' / ');
      }
      return parts.slice(-2).join(' / ') || value;
    }

    function renderTable(el, rows) {
      if (!rows.length) {
        el.innerHTML = '<tr><td>—</td><td>Chưa có dữ liệu</td></tr>';
        return;
      }
      el.innerHTML = rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join('');
    }

    function renderHero() {
      const report = state.report;
      if (!report) {
        $('heroTitle').textContent = 'Chưa nạp report';
        $('heroPeriod').innerHTML = '<span class="period-label">History UTC</span><span>—</span>';
        $('heroSub').textContent = 'Hãy chọn report JSON để bắt đầu.';
        $('heroMeta').textContent = 'Report metadata sẽ hiện ở đây.';
        $('heroWarning').textContent = 'Lưu ý: nếu chỉ nạp report JSON, viewer vẫn hiện summary được nhưng chưa có monthly ledger / equity chart.';
        return;
      }
      $('heroTitle').textContent = compactRunTitle(report);
      $('heroTitle').title = report.strategy_name || 'Unnamed portfolio run';
      const coverage = getBacktestCoverage();
      $('heroPeriod').innerHTML = `<span class="period-label">Portfolio (UTC)</span><span>${esc(formatCoverageDate(coverage.start))} → ${esc(formatCoverageDate(coverage.end))}</span>`;
      $('heroSub').innerHTML = `Period <b>${esc(formatCoverageDate(coverage.start))}</b> → <b>${esc(formatCoverageDate(coverage.end))}</b> <span class="small">(UTC)</span> · Run data <b>${esc(report.dataset_id || '—')}</b> · Feature set <b>${esc(report.feature_set_id || '—')}</b> · Init balance <b>${esc(fmtMoney(deriveInitialEquity()))}</b> · R:R <b>${esc(deriveRiskRewardRatio())}</b>`;

      const companion = report.artifacts || {};
      $('heroMeta').innerHTML = [
        `<b>Artifacts</b><br>`,
        `report: <code title="${esc(state.loadedFiles.report || '')}">${esc(shortPath(state.loadedFiles.report || 'loaded report'))}</code><br>`,
        `trades: <code title="${esc(companion.trade_log_path || state.loadedFiles.trades || '')}">${esc(shortPath(companion.trade_log_path || state.loadedFiles.trades || 'not declared'))}</code><br>`,
        `equity: <code title="${esc(companion.equity_curve_path || state.loadedFiles.equity || '')}">${esc(shortPath(companion.equity_curve_path || state.loadedFiles.equity || 'not declared'))}</code>`
      ].join('');

      const validation = report.validation_results || {};
      const reasons = Array.isArray(validation.reasons) && validation.reasons.length ? validation.reasons.map(esc).join('; ') : 'none';
      const blackout = JSON.stringify(report.backtest_config?.blocked_entry_hours_gmt7 || []);
      $('heroWarning').innerHTML = `<b>Validation:</b> <span class="${validation.passed ? 'good' : 'bad'}">${validation.passed ? 'passed' : 'failed'}</span><br>` +
        `reasons: <b>${reasons}</b><br>` +
        `blocked_entry_hours_gmt7 in report config: <code>${esc(blackout)}</code><br>` +
        `Nếu trades/equity chưa nạp thì phần chart/ledger sẽ chưa đầy đủ.`;
    }

    function renderMetrics() {
      const metrics = state.report?.metrics_summary || {};
      const initial = deriveInitialEquity();
      const pnl = deriveNetPnl(metrics);
      const finalEquity = Number.isFinite(initial) && Number.isFinite(pnl) ? initial + pnl : NaN;
      $('finalEquity').textContent = fmtMoney(finalEquity);
      const totalProfit = state.monthly.length
        ? state.monthly.reduce((sum, month) => sum + (Number.isFinite(Number(month.pnl)) ? Number(month.pnl) : 0), 0)
        : NaN;
      $('totalProfit').textContent = fmtMoney(totalProfit);
      $('winRateValue').textContent = fmtPct(metrics.win_rate);
      $('winRateSub').textContent = `${fmtInt(metrics.total_trades * Number(metrics.win_rate || 0))} wins / ${fmtInt(metrics.total_trades)} trades`;
      $('maxDdValue').textContent = fmtPct(metrics.max_drawdown);
      const maxDdUsd = deriveMaxDrawdownUsd();
      $('drawdownSub').textContent = 'Overall Max DD';
      const dd = getDailyDrawdownInfo();
      $('maxDailyDdValue').textContent = fmtPct(Number(dd.max_daily_drawdown));
      $('maxDailyDdSub').textContent = Number.isFinite(Number(dd.max_daily_drawdown_usd))
        ? `${fmtMoney(Number(dd.max_daily_drawdown_usd))}${dd.worst_day ? ` · worst ${dd.worst_day}` : ''}`
        : (dd.worst_day ? `worst ${dd.worst_day}` : 'worst day');
      $('tradeCount').textContent = fmtInt(metrics.total_trades);
      $('pfSub').textContent = `profit factor ${fmtNum(metrics.profit_factor, 3)}`;
    }


    function renderContractAndSignals() {
      const cfg = state.report?.backtest_config || {};
      const val = state.report?.validation_results || {};
      const metrics = state.report?.metrics_summary || {};
      const signalCounts = state.report?.signal_counts || {};
      const dd = getDailyDrawdownInfo();
      renderTable($('contractTable'), [
        ['Initial equity', fmtMoney(Number(cfg.initial_equity))],
        ['Portfolio period (UTC)', `${esc(formatCoverageDate(getBacktestCoverage().start))} → ${esc(formatCoverageDate(getBacktestCoverage().end))}`],
        // ['', fmtNum(Number(cfg.volume_lots), 2)],
        ['Risk / Reward USD (Volume lots)', `${fmtMoney(Number(cfg.risk_usd))} / ${fmtMoney(Number(cfg.reward_usd))} (${fmtNum(Number(cfg.volume_lots), 2)} lot)`],
        ['R:R ratio', deriveRiskRewardRatio()],
        ['Daily DD latest ($ / %)', dd.latest_daily_drawdown != null ? `${fmtMoney(Number(dd.latest_daily_drawdown_usd))} / ${fmtPct(Number(dd.latest_daily_drawdown))} · ${esc(dd.latest_day || '—')}` : '—'],
        ['Max Daily DD ($ / %)', dd.max_daily_drawdown != null ? `${fmtMoney(Number(dd.max_daily_drawdown_usd))} / ${fmtPct(Number(dd.max_daily_drawdown))} · worst ${esc(dd.worst_day || '—')}` : '—'],
        ['Preset bias params', `RSI<${esc(cfg.rsi_entry_long)} / RSI>${esc(cfg.rsi_entry_short)} · gradient ${esc(cfg.gradient_periods)}`],
        ['Spread / Slippage / Commission', `${fmtNum(Number(cfg.max_spread), 3)} / ${fmtNum(Number(cfg.slippage_per_side), 3)} / ${fmtMoney(Number(cfg.commission_per_trade_usd))}`],
        // ['Trade side / Session', `${esc(cfg.trade_side || '—')} / ${esc(cfg.session_filter || '—')}`],
        // ['Validation', `<span class="${val.passed ? 'good' : 'bad'}">${val.passed ? 'PASSED' : 'FAILED'}</span>`],
        // ['Rejection reasons', esc((val.reasons || []).join('; ') || 'none')],
      ]);
      renderTable($('signalTable'), [
        ['Long signals', fmtInt(signalCounts.long_signal)],
        ['Short signals', fmtInt(signalCounts.short_signal)],
        ['Blocked spread', fmtInt(signalCounts.blocked_spread)],
        ['Entered long', fmtInt(signalCounts.entered_long)],
        ['Entered short', fmtInt(signalCounts.entered_short)],
        ['Win rate', fmtPct(metrics.win_rate)],
        ['Expectancy', fmtNum(metrics.expectancy, 4)],
        ['Exposure time', fmtPct(metrics.exposure_time)],
      ]);
    }

    function makeLineChart(points, color, formatter) {
      if (!points.length) return '<div class="empty">Không có dữ liệu chart.</div>';
      const width = 920, height = 320, pad = 28;
      const values = points.map(p => p.value);
      const min = Math.min(...values), max = Math.max(...values);
      const range = max - min || 1;
      const coords = points.map((p, i) => {
        const x = pad + (i * (width - pad * 2) / Math.max(points.length - 1, 1));
        const y = height - pad - ((p.value - min) / range) * (height - pad * 2);
        return { x, y, raw: p.value, label: p.label };
      });
      const path = coords.map((c, i) => `${i ? 'L' : 'M'} ${c.x.toFixed(2)} ${c.y.toFixed(2)}`).join(' ');
      const yTicks = [0, .25, .5, .75, 1].map(r => ({
        y: height - pad - r * (height - pad * 2),
        value: min + r * range,
      }));
      const xTicks = [coords[0], coords[Math.floor((coords.length - 1) / 2)], coords[coords.length - 1]].filter(Boolean);
      return `<div class="chart-shell"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="chart">
        <rect x="0" y="0" width="${width}" height="${height}" rx="12" fill="#101713"></rect>
        ${yTicks.map(t => `<g><line x1="${pad}" y1="${t.y}" x2="${width - pad}" y2="${t.y}" stroke="#223129" stroke-dasharray="3 6"></line><text x="${pad - 8}" y="${t.y + 4}" fill="#92a59a" font-size="11" text-anchor="end">${esc(formatter(t.value))}</text></g>`).join('')}
        ${xTicks.map(t => `<text x="${t.x}" y="${height - 8}" fill="#92a59a" font-size="11" text-anchor="middle">${esc(t.label)}</text>`).join('')}
        <path d="${path}" fill="none" stroke="${color}" stroke-width="3.5" stroke-linejoin="round" stroke-linecap="round"></path>
      </svg></div>`;
    }

    function renderEquity() {
      if (!state.equity.length) {
        $('equityWrap').className = 'empty';
        $('equityWrap').innerHTML = 'Nạp <code>equity_curve.csv</code> để xem equity curve.';
        return;
      }
      const points = state.equity.map((row, idx) => ({ value: row.equity, label: idx === 0 ? 'start' : idx === state.equity.length - 1 ? 'end' : '' })).filter((_, idx, arr) => idx === 0 || idx === arr.length - 1 || idx === Math.floor(arr.length / 2));
      const full = state.equity.map((row, idx) => ({ value: row.equity, label: idx === 0 ? 'start' : idx === state.equity.length - 1 ? 'end' : idx === Math.floor(state.equity.length / 2) ? 'mid' : '' }));
      $('equityWrap').className = '';
      $('equityWrap').innerHTML = makeLineChart(full, '#87e7a5', (v) => `$${Math.round(v).toLocaleString('en-US')}`);
    }

    function renderDailyLedger() {
      const selectedMonth = state.selectedMonth || null;
      const rows = (state.daily || []).filter((day) => !selectedMonth || String(day.day || '').startsWith(selectedMonth));
      $('dailyFilterLabel').textContent = selectedMonth ? `· lọc theo ${selectedMonth}` : '';
      if (!rows.length) {
        $('dailyChartWrap').className = 'empty';
        $('dailyChartWrap').innerHTML = 'Daily chart sẽ hiện khi manifest có daily ledger.';
        $('dailyTableWrap').className = 'empty';
        $('dailyTableWrap').innerHTML = 'Daily ledger sẽ hiện khi manifest có dữ liệu ngày.';
        $('dailyDetailWrap').className = 'empty';
        $('dailyDetailWrap').innerHTML = 'Chọn một ngày để xem chi tiết.';
        return;
      }

      const maxAbs = Math.max(...rows.map(d => Math.abs(Number(d.pnl) || 0))) || 1;
      const isWeekendDay = (day) => {
        const weekday = new Date(`${String(day)}T00:00:00Z`).getUTCDay();
        return weekday === 0 || weekday === 6;
      };
      const bars = rows.map((day) => {
        const pnl = Number(day.pnl) || 0;
        const h = Math.max(8, (Math.abs(pnl) / maxAbs) * 190);
        const color = pnl >= 0 ? '#b4f067' : '#ff8f8f';
        const label = String(day.day || '').slice(8, 10);
        const weekend = isWeekendDay(day.day);
        const active = day.day === state.selectedDay ? 'outline:2px solid #ffd27a;' : '';
        return `<button type="button" data-day="${esc(day.day)}" class="daily-bar" style="appearance:none;border:none;background:transparent;cursor:pointer;padding:0;display:grid;gap:8px;justify-items:center;align-content:end;min-height:230px;${active}">
          <div style="width:100%;display:grid;align-items:end;justify-items:center;min-height:200px;">
            <div title="${esc(day.day)} · ${esc(fmtMoney(pnl))}" style="width:100%;max-width:22px;height:${h}px;background:${color};border-radius:6px 6px 2px 2px;box-shadow:inset 0 0 0 1px rgba(255,255,255,.08);"></div>
          </div>
          <div class="${weekend ? 'weekend-day' : ''}" style="font-size:10px;color:#9db0a4;font-weight:800;transform:rotate(-35deg);">${esc(label)}</div>
        </button>`;
      }).join('');
      $('dailyChartWrap').className = '';
      $('dailyChartWrap').innerHTML = `<div class="chart-shell"><div style="display:grid;grid-template-columns:repeat(${rows.length}, minmax(12px, 1fr));gap:4px;align-items:end;">${bars}</div></div><div class="hint" style="margin-top:10px;">Click một ngày để xem chi tiết daily ledger.</div>`;
      document.querySelectorAll('.daily-bar').forEach((btn) => btn.addEventListener('click', () => {
        state.selectedDay = btn.dataset.day;
        renderDailyLedger();
      }));

      const tableRows = rows.map((day) => {
        const active = day.day === state.selectedDay ? ' class="daily-row active"' : ' class="daily-row"';
        const dayClass = isWeekendDay(day.day) ? 'weekend-day' : '';
        return `<tr${active} data-day="${esc(day.day)}">
          <td class="${dayClass}">${esc(day.day)}</td>
          <td>${fmtInt(Number(day.trades ?? day.trade_count))}</td>
          <td>${Number.isFinite(Number(day.profitFactor)) ? fmtNum(Number(day.profitFactor), 2) : '∞'}</td>
          <td>${fmtPct(Number(day.winRate))}</td>
          <td>${fmtMoney(Number(day.pnl))}</td>
          <td>${fmtMoney(Number(day.expectancy))}</td>
          <td>${fmtMoney(Number(day.daily_drawdown_usd))} / ${fmtPct(Number(day.daily_drawdown))}</td>
          <td>${fmtInt(Number(day.long))} / ${fmtInt(Number(day.short))}</td>
          <td>${fmtMoney(Number(day.end_equity))}</td>
        </tr>`;
      }).join('');
      $('dailyTableWrap').className = 'daily-table-wrap';
      $('dailyTableWrap').innerHTML = `<table><thead><tr><th>Day</th><th>Trades</th><th>PF</th><th>WR</th><th>Net P&L</th><th>Expectancy</th><th>Max DD $ / %</th><th>Long/Short</th><th>End equity</th></tr></thead><tbody>${tableRows}</tbody></table>`;
      document.querySelectorAll('.daily-row').forEach((row) => row.addEventListener('click', () => {
        state.selectedDay = row.dataset.day;
        renderDailyLedger();
      }));

      const selected = rows.find((day) => day.day === state.selectedDay) || rows[rows.length - 1];
      state.selectedDay = selected?.day || null;
      if (!selected) return;
      $('dailyDetailWrap').className = '';
      $('dailyDetailWrap').innerHTML = `<div class="daily-detail-grid">
        <div class="daily-detail-tile"><div class="label">Day</div><div class="value">${esc(selected.day)}</div></div>
        <div class="daily-detail-tile"><div class="label">Trades</div><div class="value">${fmtInt(Number(selected.trades ?? selected.trade_count))}</div></div>
        <div class="daily-detail-tile"><div class="label">PF</div><div class="value">${Number.isFinite(Number(selected.profitFactor)) ? fmtNum(Number(selected.profitFactor), 2) : '∞'}</div></div>
        <div class="daily-detail-tile"><div class="label">WR</div><div class="value">${fmtPct(Number(selected.winRate))}</div></div>
        <div class="daily-detail-tile"><div class="label">P&L</div><div class="value">${fmtMoney(Number(selected.pnl))}</div></div>
        <div class="daily-detail-tile"><div class="label">Max DD</div><div class="value">${fmtMoney(Number(selected.daily_drawdown_usd))} / ${fmtPct(Number(selected.daily_drawdown))}</div></div>
        <div class="daily-detail-tile"><div class="label">Long / Short</div><div class="value">${fmtInt(Number(selected.long))} / ${fmtInt(Number(selected.short))}</div></div>
        <div class="daily-detail-tile"><div class="label">End Equity</div><div class="value">${fmtMoney(Number(selected.end_equity))}</div></div>
      </div>`;
    }

    function renderMonthly() {
      if (!state.monthly.length) {
        $('monthlyChartWrap').className = 'empty';
        $('monthlyChartWrap').innerHTML = 'Nạp <code>trades.csv</code> để dựng monthly chart.';
        $('monthlyTableWrap').className = 'empty';
        $('monthlyTableWrap').innerHTML = 'Monthly ledger sẽ hiện khi có <code>trades.csv</code>.';
        $('monthDetailTable').innerHTML = '<tr><td>—</td><td>Chưa có dữ liệu tháng</td></tr>';
        $('tradeSampleWrap').className = 'empty';
        $('tradeSampleWrap').innerHTML = 'Trade sample sẽ hiện khi có <code>trades.csv</code>.';
        return;
      }

      state.selectedMonth = state.selectedMonth || state.monthly[state.monthly.length - 1]?.month;
      const maxAbs = Math.max(...state.monthly.map(m => Math.abs(m.pnl))) || 1;
      const bars = state.monthly.map((item, idx) => {
        const h = Math.max(8, (Math.abs(item.pnl) / maxAbs) * 260);
        const color = item.pnl >= 0 ? '#87e7a5' : '#ff9b9b';
        const active = item.month === state.selectedMonth ? ' active' : '';
        return `<button type="button" data-month="${esc(item.month)}" class="month-bar${active}" style="appearance:none;border:none;background:transparent;cursor:pointer;padding:0;display:grid;gap:8px;justify-items:center;align-content:end;min-height:300px;">
          <div style="width:100%;display:grid;align-items:end;justify-items:center;min-height:270px;">
            <div style="width:100%;max-width:30px;height:${h}px;background:${color};border-radius:8px 8px 3px 3px;box-shadow:inset 0 0 0 1px rgba(255,255,255,.08);"></div>
          </div>
          <div style="font-size:12px;color:#9db0a4;font-weight:700;">${esc(item.month)}</div>
        </button>`;
      }).join('');

      $('monthlyChartWrap').className = '';
      const monthlyMinWidth = Math.max(0, state.monthly.length * 66);
      $('monthlyChartWrap').innerHTML = `<div class="chart-shell monthly-chart-shell"><div style="min-width:${monthlyMinWidth}px;display:grid;grid-template-columns:repeat(${state.monthly.length}, minmax(54px, 1fr));gap:12px;align-items:end;">${bars}</div></div><div class="hint" style="margin-top:10px;">Click một tháng để xem chi tiết.</div>`;

      document.querySelectorAll('.month-bar').forEach((btn) => btn.addEventListener('click', () => {
        state.selectedMonth = btn.dataset.month;
        state.selectedDay = null;
        document.querySelectorAll('.month-bar').forEach((monthBtn) => monthBtn.classList.toggle('active', monthBtn === btn));
        renderSelectedMonth();
        renderDailyLedger();
      }));

      const rows = state.monthly.map((item) => `
        <tr>
          <td>${esc(item.month)}</td>
          <td>${fmtInt(item.trades)}</td>
          <td>${fmtPct(item.winRate)}</td>
          <td>${fmtMoney(item.pnl)}</td>
          <td>${Number.isFinite(item.profitFactor) ? fmtNum(item.profitFactor, 3) : '∞'}</td>
          <td>${fmtMoney(item.endEquity)}</td>
        </tr>`).join('');
      $('monthlyTableWrap').className = '';
      $('monthlyTableWrap').innerHTML = `<div style="overflow:auto;"><table><thead><tr><th>Month</th><th>Trades</th><th>Win rate</th><th>Net P&L</th><th>PF</th><th>End equity</th></tr></thead><tbody>${rows}</tbody></table></div>`;

      const sampleRows = state.trades.slice(0, 8).map((trade) => `
        <tr>
          <td>${esc(trade.side)}</td>
          <td>${esc(trade.entry_time)}</td>
          <td>${esc(trade.exit_time)}</td>
          <td>${fmtMoney(trade.pnl)}</td>
          <td>${fmtInt(trade.bars_held)}</td>
        </tr>`).join('');
      $('tradeSampleWrap').className = '';
      $('tradeSampleWrap').innerHTML = `<div style="overflow:auto;"><table><thead><tr><th>Side</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Bars</th></tr></thead><tbody>${sampleRows}</tbody></table></div>`;

      renderSelectedMonth();
    }

    function renderSelectedMonth() {
      const month = state.monthly.find((item) => item.month === state.selectedMonth) || state.monthly[state.monthly.length - 1];
      if (!month) {
        $('monthDetailTable').innerHTML = '<tr><td>—</td><td>Chưa có dữ liệu tháng</td></tr>';
        return;
      }
      renderTable($('monthDetailTable'), [
        ['Month', esc(month.month)],
        ['Trades (W/L)', `${fmtInt(month.trades)} (${fmtInt(month.wins)} / ${fmtInt(month.losses)})`],
        ['Win rate', fmtPct(month.winRate)],
        ['Net P&L', fmtMoney(month.pnl)],
        ['Profit factor', Number.isFinite(month.profitFactor) ? fmtNum(month.profitFactor, 3) : '∞'],
        ['AVG bars held', fmtNum(month.avgBars, 2)],
        ['DD', `${fmtMoney(month.drawdownUsd)} / ${fmtPct(month.drawdownPct)}`],
        ['Max DD', `${fmtMoney(month.maxDailyDrawdownUsd)}${month.worstDay ? ` (${esc(month.worstDay)})` : ''} / ${fmtPct(month.maxDailyDrawdownPct)}`],
        ['End equity', fmtMoney(month.endEquity)],
      ]);
    }

    function renderAll() {
      renderHero();
      renderMetrics();

      renderContractAndSignals();
      renderEquity();
      renderDailyLedger();
      renderMonthly();
    }

    function shortLabel(run) {
      const ts = (run.modified_at || '').replace('T', ' ').slice(0, 19);
      const base = run.strategy_name || 'unknown';
      const compact = base.length > 38 ? `${base.slice(0, 38)}…` : base;
      return `${ts} · ${run.run_id} · ${compact}`;
    }

    function populateRunSelect() {
      const select = $('runSelect');
      if (!select) return;
      const runs = Array.isArray(manifest.runs) ? manifest.runs : [];
      if (!runs.length) {
        select.innerHTML = '<option value="">-- không có run nào trong outputs --</option>';
        return;
      }
      select.innerHTML = ['<option value="">-- chọn một run --</option>'].concat(
        runs.map((run) => `<option value="${esc(run.run_id)}" title="${esc(run.label || run.run_id)}">${esc(shortLabel(run))}</option>`)
      ).join('');
    }

    function loadRunFromManifest(runId) {
      const run = (manifest.runs || []).find((item) => item.run_id === runId);
      if (!run) {
        setStatus(`Không tìm thấy run ${runId} trong manifest.`);
        return;
      }
      state.activeRunId = run.run_id;
      state.report = {
        strategy_name: run.strategy_name,
        dataset_id: run.dataset_id,
        feature_set_id: run.feature_set_id,
        config_hash: 'manifest',
        backtest_config: run.backtest_config || {},
        data_coverage: run.data_coverage || {},
        metrics_summary: run.metrics_summary || {},
        validation_results: run.validation_results || {},
        signal_counts: run.signal_counts || {},
        daily_drawdown: run.daily_drawdown || {},
        artifacts: {
          trade_log_path: run.trades_file,
          equity_curve_path: run.equity_file,
          report_path: run.report_file,
        },
      };
      state.trades = Array.isArray(run.trade_sample) ? run.trade_sample : [];
      state.equity = Array.isArray(run.equity_sample) ? run.equity_sample : [];
      state.monthly = Array.isArray(run.monthly) ? run.monthly : [];
      state.daily = Array.isArray(run.daily_drawdown?.daily_rows) ? run.daily_drawdown.daily_rows : [];
      state.selectedDay = state.daily[state.daily.length - 1]?.day || null;
      state.selectedMonth = state.monthly[state.monthly.length - 1]?.month || null;
      state.loadedFiles = {
        report: run.report_file || run.run_id,
        trades: run.trades_file || null,
        equity: run.equity_file || null,
      };
      setStatus([
        `Loaded from manifest: ${run.run_id}`,
        `strategy: ${run.strategy_name || 'unknown'}`,
        `modified_at: ${run.modified_at || 'unknown'}`,
        `trade_rows: ${run.trade_rows ?? '—'} · equity_rows: ${run.equity_rows ?? '—'}`,
        'Sorted newest → oldest from outputs folder.'
      ]);

    }

    function resetViewer() {
      state.report = null;
      state.trades = [];
      state.equity = [];
      state.monthly = [];
      state.daily = [];
      state.selectedDay = null;
      state.selectedMonth = null;
      state.loadedFiles = { report: null, trades: null, equity: null };
      state.activeRunId = null;
      state.statusMessage = '';
      if ($('runSelect')) $('runSelect').value = '';
      renderAll();
    }

    if ($('runSelect')) {
      $('runSelect').addEventListener('change', (e) => {
        if (!e.target.value) {
          resetViewer();
          return;
        }
        loadRunFromManifest(e.target.value);
        renderAll();
      });
    }

    populateRunSelect();
    const latest = (manifest.runs || [])[0];
    if (latest && $('runSelect')) {
      $('runSelect').value = latest.run_id;
      loadRunFromManifest(latest.run_id);
      renderAll();
    } else {
      renderAll();
    }
