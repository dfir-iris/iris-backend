(function ($) {
  const apiBase = '/custom-dashboards/api/dashboards';
  const DEFAULT_RANGE_DAYS = 30;
  const CHART_COLORS = ['#177dff', '#5e72e4', '#fdaf4b', '#f3545d', '#2dce89', '#11cdef', '#ff6b6b', '#ffa21d', '#9f7aea', '#45aaf2'];
  const DASHBOARD_AUTO_REFRESH_INTERVAL_MS = 60000;
  const DASHBOARD_STATE_STORAGE_KEY = 'iris.customDashboards.state';

  let dashboardsCache = [];
  let currentDashboardId = null;
  let chartInstances = {};
  let currentQuickRangeKey = null;
  let dashboardAutoRefreshTimer = null;
  let isFetchingDashboardData = false;
  let dashboardState = loadDashboardState();
  if (!dashboardState || typeof dashboardState !== 'object') {
    dashboardState = {};
  }
  const canShareDashboards = ($('#dashboardCanShare').val() || '0') === '1';

  function buildApiUrl(path, query) {
    const params = new URLSearchParams();
    const caseId = $('#currentCaseId').val();

    if (query) {
      Object.keys(query).forEach((key) => {
        const value = query[key];
        if (value !== null && value !== undefined && value !== '') {
          params.append(key, value);
        }
      });
    }

    if (caseId) {
      params.append('cid', caseId);
    }

    const qs = params.toString();
    if (!qs) {
      return path;
    }
    return `${path}?${qs}`;
  }

  function formatDateForInput(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
      return '';
    }
    const pad = (value) => String(value).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function parseInputDate(value) {
    if (!value) {
      return null;
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return null;
    }
    return parsed;
  }

  function loadDashboardState() {
    if (typeof window === 'undefined' || !window.localStorage) {
      return {};
    }
    try {
      const raw = window.localStorage.getItem(DASHBOARD_STATE_STORAGE_KEY);
      if (!raw) {
        return {};
      }
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (error) {
      console.warn('Unable to load dashboard preferences', error);
      return {};
    }
  }

  function persistDashboardState(state) {
    if (typeof window === 'undefined' || !window.localStorage) {
      return;
    }
    try {
      window.localStorage.setItem(DASHBOARD_STATE_STORAGE_KEY, JSON.stringify(state || {}));
    } catch (error) {
      console.warn('Unable to persist dashboard preferences', error);
    }
  }

  function updateDashboardState(patch) {
    if (!patch || typeof patch !== 'object') {
      return;
    }
    dashboardState = Object.assign({}, dashboardState || {}, patch);
    persistDashboardState(dashboardState);
  }

  function toNumericValue(value) {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    if (typeof value === 'number') {
      return Number.isNaN(value) ? null : value;
    }
    if (typeof value === 'string') {
      const parsed = Number(value);
      return Number.isNaN(parsed) ? null : parsed;
    }
    return null;
  }

  function formatNumberValue(value) {
    const numeric = toNumericValue(value);
    if (numeric === null) {
      if (value === null || value === undefined || value === '') {
        return '--';
      }
      return String(value);
    }
    if (Number.isInteger(numeric)) {
      return String(Math.trunc(numeric));
    }
    return numeric.toFixed(2).replace(/\.00$/, '').replace(/0+$/, '').replace(/\.$/, '');
  }

  function formatPercentageValue(value) {
    const numeric = toNumericValue(value);
    if (numeric === null) {
      return '--';
    }
    const formatted = Number.isInteger(numeric)
      ? String(Math.trunc(numeric))
      : numeric.toFixed(2).replace(/\.00$/, '').replace(/0+$/, '').replace(/\.$/, '');
    return `${formatted}%`;
  }

  function normalizeDisplayMode(value, fallback = 'number') {
    if (!value || typeof value !== 'string') {
      return fallback;
    }
    const normalized = value.trim().toLowerCase();
    if (normalized === 'percentage' || normalized === 'percent') {
      return 'percentage';
    }
    if (normalized === 'number_percentage'
      || normalized === 'number and percentage'
      || normalized === 'number-and-percentage'
      || normalized === 'both'
      || normalized === 'numberpercentage') {
      return 'number_percentage';
    }
    if (normalized === 'number') {
      return 'number';
    }
    return fallback;
  }

  function formatHeaderLabel(value) {
    if (value === null || value === undefined) {
      return 'Value';
    }
    const text = String(value).replace(/_/g, ' ').trim();
    if (!text) {
      return 'Value';
    }
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function setDefaultTimeframe() {
    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - DEFAULT_RANGE_DAYS);
    $('#dashboardStart').val(formatDateForInput(start));
    $('#dashboardEnd').val(formatDateForInput(end));
    currentQuickRangeKey = null;
    updateQuickRangeButtons();
    updateRangeSummary(start, end);
  }

  function updateQuickRangeButtons() {
    $('.dashboard-quick-range').removeClass('active');
    if (!currentQuickRangeKey) {
      return;
    }
    $(`.dashboard-quick-range[data-range-key="${currentQuickRangeKey}"]`).addClass('active');
  }

  function updateRangeSummary(start, end) {
    const summaryElement = $('#dashboardViewerRange');
    if (!start || !end) {
      summaryElement.text('--');
      return;
    }
    const startText = start.toLocaleString();
    const endText = end.toLocaleString();
    summaryElement.text(`${startText} → ${endText}`);
  }

  function showFilterError(message) {
    const container = $('#dashboardFilterError');
    container.text(message || 'Invalid timeframe');
    container.removeClass('d-none');
  }

  function clearFilterError() {
    $('#dashboardFilterError').addClass('d-none').text('');
  }

  function getSelectedTimeframe({ allowPartial } = { allowPartial: false }) {
    const startInput = $('#dashboardStart').val();
    const endInput = $('#dashboardEnd').val();

    const startDate = parseInputDate(startInput);
    const endDate = parseInputDate(endInput);

    if (!startDate && !endDate) {
      if (allowPartial) {
        return { start: null, end: null };
      }
      showFilterError('Please choose a valid timeframe.');
      return null;
    }

    if (!endDate && startDate) {
      if (allowPartial) {
        return { start: startDate, end: null };
      }
      showFilterError('End date is required.');
      return null;
    }

    if (!startDate && endDate) {
      if (allowPartial) {
        return { start: null, end: endDate };
      }
      showFilterError('Start date is required.');
      return null;
    }

    if (startDate && endDate && startDate > endDate) {
      showFilterError('Start date must be before end date.');
      return null;
    }

    return { start: startDate, end: endDate };
  }

  function destroyCharts() {
    Object.keys(chartInstances).forEach((key) => {
      try {
        chartInstances[key].destroy();
      } catch (err) {
        console.warn('Failed to destroy chart', key, err);
      }
    });
    chartInstances = {};
  }

  function setViewerLoading(isLoading) {
    $('#dashboardViewerLoading').toggleClass('d-none', !isLoading);
    if (isLoading) {
      $('#dashboardViewerEmptyState').addClass('d-none');
      $('#dashboardWidgets').addClass('d-none');
    }
    $('#dashboardViewerStatus').text(isLoading ? 'Loading...' : 'Idle');
  }

  function setViewerStatus(message, isError) {
    const status = $('#dashboardViewerStatus');
    status.text(message);
    if (isError) {
      status.addClass('text-danger');
    } else {
      status.removeClass('text-danger');
    }
  }

  function updateDashboardActionsState() {
    const hasSelection = Boolean(currentDashboardId);
    $('#editDashboardBtn').prop('disabled', !hasSelection);
    $('#removeDashboardBtn').prop('disabled', !hasSelection);
  }

  function clearDashboardViewer() {
    destroyCharts();
    currentDashboardId = null;
    updateDashboardState({ lastDashboardId: null });
    $('#dashboardSelector').val('');
    $('#selectedDashboardName').text('Select a dashboard');
    $('#selectedDashboardSharedBadge').addClass('d-none');
    $('#dashboardViewerRange').text('--');
    updateRangeSummary(null, null);
    $('#dashboardWidgets').empty().addClass('d-none');
    $('#dashboardViewerEmptyState').removeClass('d-none').text('Choose a dashboard to display its widgets.');
    setViewerStatus('Idle', false);
    updateDashboardActionsState();
  }

  function normalizeColorList(input) {
    if (!input) {
      return null;
    }
    let entries = [];
    if (Array.isArray(input)) {
      entries = input;
    } else if (typeof input === 'string') {
      entries = input.split(',');
    }
    const trimmed = entries.map((entry) => typeof entry === 'string' ? entry.trim() : null).filter(Boolean);
    return trimmed.length ? trimmed : null;
  }

  function lightenColor(color, alpha) {
    if (typeof color !== 'string' || !color) {
      return color;
    }
    if (color.startsWith('rgba') || color.startsWith('rgb')) {
      return color;
    }
    if (!color.startsWith('#')) {
      return color;
    }
    const hex = color.replace('#', '');
    if (hex.length !== 6) {
      return color;
    }
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    const clampedAlpha = Math.max(0, Math.min(1, alpha));
    return `rgba(${r}, ${g}, ${b}, ${clampedAlpha})`;
  }

  function assignDatasetColors(datasets, chartType, widgetOptions = {}) {
    const explicitColors = normalizeColorList(widgetOptions.colors);
    const primaryColor = typeof widgetOptions.color === 'string' ? widgetOptions.color.trim() : null;

    datasets.forEach((dataset, index) => {
      const datasetColors = normalizeColorList(dataset.colors) || explicitColors;
      const datasetPrimary = typeof dataset.color === 'string' ? dataset.color.trim() : null;
      const baseColor = datasetPrimary || primaryColor || (datasetColors ? datasetColors[index % datasetColors.length] : CHART_COLORS[index % CHART_COLORS.length]);
      if (chartType === 'line') {
        dataset.borderColor = dataset.borderColor || baseColor;
        if (widgetOptions.fill === true) {
          dataset.backgroundColor = dataset.backgroundColor || lightenColor(baseColor, 0.15);
          dataset.fill = true;
        } else {
          dataset.backgroundColor = dataset.backgroundColor || baseColor;
          dataset.fill = false;
        }
        dataset.tension = 0.2;
      } else if (chartType === 'pie') {
        const valueColors = datasetColors || explicitColors;
        if (valueColors && valueColors.length) {
          dataset.backgroundColor = dataset.data.map((_, dataIndex) => valueColors[dataIndex % valueColors.length]);
        } else if (!Array.isArray(dataset.backgroundColor)) {
          dataset.backgroundColor = dataset.data.map((_, dataIndex) => CHART_COLORS[dataIndex % CHART_COLORS.length]);
        }
      } else {
        const valueColors = datasetColors;
        if (valueColors && valueColors.length) {
          const colorArray = dataset.data.map((_, dataIndex) => valueColors[dataIndex % valueColors.length]);
          dataset.backgroundColor = dataset.backgroundColor || colorArray;
          dataset.borderColor = dataset.borderColor || colorArray;
        } else {
          dataset.backgroundColor = dataset.backgroundColor || baseColor;
          dataset.borderColor = dataset.borderColor || baseColor;
        }
        dataset.borderWidth = dataset.borderWidth || 1;
      }
      dataset.hoverBackgroundColor = dataset.hoverBackgroundColor || dataset.backgroundColor;
      dataset.hoverBorderColor = dataset.hoverBorderColor || dataset.borderColor;
    });
  }

  function renderNumericWidget(widget, container) {
    const value = widget.data && widget.data.value !== undefined ? widget.data.value : null;
    const label = widget.data && widget.data.label ? widget.data.label : '';
    const widgetOptions = (widget.definition && widget.definition.options) || widget.options || {};
    let displayValue = '--';

    if (widget.chart_type === 'percentage') {
      displayValue = (widget.data && widget.data.formatted_value)
        || (widget.data && widget.data.formatted_percentage)
        || formatPercentageValue(toNumericValue(value));
    } else {
      displayValue = (widget.data && widget.data.formatted_value)
        || formatNumberValue(value);
    }

    const valueElement = $('<div class="display-3 font-weight-bold mb-1"></div>').text(displayValue !== null && displayValue !== undefined ? displayValue : '--');
    if (typeof widgetOptions.color === 'string') {
      valueElement.css('color', widgetOptions.color);
    }
    const labelElement = $('<small class="text-muted"></small>').text(label);

    container.append(valueElement);
    container.append(labelElement);

    if (widget.data && Array.isArray(widget.data.rows) && widget.data.rows.length) {
      const details = $('<pre class="mt-3 small text-muted"></pre>').text(JSON.stringify(widget.data.rows, null, 2));
      container.append(details);
    }
  }

  function renderTableWidget(widget, container) {
    const data = widget.data || {};
  const rows = Array.isArray(data.rows) ? data.rows : [];
  const groupHeaders = Array.isArray(data.group_headers) ? data.group_headers : [];
  const valueHeaders = Array.isArray(data.value_headers) ? data.value_headers : [];
  const valueKeys = Array.isArray(data.value_keys) ? data.value_keys : [];
    const totals = Array.isArray(data.totals) ? data.totals : [];
    const totalLabel = data.total_label || 'Total';
    const widgetOptions = (widget.definition && widget.definition.options) || widget.options || {};
    const displayMode = normalizeDisplayMode(widgetOptions.display_mode || widgetOptions.display || data.display_mode);

    if (!rows.length) {
      container.append('<div class="text-muted">No data available.</div>');
      return;
    }

    const tableWrapper = $('<div class="custom-dashboard-table table-responsive"></div>');
    const table = $('<table class="table table-sm table-striped mb-0"></table>');
    const thead = $('<thead></thead>');
    const headerRow = $('<tr></tr>');

    if (groupHeaders.length) {
      groupHeaders.forEach((header) => {
        headerRow.append($('<th></th>').text(header));
      });
    }

    const effectiveHeaders = (valueHeaders.length ? valueHeaders : valueKeys).slice();
    if (effectiveHeaders.length) {
      effectiveHeaders.forEach((header, index) => {
        const fallbackLabel = header || valueKeys[index] || `Value ${index + 1}`;
        const headerLabel = valueHeaders.length ? header : formatHeaderLabel(fallbackLabel);
        if (displayMode === 'number_percentage') {
          headerRow.append($('<th class="text-right"></th>').text(`${headerLabel} (Value)`));
          headerRow.append($('<th class="text-right"></th>').text(`${headerLabel} (%)`));
        } else if (displayMode === 'percentage') {
          headerRow.append($('<th class="text-right"></th>').text(`${headerLabel} (%)`));
        } else {
          headerRow.append($('<th class="text-right"></th>').text(headerLabel));
        }
      });
    } else if (!groupHeaders.length) {
      headerRow.append($('<th></th>').text('Value'));
    }

    thead.append(headerRow);
    table.append(thead);

    const tbody = $('<tbody></tbody>');
    rows.forEach((row) => {
      const tr = $('<tr></tr>');
      const rawGroupValues = Array.isArray(row.group_values) ? row.group_values : [];
      const formattedGroups = Array.isArray(row.formatted_group_values) ? row.formatted_group_values : rawGroupValues;
      const valueCells = Array.isArray(row.value_cells) ? row.value_cells : [];

      if (groupHeaders.length) {
        groupHeaders.forEach((_, index) => {
          const formattedValue = formattedGroups[index];
          const rawValue = rawGroupValues[index];
          const candidate = formattedValue !== undefined ? formattedValue : rawValue;
          const textValue = candidate === null || candidate === undefined || candidate === '' ? 'N/A' : candidate;
          tr.append($('<td></td>').text(textValue));
        });
      }

      if (effectiveHeaders.length) {
        effectiveHeaders.forEach((header, index) => {
          const key = valueKeys[index];
          const matchedCell = key ? valueCells.find((entry) => entry && entry.key === key) : valueCells[index];
          const cell = matchedCell || valueCells[index] || null;
          const numberText = cell && cell.formatted_value !== undefined ? cell.formatted_value : formatNumberValue(cell ? cell.value : null);
          const percentageText = cell && cell.formatted_percentage !== undefined ? cell.formatted_percentage : formatPercentageValue(cell ? cell.percentage : null);
          if (displayMode === 'number_percentage') {
            tr.append($('<td class="text-right"></td>').text(numberText));
            tr.append($('<td class="text-right"></td>').text(percentageText));
          } else if (displayMode === 'percentage') {
            tr.append($('<td class="text-right"></td>').text(percentageText));
          } else {
            tr.append($('<td class="text-right"></td>').text(numberText));
          }
        });
      } else {
        const firstCell = valueCells[0];
        const fallbackText = firstCell && firstCell.formatted_value !== undefined ? firstCell.formatted_value : formatNumberValue(firstCell ? firstCell.value : null);
        tr.append($('<td class="text-right"></td>').text(fallbackText));
      }

      tbody.append(tr);
    });
    table.append(tbody);

    const hasTotals = totals.some((entry) => entry && entry.value !== null && entry.value !== undefined);
    if (hasTotals && effectiveHeaders.length) {
      const tfoot = $('<tfoot></tfoot>');
      const totalRow = $('<tr class="font-weight-bold"></tr>');
      if (groupHeaders.length) {
        totalRow.append($('<td></td>').attr('colspan', groupHeaders.length).text(totalLabel));
      }
      let firstValueCell = !groupHeaders.length;
      effectiveHeaders.forEach((header, index) => {
        const key = valueKeys[index];
        const entry = key ? totals.find((item) => item && item.key === key) : totals[index];
        const numberText = entry && entry.formatted_value !== undefined ? entry.formatted_value : formatNumberValue(entry ? entry.value : null);
        const percentageText = entry && entry.formatted_percentage !== undefined ? entry.formatted_percentage : formatPercentageValue(entry ? entry.percentage : null);
        if (displayMode === 'number_percentage') {
          const numberCellText = firstValueCell ? `${totalLabel}: ${numberText}` : numberText;
          totalRow.append($('<td class="text-right"></td>').text(numberCellText));
          totalRow.append($('<td class="text-right"></td>').text(percentageText));
          firstValueCell = false;
        } else if (displayMode === 'percentage') {
          const cellText = firstValueCell ? `${totalLabel}: ${percentageText}` : percentageText;
          totalRow.append($('<td class="text-right"></td>').text(cellText));
          firstValueCell = false;
        } else {
          const cellText = firstValueCell ? `${totalLabel}: ${numberText}` : numberText;
          totalRow.append($('<td class="text-right"></td>').text(cellText));
          firstValueCell = false;
        }
      });
      tfoot.append(totalRow);
      table.append(tfoot);
    }

    tableWrapper.append(table);
    container.append(tableWrapper);
  }

  function renderChartWidget(widget, container) {
    const chartData = widget.data;
    if (!chartData || !Array.isArray(chartData.labels)) {
      container.append('<div class="text-muted">No data available.</div>');
      return;
    }

    const widgetOptions = widget.definition && widget.definition.options ? widget.definition.options : {};
    const resolvedDisplayMode = normalizeDisplayMode(widgetOptions.display_mode || widgetOptions.display || chartData.display_mode);
    const canvasWrapper = $('<div class="custom-dashboard-chart"></div>');
    const canvas = $('<canvas></canvas>');
    canvasWrapper.append(canvas);
    container.append(canvasWrapper);

    const datasets = chartData.datasets ? chartData.datasets.map((dataset) => Object.assign({}, dataset)) : [];
    assignDatasetColors(datasets, widget.chart_type, widgetOptions);

    const maxLabelLength = Number(widgetOptions.label_max_length) > 0 ? Number(widgetOptions.label_max_length) : 32;
    const trimLabel = (label) => {
      if (typeof label !== 'string') {
        return label;
      }
      if (label.length <= maxLabelLength) {
        return label;
      }
      return `${label.slice(0, maxLabelLength - 1)}...`;
    };

    datasets.forEach((dataset) => {
      const rawData = Array.isArray(dataset.data) ? dataset.data.slice() : [];
      const numericValues = rawData.map((value) => toNumericValue(value));
      const explicitTotal = dataset.total !== undefined ? toNumericValue(dataset.total) : null;
      let computedTotal = explicitTotal;
      if (computedTotal === null) {
        computedTotal = numericValues.reduce((accumulator, value) => {
          if (value === null) {
            return accumulator;
          }
          if (accumulator === null) {
            return value;
          }
          return accumulator + value;
        }, null);
      }
      dataset._irisRawData = rawData;
      dataset._irisNumericValues = numericValues;
      dataset._irisTotal = computedTotal;
      dataset._irisPercentageValues = numericValues.map((value) => {
        if (value === null || computedTotal === null) {
          return null;
        }
        if (computedTotal === 0) {
          return 0;
        }
        return (value / computedTotal) * 100;
      });
    });

    const chartConfig = {
      type: widget.chart_type,
      data: {
        labels: chartData.labels,
        datasets: datasets
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        legend: {
          display: datasets.length > 1 || widget.chart_type === 'pie'
        },
        layout: {
          padding: {
            top: 8,
            right: 16,
            bottom: 16,
            left: 8
          }
        },
        tooltips: {
          callbacks: {
            title: (tooltipItems, data) => {
              if (!tooltipItems.length) {
                return '';
              }
              const index = tooltipItems[0].index;
              return data.labels && data.labels[index] ? data.labels[index] : '';
            },
            label: (tooltipItem, data) => {
              if (!data || !Array.isArray(data.datasets)) {
                return '';
              }
              const dataset = data.datasets[tooltipItem.datasetIndex] || {};
              const datasetLabel = dataset.label ? `${dataset.label}: ` : '';
              const rawValues = Array.isArray(dataset._irisRawData) ? dataset._irisRawData : Array.isArray(dataset.data) ? dataset.data : [];
              const numericValues = Array.isArray(dataset._irisNumericValues) ? dataset._irisNumericValues : rawValues.map((value) => toNumericValue(value));
              const rawValue = rawValues[tooltipItem.index];
              const numericValue = numericValues[tooltipItem.index];
              const numberText = formatNumberValue(rawValue);
              const valueKey = dataset.value_key || dataset.valueKey;
              const percentageValues = Array.isArray(dataset._irisPercentageValues) ? dataset._irisPercentageValues : [];
              const percentageRaw = percentageValues[tooltipItem.index];
              const hasPercentage = percentageRaw !== null && percentageRaw !== undefined;
              const percentageText = hasPercentage ? formatPercentageValue(percentageRaw) : null;

              if (resolvedDisplayMode === 'percentage') {
                return `${datasetLabel}${hasPercentage ? percentageText : '--'}`;
              }

              if (resolvedDisplayMode === 'number_percentage') {
                if (hasPercentage && percentageText !== null) {
                  return `${datasetLabel}${numberText} (${percentageText})`;
                }
                return `${datasetLabel}${numberText}`;
              }
              return `${datasetLabel}${numberText}`;
            }
          }
        },
        scales: widget.chart_type === 'pie' ? {} : {
          xAxes: [{
            ticks: {
              autoSkip: true,
              maxRotation: 0,
              minRotation: 0,
              callback: (value) => trimLabel(value)
            }
          }],
          yAxes: [{
            ticks: {
              beginAtZero: true
            }
          }]
        }
      }
    };

    if (widgetOptions.legend_position) {
      chartConfig.options.legend.position = widgetOptions.legend_position;
    }

    const context = canvas.get(0).getContext('2d');
    chartInstances[widget.widget_id] = new Chart(context, chartConfig);
  }

  function renderWidget(widget) {
    const chartType = (widget.chart_type || '').toLowerCase();
    let columnClass = 'col-md-6';
    if (chartType === 'table') {
      columnClass = 'col-12';
    } else if (chartType === 'number' || chartType === 'percentage') {
      columnClass = 'col-md-4';
    }
    const col = $('<div></div>').addClass(`${columnClass} mb-4`);
    const card = $('<div class="card h-100"></div>');
    const header = $('<div class="card-header py-3"></div>');
    const title = $('<h6 class="mb-0"></h6>').text(widget.name || 'Widget');
    header.append(title);
    const body = $('<div class="card-body"></div>');

    if (widget.error) {
      body.append($('<div class="alert alert-warning mb-0"></div>').text(widget.error));
    } else if (!widget.data) {
      body.append('<div class="text-muted">No data available.</div>');
    } else if (chartType === 'table') {
      renderTableWidget(widget, body);
    } else if (chartType === 'number' || chartType === 'percentage') {
      renderNumericWidget(widget, body);
    } else {
      renderChartWidget(widget, body);
    }

    card.append(header);
    card.append(body);
    col.append(card);
    return col;
  }

  function renderDashboardDetail(payload) {
    destroyCharts();

    const container = $('#dashboardWidgets');
    container.empty();

    const widgets = payload.widgets || [];
    if (!widgets.length) {
      $('#dashboardViewerEmptyState').removeClass('d-none').text('No widgets configured for this dashboard.');
      container.addClass('d-none');
      return;
    }

    widgets.forEach((widget) => {
      container.append(renderWidget(widget));
    });

    $('#dashboardViewerEmptyState').addClass('d-none');
    container.removeClass('d-none');
  }

  function refreshSelectedDashboard(options = {}) {
    if (!currentDashboardId) {
      return;
    }

    if (options.skipIfLoading && isFetchingDashboardData) {
      return;
    }

    const timeframe = getSelectedTimeframe();
    if (!timeframe) {
      return;
    }

    clearFilterError();
    const isSilent = options.silent === true;
    if (!isSilent) {
      setViewerLoading(true);
    } else {
      setViewerStatus('Refreshing...', false);
    }

    const params = {
      start: timeframe.start ? timeframe.start.toISOString() : undefined,
      end: timeframe.end ? timeframe.end.toISOString() : undefined
    };

    isFetchingDashboardData = true;

    $.getJSON(buildApiUrl(`${apiBase}/${currentDashboardId}/data`, params))
      .done((response) => {
        if (!response || response.status !== 'success') {
          if (!isSilent) {
            setViewerLoading(false);
          }
          const errorMessage = response && response.message ? response.message : 'Unable to load dashboard data.';
          setViewerStatus(errorMessage, true);
          $('#dashboardViewerEmptyState').removeClass('d-none').text(errorMessage);
          $('#dashboardWidgets').addClass('d-none');
          if (response && response.message) {
            notify_error(response.message);
          }
          return;
        }

        const data = response.data || {};
        const timeframe = data.timeframe || {};
        const startDate = timeframe.start ? new Date(timeframe.start) : null;
        const endDate = timeframe.end ? new Date(timeframe.end) : null;
        updateRangeSummary(startDate, endDate);
        renderDashboardDetail(data);
        if (!isSilent) {
          setViewerLoading(false);
        }
        const timestamp = new Date().toLocaleTimeString();
        const reason = options.reason === 'auto-refresh' ? 'Auto-refreshed' : 'Loaded';
        setViewerStatus(`${reason} at ${timestamp}`, false);
      })
      .fail((jqXHR) => {
        if (!isSilent) {
          setViewerLoading(false);
        }
        const timestamp = new Date().toLocaleTimeString();
        setViewerStatus(`Error at ${timestamp}`, true);
        if (jqXHR.responseJSON && jqXHR.responseJSON.message) {
          notify_error(jqXHR.responseJSON.message);
        } else {
          ajax_notify_error(jqXHR, `${apiBase}/${currentDashboardId}/data`);
        }
        $('#dashboardViewerEmptyState').removeClass('d-none').text('Failed to load data for the selected dashboard.');
      })
      .always(() => {
        isFetchingDashboardData = false;
      });
  }

  function clearAutoRefreshTimer() {
    if (dashboardAutoRefreshTimer) {
      clearInterval(dashboardAutoRefreshTimer);
      dashboardAutoRefreshTimer = null;
    }
  }

  function setAutoRefresh(enabled, options = {}) {
    const checkbox = $('#dashboardAutoRefresh');
    if (checkbox.length) {
      checkbox.prop('checked', !!enabled);
    }

    clearAutoRefreshTimer();

    if (enabled) {
      const intervalCandidate = options.intervalMs !== undefined ? Number(options.intervalMs) : DASHBOARD_AUTO_REFRESH_INTERVAL_MS;
      const intervalMs = Number.isFinite(intervalCandidate) && intervalCandidate > 0 ? intervalCandidate : DASHBOARD_AUTO_REFRESH_INTERVAL_MS;
      dashboardAutoRefreshTimer = setInterval(() => {
        refreshSelectedDashboard({ skipIfLoading: true, silent: true, reason: 'auto-refresh' });
      }, intervalMs);
    }

    updateDashboardState({ autoRefresh: !!enabled });

    if (enabled && options.skipImmediate !== true) {
      refreshSelectedDashboard({ skipIfLoading: true, silent: options.silent === true, reason: 'auto-refresh' });
    }
  }

  function selectDashboard(dashboardId) {
    const numericId = Number(dashboardId);
    const dashboard = dashboardsCache.find((item) => Number(item.id) === numericId);
    if (!dashboard) {
      clearDashboardViewer();
      return;
    }

    currentDashboardId = dashboard.id;
    updateDashboardState({ lastDashboardId: Number(dashboard.id) });
    $('#dashboardSelector').val(String(dashboard.id));
    $('#selectedDashboardName').text(dashboard.name || 'Dashboard');
    if (dashboard.is_shared) {
      $('#selectedDashboardSharedBadge').removeClass('d-none');
    } else {
      $('#selectedDashboardSharedBadge').addClass('d-none');
    }
    setViewerStatus('Idle', false);
    $('#dashboardViewerEmptyState').removeClass('d-none').text('Loading dashboard data...');
    $('#dashboardWidgets').empty();

    const range = getSelectedTimeframe({ allowPartial: true });
    updateRangeSummary(range ? range.start : null, range ? range.end : null);
    updateDashboardActionsState();

    refreshSelectedDashboard();
  }

  function renderDashboardSelector(dashboards) {
    const selector = $('#dashboardSelector');
    selector.empty();
    selector.append('<option value="">Select a dashboard</option>');

    dashboards.forEach((dashboard) => {
      const optionText = dashboard.is_shared ? `${dashboard.name} (Shared)` : dashboard.name;
      const option = $('<option></option>').val(dashboard.id).text(optionText);
      if (dashboard.description) {
        option.attr('title', dashboard.description);
      }
      selector.append(option);
    });

    if (!dashboards.length) {
      $('#dashboardSelectorEmptyState').removeClass('d-none');
      selector.prop('disabled', true);
    } else {
      $('#dashboardSelectorEmptyState').addClass('d-none');
      selector.prop('disabled', false);
      const exists = dashboards.some((dashboard) => Number(dashboard.id) === Number(currentDashboardId));
      if (exists) {
        selector.val(String(currentDashboardId));
      } else {
        selector.val('');
      }
    }

    updateDashboardActionsState();
  }

  function loadDashboards(autoSelect = true) {
    $.getJSON(buildApiUrl(apiBase))
      .done((response) => {
        if (response.status !== 'success') {
          notify_error(response.message || 'Unable to load dashboards.');
          return;
        }
        dashboardsCache = response.data || [];
        renderDashboardSelector(dashboardsCache);

        if (!dashboardsCache.length) {
          clearDashboardViewer();
          $('#dashboardViewerEmptyState').text('Create your first dashboard to get started.');
          return;
        }

        if (currentDashboardId) {
          const exists = dashboardsCache.some((item) => Number(item.id) === Number(currentDashboardId));
          if (exists) {
            selectDashboard(currentDashboardId);
            return;
          }
        }

        const storedDashboardId = dashboardState && dashboardState.lastDashboardId !== undefined && dashboardState.lastDashboardId !== null
          ? Number(dashboardState.lastDashboardId)
          : null;
        if (storedDashboardId) {
          const exists = dashboardsCache.some((item) => Number(item.id) === storedDashboardId);
          if (exists) {
            selectDashboard(storedDashboardId);
            return;
          }
        }

        if (autoSelect) {
          selectDashboard(dashboardsCache[0].id);
        } else {
          clearDashboardViewer();
        }
      })
      .fail((jqXHR) => {
        ajax_notify_error(jqXHR, apiBase);
      });
  }

  function resetEditor() {
    $('#dashboardEditorForm')[0].reset();
    $('#dashboardId').val('');
    $('#widgetList').empty();
    $('#dashboardIsShared').prop('disabled', !canShareDashboards);
    if (!canShareDashboards) {
      $('#dashboardIsShared').prop('checked', false);
    }
  }

  function addWidgetRow(widget) {
    const widgetId = `widget-${Date.now()}-${Math.random()}`;
    const row = $(`
      <div class="card mb-2 widget-row" data-widget-id="${widgetId}">
        <div class="card-body">
          <div class="form-row">
            <div class="form-group col-md-6">
              <label>Name</label>
              <input type="text" class="form-control widget-name" value="${widget && widget.name ? widget.name : ''}" required>
            </div>
            <div class="form-group col-md-4">
              <label>Visualization</label>
              <select class="form-control widget-chart" required>
                <option value="line">Line</option>
                <option value="bar">Bar</option>
                <option value="pie">Pie</option>
                <option value="number">Number</option>
                <option value="percentage">Percentage</option>
                <option value="table">Table</option>
              </select>
            </div>
            <div class="form-group col-md-2">
              <label>&nbsp;</label>
              <button type="button" class="btn btn-outline-danger btn-block remove-widget">Remove</button>
            </div>
          </div>
          <div class="form-group">
            <label>Configuration (JSON)</label>
            <textarea class="form-control widget-definition" rows="4" placeholder='{"fields": [...]}'>${widget ? JSON.stringify(widget, null, 2) : ''}</textarea>
          </div>
        </div>
      </div>
    `);

    row.find('.widget-chart').val(widget && widget.chart_type ? widget.chart_type : 'line');
    row.find('.remove-widget').on('click', function () {
      row.remove();
    });

    $('#widgetList').append(row);
  }

  function openEditor(dashboard) {
    resetEditor();
    if (dashboard) {
      $('#dashboardId').val(dashboard.id);
      $('#dashboardName').val(dashboard.name);
      $('#dashboardDescription').val(dashboard.description || '');
      $('#dashboardIsShared').prop('disabled', !canShareDashboards);
      $('#dashboardIsShared').prop('checked', canShareDashboards ? !!dashboard.is_shared : false);
      $('#dashboardEditorTitle').text('Edit dashboard');
      $('#dashboardDeleteBtn').show();
      (dashboard.widgets || []).forEach((widget) => {
        addWidgetRow(widget);
      });
    } else {
      $('#dashboardEditorTitle').text('New dashboard');
      $('#dashboardDeleteBtn').hide();
      $('#dashboardIsShared').prop('disabled', !canShareDashboards);
    }

    $('#dashboardEditorModal').modal('show');
  }

  function collectDashboardPayload() {
    const widgets = [];
    let hasError = false;

    $('.widget-row').each(function () {
      const row = $(this);
      const definitionText = row.find('.widget-definition').val();
      row.find('.widget-definition').removeClass('is-invalid');

      try {
        const definition = JSON.parse(definitionText || '{}');
        definition.name = row.find('.widget-name').val();
        definition.chart_type = row.find('.widget-chart').val();
        widgets.push(definition);
      } catch (e) {
        hasError = true;
        row.find('.widget-definition').addClass('is-invalid');
        notify_error(`Widget configuration is not valid JSON: ${e.message}`);
      }
    });

    if (hasError) {
      return null;
    }

    return {
      name: $('#dashboardName').val(),
      description: $('#dashboardDescription').val(),
      is_shared: canShareDashboards ? $('#dashboardIsShared').is(':checked') : false,
      widgets: widgets
    };
  }

  function saveDashboard() {
    const payload = collectDashboardPayload();
    if (!payload) {
      return;
    }

    const dashboardId = $('#dashboardId').val();
    const method = dashboardId ? 'PUT' : 'POST';
    const url = dashboardId ? buildApiUrl(`${apiBase}/${dashboardId}`) : buildApiUrl(apiBase);

    if (method === 'POST') {
      payload.csrf_token = $('#csrf_token').val();
    }

    if (!canShareDashboards) {
      payload.is_shared = false;
    }

    $.ajax({
      url: url,
      method: method,
      contentType: 'application/json',
      data: JSON.stringify(payload)
    }).done(function (response) {
      $('#dashboardEditorModal').modal('hide');
      if (response && response.data && response.data.id) {
        currentDashboardId = response.data.id;
      }
      loadDashboards();
    }).fail(function (jqXHR) {
      if (jqXHR.responseJSON && jqXHR.responseJSON.errors) {
        notify_error('Validation failed while saving the dashboard.');
      } else if (jqXHR.responseJSON && jqXHR.responseJSON.message) {
        notify_error(jqXHR.responseJSON.message);
      } else {
        ajax_notify_error(jqXHR, url);
      }
    });
  }

  function deleteDashboard(dashboardIdOverride) {
    const dashboardIdValue = dashboardIdOverride || $('#dashboardId').val();
    if (!dashboardIdValue) {
      return;
    }

    if (!confirm('Delete this dashboard? This action cannot be undone.')) {
      return;
    }

    const url = buildApiUrl(`${apiBase}/${dashboardIdValue}`);

    $.ajax({
      url: url,
      method: 'DELETE'
    }).done(function () {
      $('#dashboardEditorModal').modal('hide');
      if (Number(dashboardIdValue) === Number(currentDashboardId)) {
        currentDashboardId = null;
      }
      loadDashboards();
    }).fail(function (jqXHR) {
      if (jqXHR.responseJSON && jqXHR.responseJSON.message) {
        notify_error(jqXHR.responseJSON.message);
      } else {
        ajax_notify_error(jqXHR, url);
      }
    });
  }

  function bindEvents() {
    $('#createDashboardBtn').on('click', function () {
      openEditor();
    });

    $('#dashboardSelector').on('change', function () {
      const selectedId = $(this).val();
      if (selectedId) {
        selectDashboard(Number(selectedId));
      } else {
        clearDashboardViewer();
      }
    });

    $('#dashboardHelpBtn').on('click', function () {
      $('#dashboardHelpModal').modal('show');
    });

    $('#dashboardAutoRefresh').on('change', function () {
      const enabled = $(this).is(':checked');
      setAutoRefresh(enabled);
    });

    $('#addWidgetBtn').on('click', function () {
      addWidgetRow();
    });

    $('#dashboardSaveBtn').on('click', function () {
      saveDashboard();
    });

    $('#dashboardDeleteBtn').on('click', function () {
      deleteDashboard();
    });

    $('#editDashboardBtn').on('click', function () {
      if (!currentDashboardId) {
        return;
      }
      const dashboard = dashboardsCache.find((item) => Number(item.id) === Number(currentDashboardId));
      if (dashboard) {
        openEditor(dashboard);
      }
    });

    $('#removeDashboardBtn').on('click', function () {
      if (!currentDashboardId) {
        return;
      }
      deleteDashboard(currentDashboardId);
    });

    $('#dashboardFilterForm').on('submit', function (event) {
      event.preventDefault();
      clearFilterError();
      const timeframe = getSelectedTimeframe();
      if (!timeframe) {
        return;
      }
      currentQuickRangeKey = null;
      updateQuickRangeButtons();
      updateRangeSummary(timeframe.start, timeframe.end);
      refreshSelectedDashboard();
    });

    $('#dashboardResetBtn').on('click', function () {
      setDefaultTimeframe();
      refreshSelectedDashboard();
    });

    $('.dashboard-quick-range').on('click', function () {
      const button = $(this);
      const now = new Date();
      const start = new Date(now);

      if (button.data('minutes')) {
        start.setMinutes(start.getMinutes() - Number(button.data('minutes')));
      }
      if (button.data('hours')) {
        start.setHours(start.getHours() - Number(button.data('hours')));
      }
      if (button.data('days')) {
        start.setDate(start.getDate() - Number(button.data('days')));
      }
      if (button.data('months')) {
        start.setMonth(start.getMonth() - Number(button.data('months')));
      }
      if (button.data('years')) {
        start.setFullYear(start.getFullYear() - Number(button.data('years')));
      }

      $('#dashboardStart').val(formatDateForInput(start));
      $('#dashboardEnd').val(formatDateForInput(now));

      currentQuickRangeKey = button.data('range-key');
      updateQuickRangeButtons();
      updateRangeSummary(start, now);
      clearFilterError();
      refreshSelectedDashboard();
    });
  }

  $(document).ready(function () {
    setDefaultTimeframe();
    bindEvents();
    updateDashboardActionsState();
    const autoRefreshEnabled = dashboardState && dashboardState.autoRefresh === true;
    setAutoRefresh(autoRefreshEnabled, { skipImmediate: true, silent: true });
    loadDashboards();
  });
})(jQuery);
